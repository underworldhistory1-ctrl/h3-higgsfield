"""H3 Studio: serves the video page and keeps generated clips until deleted.

Generated videos are indexed from ComfyUI output on every library refresh.
Temporary uploaded reference files are discarded after a render or when stale.
Downloaded clips remain local to the user's browser.

Wipe limits, stated plainly: overwriting in place defeats undelete and
file-carving at the filesystem level, but on an SSD the flash translation layer
does wear-levelling, so the original NAND pages are not necessarily the ones
rewritten. Only full-disk encryption, ATA secure erase, or writing to a
RAM-backed filesystem gives a hard guarantee.
"""

import asyncio
import json
import math
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
import uuid

from aiohttp import web

import folder_paths
from server import PromptServer
from .h3_video_save import H3SaveVideo
from .qwen_image import (
    NODE_CLASS_MAPPINGS as QWEN_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as QWEN_NODE_DISPLAY_NAME_MAPPINGS,
    delete_image_output,
    profile_status as qwen_profile_status,
    required_qwen_nodes,
    safe_image_path,
    scan_image_library,
    update_image_details,
)


NODE_CLASS_MAPPINGS = {"H3SaveVideo": H3SaveVideo}
NODE_DISPLAY_NAME_MAPPINGS = {"H3SaveVideo": "H3 Save Video"}
NODE_CLASS_MAPPINGS.update(QWEN_NODE_CLASS_MAPPINGS)
NODE_DISPLAY_NAME_MAPPINGS.update(QWEN_NODE_DISPLAY_NAME_MAPPINGS)
WEB_DIRECTORY = "./web"

PREFIX = "h3_studio"          # only files starting with this are ever touched
INPUT_PREFIX = "h3_studio_kf"  # uploaded frames/references, same disposable treatment
INPUT_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm", ".wav", ".mp3", ".flac", ".m4a")
SUBFOLDER = "video"
STALE_AFTER = 6 * 60 * 60     # wipe unclaimed uploaded inputs after 6h
SWEEP_EVERY = 600
# A reload fires the same teardown as a close, and the browser cannot tell you
# which happened. So a departing page only *schedules* its wipe; a page that
# comes back within the grace period cancels it. A real close never comes back,
# so the wipe lands a minute later instead of instantly.
SESSION_GRACE = 60
TICK = 15                     # how often pending wipes are checked

# filename -> {"kept": bool, "ts": float}
_tracked = {}
# session id -> {"deadline": float, "files": [str]}
_pending = {}
_resumed = {}
_lock = threading.Lock()
_probe_cache = {}
_metadata = {}
_registered_jobs = {}


def _video_dir():
    return os.path.join(folder_paths.get_output_directory(), SUBFOLDER)


def _thumbnail_dir():
    return os.path.join(_video_dir(), ".h3-thumbnails")


def _image_dir():
    return os.path.join(folder_paths.get_output_directory(), "images")


def _video_metadata(path):
    """Cache clip duration by file identity; never let ffprobe block aiohttp."""
    stat = os.stat(path)
    key = (stat.st_mtime_ns, stat.st_size)
    with _lock:
        cached = _probe_cache.get(path)
    if cached and cached[0] == key:
        return cached[1]
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=8, check=True)
        duration = round(float(result.stdout.strip()), 2)
    except (OSError, ValueError, subprocess.SubprocessError):
        duration = None
    with _lock:
        _probe_cache[path] = (key, duration)
    return duration


def _kept_registry():
    return os.path.join(_video_dir(), ".h3-studio-kept.json")


def _metadata_registry():
    return os.path.join(_video_dir(), ".h3-studio-metadata.json")


def _job_registry():
    return os.path.join(_video_dir(), ".h3-studio-jobs.json")


