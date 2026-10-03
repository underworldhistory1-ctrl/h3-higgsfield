"""Prepare a bounded, private continuation source without GPU inference."""
import json
import math
import pathlib
import subprocess
from .assembly import get_ffmpeg_path, get_ffprobe_path, inspect_media


def require_cpu_headroom(meminfo_path="/proc/meminfo", cgroup_root="/sys/fs/cgroup"):
    """Refuse CPU video preparation when this server cannot spare 512 MiB."""
    meminfo = pathlib.Path(meminfo_path)
    if not meminfo.is_file():  # Windows CPU test hosts do not expose Linux memory accounting.
        return
    try:
        stats = dict(line.split(":", 1) for line in meminfo.read_text().splitlines() if ":" in line)
        available = int(stats["MemAvailable"].split()[0]) * 1024
        cgroup = pathlib.Path(cgroup_root)
        if (cgroup / "memory.current").is_file():
            maximum = (cgroup / "memory.max").read_text().strip()
            if maximum != "max":
                current = int((cgroup / "memory.current").read_text())
                cache = dict(line.split() for line in (cgroup / "memory.stat").read_text().splitlines())
                reclaimable = int(cache.get("inactive_file", "0"))
                available = min(available, max(0, int(maximum) - current + reclaimable))
    except (OSError, ValueError, KeyError):
        raise ValueError("Server memory could not be checked; retry video preparation later") from None
    if available < 512 * 1024 * 1024:
        raise ValueError("Server memory is busy; retry video preparation after current work finishes")


def prepare_video_source(source, destination, width, height, keep_seconds=5, fit="crop"):
    if any(type(n) is not int or n < 256 or n > 2048 or n % 32 for n in (width, height)):
        raise ValueError("Select a 256–2048 px H3 canvas with dimensions divisible by 32")
    if fit not in ("crop", "contain"):
        raise ValueError("Choose crop or contain")
    keep = float(keep_seconds)
    if not math.isfinite(keep) or not 2 <= keep <= 15.1:
        raise ValueError("Use the last 2–15.1 seconds of the source")
    require_cpu_headroom()
    probe = subprocess.run([get_ffprobe_path(), "-v", "error", "-protocol_whitelist", "file,pipe",
        "-show_streams", "-show_format", "-of", "json", str(source)],
        capture_output=True, text=True, check=True, timeout=30)
    info = json.loads(probe.stdout)
    formats = set(info.get("format", {}).get("format_name", "").split(","))
    if not formats.intersection({"mov", "mp4", "matroska", "webm", "avi"}):
        raise ValueError("Use an MP4, MOV, WebM, MKV or AVI video")
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video or not all(2 <= int(video.get(key, 0)) <= 8192 for key in ("width", "height")) or not 0 < int(video.get("width", 0)) * int(video.get("height", 0)) <= 8847360:
        raise ValueError("Source must contain a video stream no larger than approximately 4K (8.8 megapixels)")
    duration = float(video.get("duration") or info.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Source duration could not be determined")
    frames = min(362, int(min(duration, keep) * 24 + 1e-6))
    if frames < 40:
        raise ValueError("Source must provide at least 40 frames at 24 fps (1.67 seconds)")
    use_duration = frames / 24
    start = max(0, duration - use_duration)
    has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])
    sizing = (f"crop='trunc(min(iw,ih*{width}/{height})/2)*2':'trunc(min(ih,iw*{height}/{width})/2)*2',scale={width}:{height}"
              if fit == "crop" else f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black")
    target = pathlib.Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [get_ffmpeg_path(), "-nostdin", "-v", "error", "-y", "-threads", "1",
        "-protocol_whitelist", "file,pipe", "-ss", str(start), "-i", str(source)]
    if not has_audio:
        command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    command += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0",
        "-vf", f"fps=24,{sizing},setsar=1,tpad=stop_mode=clone:stop_duration=0.1",
        "-af", f"aresample=48000:async=1:first_pts=0,apad,atrim=duration={use_duration}",
        "-frames:v", str(frames), "-t", str(use_duration), "-c:v", "libx264", "-threads", "1",
        "-filter_threads", "1", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-map_metadata", "-1", "-movflags", "+faststart", str(target)]
    try:
        subprocess.run(command, capture_output=True, check=True, timeout=180)
        result = inspect_media(str(target))
        decoded = next(s for s in result["streams"] if s["codec_type"] == "video")
        if int(decoded.get("nb_read_frames") or decoded.get("nb_frames") or 0) != frames:
            raise ValueError("Prepared source has an unexpected decoded frame count")
        if not any(s.get("codec_type") == "audio" for s in result["streams"]):
            raise ValueError("Prepared source lacks its audio timeline")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {"fps": 24, "width": width, "height": height, "frame_count": frames,
        "source": "uploaded_video", "original_duration_seconds": duration, "source_start_seconds": start,
        "used_duration_seconds": use_duration, "source_has_audio": has_audio,
        "fit": fit, "context_frames": 39, "context_seconds": 39 / 24}
