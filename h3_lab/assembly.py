"""Sequence Assembly and Stream Stitching for H3 Studio Lab.

Joins accepted clips into a continuous exported MP4 sequence using FFmpeg.
Guarantees:
- Single-pass AAC encoding to prevent compound audio padding delay.
- Bounded memory stream concatenation.
- Verification of decodable video and non-empty audio streams.
"""

import json
import logging
import os
import pathlib
import shutil
import subprocess
import tempfile
import uuid

_LOG = logging.getLogger("h3_lab.assembly")


def get_ffmpeg_path():
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        winget = pathlib.Path(local) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"
        if winget.is_file():
            return str(winget)
    return "ffmpeg"


def get_ffprobe_path():
    found = shutil.which("ffprobe")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        winget = pathlib.Path(local) / "Microsoft" / "WinGet" / "Links" / "ffprobe.exe"
        if winget.is_file():
            return str(winget)
    return "ffprobe"


def inspect_media(path: str) -> dict:
    ffprobe = get_ffprobe_path()
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries", "stream=codec_type,nb_frames,nb_read_frames,duration,width,height,r_frame_rate:format=duration", "-count_frames",
        "-of", "json", str(path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
    return json.loads(res.stdout)


def assemble_sequence(clip_paths: list, output_path: str, fps: int = 24, overlap_frames=None) -> dict:
    """Trim each declared context before one video/audio concatenation encode."""
    if not clip_paths or overlap_frames is None or len(overlap_frames) != len(clip_paths):
        raise ValueError("Declare overlap_frames for every clip")
    if fps != 24:
        raise ValueError("H3 sequences require 24 fps")
    infos = [inspect_media(path) for path in clip_paths]
    frame_counts = []
    canvas = None
    for info, overlap in zip(infos, overlap_frames):
        streams = info.get("streams", [])
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        if not video or not audio:
            raise ValueError("Every clip must contain video and audio")
        from fractions import Fraction
        if Fraction(video.get("r_frame_rate", "0/1")) != fps:
            raise ValueError("Every clip must be canonical 24 fps")
        dimensions = (video.get("width"), video.get("height"))
        canvas = canvas or dimensions
        if dimensions != canvas:
            raise ValueError("Clip canvases do not match")
        count = int(video.get("nb_read_frames") or video.get("nb_frames") or 0)
        if not isinstance(overlap, int) or isinstance(overlap, bool) or overlap < 0 or overlap >= count:
            raise ValueError("Invalid overlap frame count")
        frame_counts.append(count - overlap)
    out_file = pathlib.Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    temp_part = out_file.with_name(out_file.stem + "." + uuid.uuid4().hex + ".part.mp4")
    cmd = [get_ffmpeg_path(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    filters = []
    for index, (path, overlap, count) in enumerate(zip(clip_paths, overlap_frames, frame_counts)):
        cmd.extend(["-i", str(path)])
        filters.append(f"[{index}:v:0]trim=start_frame={overlap},setpts=PTS-STARTPTS[v{index}]")
        start = round(overlap / fps * 48000)
        end = round((overlap + count) / fps * 48000)
        filters.append(f"[{index}:a:0]aresample=48000,atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS,apad,atrim=end_sample={end-start}[a{index}]")
    inputs = "".join(f"[v{index}][a{index}]" for index in range(len(clip_paths)))
    filters.append(f"{inputs}concat=n={len(clip_paths)}:v=1:a=1[v][a]")
    cmd.extend(["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(temp_part)])
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if res.returncode:
            raise RuntimeError("FFmpeg sequence assembly failed: " + res.stderr[-2000:])
        media = inspect_media(str(temp_part))
        video = next((stream for stream in media["streams"] if stream.get("codec_type") == "video"), {})
        if int(video.get("nb_read_frames") or video.get("nb_frames") or 0) != sum(frame_counts):
            raise ValueError("Assembled sequence frame count does not match trimmed takes")
        if not any(stream.get("codec_type") == "audio" and float(stream.get("duration", 0)) > 0 for stream in media["streams"]):
            raise ValueError("Assembled sequence lacks nonempty audio")
        subprocess.run([get_ffmpeg_path(), "-v", "error", "-i", str(temp_part), "-f", "null", "-"],
                       capture_output=True, check=True, timeout=600)
        os.replace(temp_part, out_file)
        return {"output_path": str(out_file), "duration": float(media["format"]["duration"]),
                "streams": ["video", "audio"], "fps": fps, "frames": sum(frame_counts), "clips_joined": len(clip_paths)}
    finally:
        temp_part.unlink(missing_ok=True)