def _load_jobs():
    try:
        with open(_job_registry(), "r", encoding="utf-8") as stream:
            source = json.load(stream)
        if not isinstance(source, dict):
            return {}
        return {token: item for token, item in source.items()
                if re.fullmatch(r"[0-9a-f]{12}", token) and isinstance(item, dict)}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save_jobs_locked():
    os.makedirs(_video_dir(), exist_ok=True)
    target = _job_registry()
    scratch = target + "." + uuid.uuid4().hex + ".tmp"
    try:
        with open(scratch, "w", encoding="utf-8") as stream:
            json.dump(_registered_jobs, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(scratch, target)
    finally:
        if os.path.exists(scratch):
            os.unlink(scratch)


def _load_metadata():
    try:
        with open(_metadata_registry(), "r", encoding="utf-8") as stream:
            source = json.load(stream)
        if not isinstance(source, dict):
            return {}
        return {name: item for name, item in source.items()
                if isinstance(item, dict) and _safe_path(name)
                and os.path.isfile(_safe_path(name))}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save_metadata_locked():
    os.makedirs(_video_dir(), exist_ok=True)
    target = _metadata_registry()
    scratch = target + "." + uuid.uuid4().hex + ".tmp"
    try:
        with open(scratch, "w", encoding="utf-8") as stream:
            json.dump(_metadata, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(scratch, target)
    finally:
        if os.path.exists(scratch):
            os.unlink(scratch)


def _clean_settings(value):
    if not isinstance(value, dict):
        return None
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 24000:
            return None
        return json.loads(encoded)
    except (TypeError, ValueError, OverflowError):
        return None


def _load_kept():
    try:
        with open(_kept_registry(), "r", encoding="utf-8") as stream:
            names = json.load(stream)
        if not isinstance(names, list):
            return []
        return [name for name in names if isinstance(name, str)
                and _safe_path(name) and os.path.isfile(_safe_path(name))]
    except (FileNotFoundError, ValueError, OSError):
        return []


def _save_kept_locked():
    """Atomic registry write; caller holds _lock."""
    os.makedirs(_video_dir(), exist_ok=True)
    target = _kept_registry()
    scratch = target + "." + uuid.uuid4().hex + ".tmp"
    names = sorted(n for n, item in _tracked.items()
                   if item.get("kept") and _safe_path(n)
                   and os.path.isfile(_safe_path(n)))
    try:
        with open(scratch, "w", encoding="utf-8") as stream:
            json.dump(names, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(scratch, target)
    finally:
        if os.path.exists(scratch):
            os.unlink(scratch)


def _scoped(filename, base_dir, prefix, exts):
    """Resolve a caller-supplied name, or None if it escapes our own files."""
    if not filename or os.path.basename(filename) != filename:
        return None
    if not filename.startswith(prefix) or not filename.lower().endswith(exts):
        return None
    base = os.path.realpath(base_dir)
    path = os.path.realpath(os.path.join(base, filename))
    if os.path.dirname(path) != base:
        return None
    return path


def _safe_path(filename):
    return _scoped(filename, _video_dir(), PREFIX, (".mp4",))


def _safe_input_path(filename):
    """References we uploaded ourselves, in ComfyUI's input directory.

    Scoped the same way as outputs: basename only, our own prefix, approved
    media extensions, and the resolved path must sit directly in input/.
    """
    return _scoped(filename, folder_paths.get_input_directory(),
                   INPUT_PREFIX, INPUT_EXTS)


async def _normalize_reference_video(source, target, trim_start=0.0, trim_duration=None, fit=False):
    """Convert, optionally trim and fit a reference without changing playback speed."""
    filters = []
    if fit:
        filters.extend(("scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease",
                        "scale=trunc(iw/2)*2:trunc(ih/2)*2"))
    filters.append("fps=24")
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", source,
        *(["-ss", str(trim_start)] if trim_start else []),
        *(["-t", str(trim_duration)] if trim_duration is not None else []),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", ",".join(filters), "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-b:a", "192k", "-movflags", "+faststart", target,
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE)
    except OSError as exc:
        raise ValueError("The server cannot convert this reference video to 24 fps.") from exc
    try:
        _, errors = await asyncio.wait_for(process.communicate(), timeout=180)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ValueError("Converting this reference video to 24 fps took too long.") from exc
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        detail = errors.decode("utf-8", "replace").strip().splitlines()
        raise ValueError("Could not convert the reference video to 24 fps: "
                         + (detail[-1][:120] if detail else "ffmpeg failed"))


@PromptServer.instance.routes.post("/h3_studio/upload_ref")
async def upload_ref(request):
    """Stream a scoped reference and inspect its actual media before using it."""
    kinds = {
        "image": {".png", ".jpg", ".jpeg", ".webp"},
        "video": {".mp4", ".mov", ".webm"},
        "audio": {".wav", ".mp3", ".flac", ".m4a"},
    }
    limits = {"image": 25 << 20, "video": 500 << 20, "audio": 100 << 20}
    kind = request.query.get("kind", "")
    if kind not in kinds:
        return web.json_response({"error": "Invalid reference type."}, status=400)
    try:
        trim_start = float(request.query.get("trim_start", "0"))
        raw_duration = request.query.get("trim_duration")
        trim_duration = float(raw_duration) if raw_duration is not None else None
        resize = request.query.get("resize", "0") == "1"
        if not math.isfinite(trim_start) or not 0 <= trim_start <= 3600:
            raise ValueError("Invalid trim start.")
        if trim_duration is not None and (not math.isfinite(trim_duration) or not 2 <= trim_duration <= 15):
            raise ValueError("Trim length must be 2–15 seconds.")
        if kind != "video" and (trim_start or trim_duration is not None):
            raise ValueError("Trimming is available for video references only.")
    except (TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    reader = await request.multipart()
    part = await reader.next()
    if not part or part.name != "file":
        return web.json_response({"error": "Missing file."}, status=400)
    ext = pathlib.Path(part.filename or "").suffix.lower()
    if ext not in kinds[kind]:
        return web.json_response({"error": "Unsupported file extension for this type."}, status=400)
    name = f"{INPUT_PREFIX}_{uuid.uuid4().hex}{ext}"
    path = _safe_input_path(name)
    normalized_path = None
    size = 0
    try:
        os.makedirs(folder_paths.get_input_directory(), exist_ok=True)
        with open(path, "xb") as output:
            while True:
                chunk = await part.read_chunk(size=1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > limits[kind]:
                    raise ValueError("Reference file is too large.")
                output.write(chunk)
        if not size:
            raise ValueError("Reference file is empty.")
        info = {"kind": kind, "size": size}
        if kind == "image":
            from PIL import Image, ImageOps
            with Image.open(path) as picture:
                if picture.width * picture.height > 50_000_000 and not resize:
                    raise ValueError("Reference image is too large (50 megapixel limit).")
                picture.verify()
            if resize:
                with Image.open(path) as original:
                    if original.width * original.height > 100_000_000:
                        raise ValueError("Reference image exceeds the safe decode limit.")
                    picture = ImageOps.exif_transpose(original)
                    changed = max(picture.size) > 2048
                    if changed:
                        picture.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                        if ext in {".jpg", ".jpeg"} and picture.mode not in {"RGB", "L"}:
                            picture = picture.convert("RGB")
                        picture.save(path)
                    info.update(width=picture.width, height=picture.height, resized=changed)
        else:
            import av
            with av.open(path) as media:
                if kind == "video":
                    if not media.streams.video:
                        raise ValueError("This file has no video stream.")
                    stream = media.streams.video[0]
                    fps = float(stream.average_rate or 0)
                    duration = float(stream.duration * stream.time_base) if stream.duration else float(media.duration or 0) / 1000000
                    if not duration and stream.frames and fps:
                        duration = stream.frames / fps
                    if not duration and fps:
                        duration = sum(1 for _ in media.decode(stream)) / fps
                    too_large = stream.width > 1920 or stream.height > 1080
                    if too_large and not resize:
                        raise ValueError("Reference video is too large; enable resize to fit 1920x1080.")
                    if trim_duration is not None:
                        if duration and trim_start + trim_duration > duration + .1:
                            raise ValueError("Trim range extends beyond the source video.")
                    elif not 1.9 <= duration <= 15.1:
                        raise ValueError("H3 reference videos must be 2–15 seconds long. Select a 2–15 second trim.")
                    info.update(duration=round(duration, 2), fps=round(fps, 2), has_audio=bool(media.streams.audio),
                                source_duration=round(duration, 2), source_width=stream.width, source_height=stream.height)
                else:
                    if not media.streams.audio:
                        raise ValueError("This file has no audio stream.")
                    stream = media.streams.audio[0]
                    duration = float(stream.duration * stream.time_base) if stream.duration else float(media.duration or 0) / 1000000
                    if not duration and stream.codec_context.sample_rate:
                        duration = sum(frame.samples for frame in media.decode(stream)) / stream.codec_context.sample_rate
                    if not 1.9 <= duration <= 15.1:
                        raise ValueError("H3 reference audio must be 2–15 seconds long.")
                    info["duration"] = round(duration, 2)
        if kind == "video" and (abs(info["fps"] - 24) > 0.02 or trim_duration is not None or trim_start or
                                resize and (info["source_width"] > 1920 or info["source_height"] > 1080)):
            original_fps = info["fps"]
            normalized_name = f"{INPUT_PREFIX}_{uuid.uuid4().hex}.mp4"
            normalized_path = _safe_input_path(normalized_name)
            await _normalize_reference_video(path, normalized_path, trim_start, trim_duration,
                                             resize and (info["source_width"] > 1920 or info["source_height"] > 1080))
            if os.path.getsize(normalized_path) > limits["video"]:
                raise ValueError("The 24 fps reference video is too large.")
            with av.open(normalized_path) as normalized:
                stream = normalized.streams.video[0]
                fps = float(stream.average_rate or 0)
                duration = (float(stream.duration * stream.time_base)
                            if stream.duration else float(normalized.duration or 0) / 1000000)
                if abs(fps - 24) > 0.02 or not 1.9 <= duration <= 15.1 or stream.width > 1920 or stream.height > 1080:
                    raise ValueError("The converted video did not meet H3's 24 fps and duration limits.")
                info.update(duration=round(duration, 2), fps=round(fps, 2),
                            has_audio=bool(normalized.streams.audio),
                            normalized_from_fps=original_fps if abs(original_fps - 24) > .02 else None,
                            size=os.path.getsize(normalized_path),
                            resized=stream.width != info["source_width"] or stream.height != info["source_height"],
                            trim_start=trim_start, trimmed=trim_duration is not None)
            os.unlink(path)
            path, name = normalized_path, normalized_name
            normalized_path = None
        with _lock:
            _tracked[name] = {"kept": False, "ts": time.time()}
        return web.json_response({"name": name, **info})
    except asyncio.CancelledError:
        if os.path.exists(path):
            os.unlink(path)
        if normalized_path and os.path.exists(normalized_path):
            os.unlink(normalized_path)
        raise
    except Exception as exc:
        if os.path.exists(path):
            os.unlink(path)
        if normalized_path and os.path.exists(normalized_path):
            os.unlink(normalized_path)
        return web.json_response({"error": str(exc)[:200]}, status=400)


def _resolve(filename):
    """A tracked file is either a generated clip or a reference we uploaded."""
    # Input MP4 names also start with PREFIX. Check the narrower input prefix
    # first, or /discard looks for an uploaded reference in output/video.
    return _safe_input_path(filename) or _safe_path(filename)


def secure_wipe(path, passes=3):
    """Overwrite, rename, then unlink. See module docstring for SSD caveats."""
    try:
        size = os.path.getsize(path)
        with open(path, "r+b", buffering=0) as f:
            for p in range(passes):
                f.seek(0)
                left = size
                while left > 0:
                    chunk = min(left, 1 << 20)
                    # final pass writes zeros so the tail is not random noise
                    f.write(b"\0" * chunk if p == passes - 1 else os.urandom(chunk))
                    left -= chunk
                f.flush()
                os.fsync(f.fileno())
            f.truncate(0)
            f.flush()
            os.fsync(f.fileno())
        # rename first so the old name does not linger in the directory entry
        scratch = os.path.join(os.path.dirname(path), uuid.uuid4().hex + ".tmp")
        os.replace(path, scratch)
        os.unlink(scratch)
        dir_fd = os.open(os.path.dirname(path), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return True
    except FileNotFoundError:
        return False
    except Exception as e:
        print(f"[h3_studio] wipe failed for {os.path.basename(path)}: {e!r}")
        return False


def _discard(filename):
    path = _resolve(filename)
    if not path:
        return False
    wiped = secure_wipe(path)
    if wiped:
        if _safe_path(filename):
            with _lock:
                _probe_cache.pop(path, None)
            try:
                os.unlink(os.path.join(_thumbnail_dir(), filename[:-4] + ".jpg"))
            except OSError:
                pass
        with _lock:
            old = _tracked.pop(filename, None)
            if old and old.get("kept"):
                _save_kept_locked()
            if _metadata.pop(filename, None) is not None:
                _save_metadata_locked()
    if wiped:
        print(f"[h3_studio] wiped {filename}")
    return wiped


@PromptServer.instance.routes.post("/h3_studio/register_job")
async def register_job(request):
    """Keep a submitted job's settings even if its browser closes mid-render."""
    data = await request.json()
    token = data.get("token")
    settings = _clean_settings(data.get("settings"))
    units = data.get("units")
    sample_key = data.get("sample_key")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{12}", token) \
            or settings is None or not isinstance(units, (int, float)) \
            or isinstance(units, bool) or not math.isfinite(units) or not 0 < units < 10000 \
            or not isinstance(sample_key, str) or len(sample_key) > 600:
        return web.json_response({"error": "rejected"}, status=400)
    with _lock:
        _registered_jobs[token] = {"settings": settings, "units": round(units, 6),
                                   "sample_key": sample_key, "submitted": time.time()}
        _save_jobs_locked()
    return web.json_response({"ok": True})


@PromptServer.instance.routes.post("/h3_studio/track")
async def track(request):
    """Register a freshly generated clip as unsaved."""
    data = await request.json()
    name = data.get("filename")
    path = _resolve(name)
    if not path or not os.path.isfile(path):
        return web.json_response({"error": "rejected"}, status=400)
    render_seconds = data.get("render_seconds")
    units = data.get("units")
    sample_key = data.get("sample_key")
    settings = _clean_settings(data.get("settings"))
    with _lock:
        _tracked[name] = {"kept": _tracked.get(name, {}).get("kept", False), "ts": time.time()}
        item = dict(_metadata.get(name, {}))
        if isinstance(render_seconds, (int, float)) and not isinstance(render_seconds, bool) \
                and math.isfinite(render_seconds) and 0 < render_seconds < 86400:
            item["render_seconds"] = round(render_seconds, 2)
            if isinstance(units, (int, float)) and not isinstance(units, bool) \
                    and math.isfinite(units) and 0 < units < 10000 \
                    and isinstance(sample_key, str) and len(sample_key) <= 600:
                item.update(units=round(units, 6), sample_key=sample_key)
        if settings is not None:
            item["settings"] = settings
        if item:
            _metadata[name] = item
            _save_metadata_locked()
    return web.json_response({"ok": True})


@PromptServer.instance.routes.post("/h3_studio/details")
async def store_details(request):
    """Backfill settings for an existing H3 video without altering the clip."""
    data = await request.json()
    name = data.get("filename")
    settings = _clean_settings(data.get("settings"))
    if not _safe_path(name) or not os.path.isfile(_safe_path(name)) or settings is None:
        return web.json_response({"error": "rejected"}, status=400)
    with _lock:
        _metadata.setdefault(name, {})["settings"] = settings
        _save_metadata_locked()
    return web.json_response({"ok": True})


@PromptServer.instance.routes.post("/h3_studio/keep")
async def keep(request):
    """Mark a clip as saved so it survives."""
    data = await request.json()
    name = data.get("filename")
    path = _safe_path(name)
    if not path or not os.path.isfile(path):
        return web.json_response({"error": "rejected"}, status=400)
    with _lock:
        _tracked.setdefault(name, {"ts": time.time()})["kept"] = True
        _save_kept_locked()
    print(f"[h3_studio] keeping {name}")
    return web.json_response({"ok": True})


@PromptServer.instance.routes.get("/h3_studio/library")
async def library(request):
    """List every H3 Studio clip still present, across tabs and restarts."""
    directory = _video_dir()
    if not os.path.isdir(directory):
        return web.json_response({"items": []})
    names = [name for name in os.listdir(directory)
             if _safe_path(name) and os.path.isfile(_safe_path(name))]
    with _lock:
        changed = False
        for name in names:
            match = re.match(r"h3_studio_([0-9a-f]{12})_", name)
            token = match.group(1) if match else None
            job = _registered_jobs.pop(token, None) if token else None
            if job:
                item = _metadata.setdefault(name, {})
                item.setdefault("settings", job["settings"])
                item.setdefault("units", job["units"])
                item.setdefault("sample_key", job["sample_key"])
                changed = True
        if changed:
            _save_metadata_locked()
            _save_jobs_locked()
        pinned = {n for n, item in _tracked.items() if item.get("kept")}
        metrics = dict(_metadata)
    items = []
    for name in names:
        path = _safe_path(name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        items.append({"filename": name, "subfolder": SUBFOLDER, "type": "output",
                      "bytes": stat.st_size, "modified": stat.st_mtime,
                      "kept": name in pinned,
                      "render_seconds": metrics.get(name, {}).get("render_seconds"),
                      "units": metrics.get(name, {}).get("units"),
                      "sample_key": metrics.get(name, {}).get("sample_key"),
                      "settings": metrics.get(name, {}).get("settings")})
    items.sort(key=lambda item: item["modified"], reverse=True)
    durations = await asyncio.gather(*(asyncio.to_thread(_video_metadata, _safe_path(item["filename"]))
                                       for item in items), return_exceptions=True)
    for item, duration in zip(items, durations):
        item["duration"] = duration if isinstance(duration, (int, float)) else None
    return web.json_response({"items": items})


def _make_thumbnail(path, target):
    source = os.stat(path)
    if os.path.isfile(target) and os.path.getmtime(target) >= source.st_mtime:
        return True
    os.makedirs(os.path.dirname(target), exist_ok=True)
    temporary = target + "." + uuid.uuid4().hex + ".tmp.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", "1", "-i", path, "-frames:v", "1", "-vf", "scale=320:-2",
             "-q:v", "4", temporary],
            capture_output=True, timeout=25, check=True)
        if not os.path.isfile(temporary) or os.path.getsize(temporary) == 0:
            return False
        os.replace(temporary, target)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@PromptServer.instance.routes.get("/h3_studio/thumbnail")
async def thumbnail(request):
    name = request.query.get("filename", "")
    path = _safe_path(name)
    if not path or not os.path.isfile(path):
        return web.Response(status=404)
    target = os.path.join(_thumbnail_dir(), name[:-4] + ".jpg")
    if not await asyncio.to_thread(_make_thumbnail, path, target):
        return web.Response(status=404)
    return web.FileResponse(target, headers={"Cache-Control": "private, max-age=3600"})


@PromptServer.instance.routes.get("/h3_studio/loras")
async def loras(request):
    """Installed ComfyUI LoRAs; listing a file does not prove H3 compatibility."""
    try:
        names = folder_paths.get_filename_list("loras")
    except (KeyError, OSError):
        names = []
    return web.json_response({"items": names})


@PromptServer.instance.routes.post("/h3_studio/verify_video")
async def verify_video(request):
    """Check the final MP4 has decodable, non-silent audio and visible frames."""
    try:
        data = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "bad request"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"error": "bad request"}, status=400)
    name = data.get("filename")
    path = _safe_path(name)
    if not path or not os.path.isfile(path):
        return web.json_response({"error": "video not found"}, status=404)
    script = pathlib.Path(__file__).parent / "scripts" / "verify_h3_video.py"
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(script), path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=90)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return web.json_response({"ok": False, "message": "Media check timed out"})
    except OSError as exc:
        return web.json_response({"ok": False, "message": str(exc)[:200]})
    message = (stdout if process.returncode == 0 else stderr).decode("utf-8", "replace").strip()
    return web.json_response({"ok": process.returncode == 0, "message": message[:500]})


@PromptServer.instance.routes.get("/h3_studio/readiness")
async def readiness(request):
    required = {
        "fl2va": ("diffusion_models", "minimax_h3_fl2va_pruned_int8_convrot.safetensors", 20970379616),
        "ref2va": ("diffusion_models", "minimax_h3_ref2va_pruned_int8_convrot.safetensors", 20970379616),
        "text_encoder": ("text_encoders", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", 15687142551),
        "video_vae": ("vae", "minimax_h3_video_vae_fp16.safetensors", 5207808496),
        "audio_vae": ("vae", "minimax_h3_audio_vae_fp32.safetensors", 605254808),
    }
    present = {}
    for key, (category, filename, expected_size) in required.items():
        path = folder_paths.get_full_path(category, filename)
        try:
            present[key] = bool(path and os.path.isfile(path) and os.path.getsize(path) == expected_size)
        except OSError:
            present[key] = False
    import nodes
    node_names = (
        "UNETLoader", "MiniMaxH3SigmaShift", "CLIPLoader", "VAELoader",
        "MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo",
        "ConditioningZeroOut", "KSampler", "VAEDecode", "VAEDecodeAudio",
        "CreateVideo", "H3SaveVideo", "SaveVideo", "LoadImage", "LoadVideo",
        "GetVideoComponents", "LoadAudio", "LoraLoaderModelOnly",
        "SpectrumApplyMiniMaxH3", "MiniMaxH3MotionCache",
    )
    available = {name: name in nodes.NODE_CLASS_MAPPINGS for name in node_names}
    vae_source = pathlib.Path(folder_paths.__file__).resolve().parent / "comfy" / "ldm" / "minimax" / "vae.py"
    try:
        vae_tile_fix = "strip[..., :, x_idx[j]:x_idx[j] + x_len[j]]" in vae_source.read_text(encoding="utf-8")
    except OSError:
        vae_tile_fix = False
    ram_limit = None
    try:
        raw = pathlib.Path("/sys/fs/cgroup/memory.max").read_text(encoding="ascii").strip()
        if raw.isdigit():
            ram_limit = round(int(raw) / (1024 ** 3), 2)
    except OSError:
        pass
    return web.json_response({"models": present, "nodes": available,
                              "quality": {"h3_vae_tile_fix": vae_tile_fix},
                              "system_ram_limit_gb": ram_limit})


@PromptServer.instance.routes.get("/h3_studio/job_progress")
async def job_progress(request):
    """Return the active sampler step so a refreshed Studio can resume its display."""
    prompt_id = request.query.get("prompt_id", "")
    if not prompt_id or len(prompt_id) > 128:
        return web.json_response({"error": "invalid prompt_id"}, status=400)
    from comfy_execution.progress import get_progress_state
    registry = get_progress_state()
    if registry.prompt_id != prompt_id:
        return web.json_response({"prompt_id": prompt_id, "step": None, "total": None})
    sampler = registry.nodes.get("8") or registry.nodes.get("sampler")
    if not sampler:
        return web.json_response({"prompt_id": prompt_id, "step": None, "total": None})
    return web.json_response({
        "prompt_id": prompt_id,
        "step": sampler["value"],
        "total": sampler["max"],
        "state": sampler["state"].value,
    })


@PromptServer.instance.routes.get("/h3_studio/image_readiness")
async def image_readiness(request):
    """Report Qwen profiles independently; a partial profile is never selectable."""
    try:
        # models_dir may be a symlink to a separately mounted model dataset.
        # Resolve the ComfyUI checkout itself, or profile checks will look for
        # /input0/models instead of the actual ComfyUI/models symlink.
        comfy_root = pathlib.Path(folder_paths.base_path).resolve()
    except (AttributeError, OSError):
        comfy_root = pathlib.Path(folder_paths.__file__).resolve().parent
    profiles = qwen_profile_status(comfy_root)
    import nodes
    available = {name: name in nodes.NODE_CLASS_MAPPINGS for name in required_qwen_nodes()}
    return web.json_response({
        "profiles": profiles,
        "nodes": available,
        "ready": any(item["ready"] for item in profiles.values()) and all(available.values()),
    })


@PromptServer.instance.routes.get("/h3_studio/image_library")
async def image_library(request):
    items = await asyncio.to_thread(scan_image_library, _image_dir())
    return web.json_response({"items": items})


@PromptServer.instance.routes.get("/h3_studio/image_file")
async def image_file(request):
    path = safe_image_path(_image_dir(), request.query.get("filename", ""))
    if path is None or not path.is_file():
        return web.Response(status=404)
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


@PromptServer.instance.routes.post("/h3_studio/image_details")
async def image_details(request):
    try:
        data = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "bad request"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"error": "bad request"}, status=400)
    ok = await asyncio.to_thread(
        update_image_details, _image_dir(), data.get("filename"), data,
    )
    return web.json_response({"ok": ok}, status=200 if ok else 400)


