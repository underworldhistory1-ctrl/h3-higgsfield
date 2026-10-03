"""Contain untrusted relative media paths within their managed directory."""
from pathlib import Path
import re


def owned_path(root, name, *, require_file=True):
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError("Invalid relative media path")
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or ':' in name:
        raise ValueError("Media path escapes managed storage")
    base = Path(root).resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base):
        raise ValueError("Media path escapes managed storage")
    if require_file and not target.is_file():
        raise FileNotFoundError("Managed media file not found")
    return target


def owned_video(root, name, *, probe=True):
    """Resolve only generated or controlled imported H3 take media."""
    target = owned_path(root, name)
    parts = Path(name).parts
    generated = len(parts) == 2 and parts[0] == "video"
    imported = len(parts) == 4 and parts[:2] == ("lab_storage", "imported_takes") and re.fullmatch(r"[a-f0-9-]{36}", parts[2])
    if not (generated or imported) or not re.fullmatch(r"h3_studio_[a-f0-9]{12}[^/\\]*\.mp4", target.name):
        raise ValueError("Only managed H3 take videos may be accessed")
    if probe:
        from .assembly import inspect_media
        try:
            streams = inspect_media(str(target)).get("streams", [])
        except Exception as error:
            raise ValueError("Take is not a valid video") from error
        if not any(item.get("codec_type") == "video" and int(item.get("nb_read_frames") or item.get("nb_frames") or 0) > 0 for item in streams):
            raise ValueError("Take lacks decodable video frames")
    return target
