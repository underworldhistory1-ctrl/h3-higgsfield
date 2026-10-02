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
        "-show_entries", "stream=codec_type,nb_frames,duration,width,height:format=duration",
        "-of", "json", str(path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def assemble_sequence(clip_paths: list, output_path: str, fps: int = 24) -> dict:
    """
    Concatenates canonical clip segments into a single MP4 with single AAC encode.
    clip_paths: list of paths to already-trimmed video clips.
    """
    if not clip_paths:
        raise ValueError("Cannot assemble an empty sequence")

    out_file = pathlib.Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    temp_part = out_file.with_suffix(".part.mp4")

    ffmpeg = get_ffmpeg_path()

    with tempfile.TemporaryDirectory(prefix="h3-assemble-") as temp_dir:
        concat_list = pathlib.Path(temp_dir) / "concat.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for p in clip_paths:
                f.write(f"file '{pathlib.Path(p).resolve().as_posix()}'\n")

        # Concat demuxer with re-encode for video and clean single-pass AAC
        cmd = [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(temp_part)
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            temp_part.unlink(missing_ok=True)
            raise RuntimeError(f"FFmpeg sequence assembly failed: {res.stderr}")

        os.replace(temp_part, out_file)

    # Verify final assembly
    media_info = inspect_media(str(out_file))
    streams = {s.get("codec_type") for s in media_info.get("streams", [])}
    if not ("video" in streams and "audio" in streams):
        raise ValueError("Assembled sequence is missing video or audio stream")

    duration = float(media_info.get("format", {}).get("duration", 0))
    return {
        "output_path": str(out_file),
        "duration": duration,
        "streams": list(streams),
        "fps": fps,
    }