@PromptServer.instance.routes.post("/h3_studio/delete_image")
async def delete_image(request):
    try:
        data = await request.json()
    except (ValueError, TypeError):
        return web.json_response({"error": "bad request"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"error": "bad request"}, status=400)
    ok = await asyncio.to_thread(delete_image_output, _image_dir(), data.get("filename"))
    return web.json_response({"ok": ok}, status=200 if ok else 404)


@PromptServer.instance.routes.post("/h3_studio/delete_saved")
async def delete_saved(request):
    data = await request.json()
    name = data.get("filename")
    if not _safe_path(name) or not os.path.isfile(_safe_path(name)):
        return web.json_response({"error": "rejected"}, status=400)
    with _lock:
        if not _tracked.get(name, {}).get("kept"):
            return web.json_response({"error": "not saved"}, status=404)
    wiped = await asyncio.to_thread(_discard, name)
    return web.json_response({"ok": wiped}, status=200 if wiped else 500)


@PromptServer.instance.routes.post("/h3_studio/discard")
async def discard(request):
    """Wipe a clip. Also reachable via sendBeacon on tab close."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not data:
        try:
            data = {"filename": (await request.text()).strip()}
        except Exception:
            return web.json_response({"error": "no filename"}, status=400)
    name = data.get("filename")
    if not _resolve(name):
        return web.json_response({"error": "rejected"}, status=400)
    with _lock:
        entry = _tracked.get(name)
    if entry and entry.get("kept"):
        return web.json_response({"ok": True, "skipped": "saved"})
    return web.json_response({"ok": await asyncio.to_thread(_discard, name)})


@PromptServer.instance.routes.post("/h3_studio/session_end")
async def session_end(request):
    """A page is going away. Wipe its clips unless it returns shortly."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "bad request"}, status=400)
    sid = str(data.get("session") or "")[:64]
    files = [f for f in (data.get("filenames") or []) if _safe_input_path(f)]
    try:
        client_ts = int(data.get("client_ts") or 0)
    except (TypeError, ValueError):
        client_ts = 0
    if not sid:
        return web.json_response({"error": "no session"}, status=400)
    with _lock:
        if client_ts and client_ts <= _resumed.get(sid, {}).get("client_ts", 0):
            return web.json_response({"ok": True, "skipped": "stale departure"})
        _pending[sid] = {"deadline": time.time() + SESSION_GRACE, "files": files}
    return web.json_response({"ok": True, "grace": SESSION_GRACE,
                              "scheduled": len(files)})


