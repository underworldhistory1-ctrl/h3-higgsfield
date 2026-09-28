#!/usr/bin/env python3
"""Download and verify selected Qwen Image 2.1 profiles for ComfyUI."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qwen_image import MODEL_PROFILES, ModelFile  # noqa: E402


MODEL_REPO = "Comfy-Org/Qwen-Image-2.1"
MODEL_REVISION = "9a44dbdb47cefd046be9c0a13476192f34c8db8e"
HEADROOM_BYTES = 5 * 1024**3


def parse_profiles(value: str | None) -> tuple[str, ...]:
    raw = value if value is not None else os.environ.get("QWEN_IMAGE_PROFILES", "")
    requested = []
    for item in raw.split(","):
        name = item.strip().lower()
        if not name:
            continue
        if name not in MODEL_PROFILES:
            raise ValueError(f"Unknown Qwen Image profile: {name}")
        if name not in requested:
            requested.append(name)
    return tuple(requested)


def selected_files(profiles: tuple[str, ...]) -> tuple[ModelFile, ...]:
    unique: dict[tuple[str, str], ModelFile] = {}
    for profile in profiles:
        for item in MODEL_PROFILES[profile]["files"]:
            unique[(item.folder, item.name)] = item
    return tuple(unique.values())


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def valid_file(path: Path, item: ModelFile, verify_hash: bool = True) -> bool:
    try:
        if path.stat().st_size != item.size:
            return False
        return not verify_hash or file_digest(path) == item.sha256
    except OSError:
        return False


def missing_bytes(models_dir: Path, profiles: tuple[str, ...]) -> int:
    total = 0
    for item in selected_files(profiles):
        target = models_dir / item.folder / item.name
        if not valid_file(target, item, verify_hash=False):
            total += item.size
    return total


def ensure_disk_space(models_dir: Path, profiles: tuple[str, ...], free_bytes: int | None = None) -> int:
    needed = missing_bytes(models_dir, profiles)
    available = shutil.disk_usage(models_dir).free if free_bytes is None else free_bytes
    required = needed + HEADROOM_BYTES
    if available < required:
        raise RuntimeError(
            f"Not enough free disk space: need {required / 1024**3:.1f} GiB "
            f"for missing Qwen files plus 5 GiB headroom, have {available / 1024**3:.1f} GiB"
        )
    return needed


def install_profiles(comfy_root: Path, profiles: tuple[str, ...], offline_check: bool = False) -> None:
    if not profiles:
        print("Qwen Image profiles are disabled; skipping image weights.", flush=True)
        return
    if not (comfy_root / "main.py").is_file():
        raise RuntimeError("ComfyUI main.py not found")
    models_dir = comfy_root / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    if not offline_check:
        needed = ensure_disk_space(models_dir, profiles)
        print(f"Qwen Image download requirement: {needed / 1024**3:.1f} GiB", flush=True)
        from huggingface_hub import hf_hub_download

    for item in selected_files(profiles):
        target = models_dir / item.folder / item.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if valid_file(target, item):
            print("Verified cached Qwen model:", item.name, flush=True)
            continue
        if offline_check:
            raise RuntimeError("Missing or invalid Qwen Image model: " + str(target))
        if target.exists():
            target.unlink()
        print("Downloading/reusing pinned Qwen model:", item.name, flush=True)
        hf_hub_download(
            repo_id=MODEL_REPO,
            filename=f"{item.folder}/{item.name}",
            revision=MODEL_REVISION,
            local_dir=models_dir,
        )
        if not valid_file(target, item):
            raise RuntimeError("Qwen Image model size or SHA-256 mismatch: " + str(target))
    print("Selected Qwen Image profiles are ready:", ", ".join(profiles), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comfy_root", type=Path)
    parser.add_argument("--profiles", help="Comma separated profiles: int8,bf16")
    parser.add_argument("--offline-check", action="store_true")
    args = parser.parse_args()
    profiles = parse_profiles(args.profiles)
    install_profiles(args.comfy_root.resolve(), profiles, args.offline_check)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit("QWEN_MODEL_ERROR: " + str(exc)) from exc