@PromptServer.instance.routes.post("/h3_studio/session_resume")
async def session_resume(request):
    """The page came back — it was a reload, not a close. Cancel the wipe."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "bad request"}, status=400)
    sid = str(data.get("session") or "")[:64]
    try:
        client_ts = int(data.get("client_ts") or 0)
    except (TypeError, ValueError):
        client_ts = 0
    with _lock:
        if client_ts:
            _resumed[sid] = {"client_ts": client_ts, "seen": time.time()}
        entry = _pending.pop(sid, None)
    # Anything still on disk is the page's again; re-arm its stale timer.
    kept = []
    files = entry["files"] if entry else (data.get("filenames") or [])
    with _lock:
        for name in files:
            path = _resolve(name)
            if path and os.path.isfile(path) and not _tracked.get(name, {}).get("kept"):
                _tracked.setdefault(name, {"kept": False})["ts"] = time.time()
                kept.append(name)
    return web.json_response({"ok": True, "recovered": kept})


def _run_pending_wipes(now):
    """Wipe sessions whose grace period elapsed without them coming back."""
    with _lock:
        due = [(sid, m) for sid, m in _pending.items() if now >= m["deadline"]]
        for sid, _ in due:
            _pending.pop(sid, None)
    for sid, meta in due:
        for name in meta["files"]:
            with _lock:
                entry = _tracked.get(name)
            if entry and entry.get("kept"):
                continue
            _discard(name)
        if meta["files"]:
            print(f"[h3_studio] session {sid[:8]} did not return; "
                  f"wiped {len(meta['files'])} file(s)")


def _untracked_debris(now):
    """Temporary uploaded inputs that no live session claims."""
    found = []
    for d, resolve in ((folder_paths.get_input_directory(), _safe_input_path),):
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            path = resolve(name)
            if not path:
                continue
            with _lock:
                if name in _tracked:
                    continue          # a session owns it; handled below
            try:
                if now - os.path.getmtime(path) > STALE_AFTER:
                    found.append(name)
            except OSError:
                pass
    return found


def _sweep_stale():
    """Catch clips whose tab died before its beacon landed."""
    last_deep = time.time()
    while True:
        time.sleep(TICK)
        now = time.time()
        _run_pending_wipes(now)
        if now - last_deep < SWEEP_EVERY:
            continue
        last_deep = now
        with _lock:
            for sid in [sid for sid, item in _resumed.items()
                        if now - item["seen"] > STALE_AFTER]:
                _resumed.pop(sid, None)
            doomed = [n for n, m in _tracked.items()
                      if _safe_input_path(n) and now - m["ts"] > STALE_AFTER]
        for name in doomed + _untracked_debris(now):
            _discard(name)


def _wipe_orphans_at_startup():
    """Wipe abandoned temporary inputs; never remove generated videos."""
    for d, resolve in ((folder_paths.get_input_directory(), _safe_input_path),):
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            path = resolve(name)
            with _lock:
                saved = _tracked.get(name, {}).get("kept", False)
            if path and not saved:
                secure_wipe(path)
                print(f"[h3_studio] wiped orphan {name}")


_metadata.update(_load_metadata())
_registered_jobs.update(_load_jobs())
for _saved_name in _load_kept():
    _tracked[_saved_name] = {"kept": True, "ts": time.time()}
_wipe_orphans_at_startup()
threading.Thread(target=_sweep_stale, daemon=True).start()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
