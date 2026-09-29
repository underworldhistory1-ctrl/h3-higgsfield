"""Install Qwen Image BF16 in /output while retaining INT8 in /input0.

Run with the H3 ComfyUI Python environment. The verified files are linked into
the shared model tree only after every download succeeds.
"""

import hashlib
import os
from pathlib import Path
import sys

from huggingface_hub import hf_hub_download

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qwen_image import MODEL_PROFILES  # noqa: E402


REVISION = "9a44dbdb47cefd046be9c0a13476192f34c8db8e"
REPO = "Comfy-Org/Qwen-Image-2.1"
STAGING = Path("/output/h3-stack/qwen-bf16")
MODELS = Path("/input0/h3-models")


def verified(path, item):
    if not path.is_file() or path.stat().st_size != item.size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == item.sha256


def main():
    files = [x for x in MODEL_PROFILES["bf16"]["files"] if not (MODELS / x.folder / x.name).is_file()]
    missing = sum(x.size for x in files)
    used = sum(p.stat().st_size for p in Path("/output/h3-stack").rglob("*") if p.is_file() and not p.is_symlink())
    if used + missing + 5 * 1024**3 > 50 * 1000**3:
        raise SystemExit("The 50 GB /output quota lacks 5 GiB headroom for BF16; leaving INT8 intact.")
    print(f"Preparing {missing / 10**9:.1f} GB of BF16 weights in /output", flush=True)
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    for item in files:
        target = STAGING / item.folder / item.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not verified(target, item):
            print("Downloading", item.name, flush=True)
            hf_hub_download(repo_id=REPO, filename=f"{item.folder}/{item.name}", revision=REVISION, local_dir=STAGING)
        if not verified(target, item):
            raise SystemExit(f"BF16 checksum failed: {item.name}")
        print("Verified", item.name, flush=True)
    for item in files:
        link = MODELS / item.folder / item.name
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.exists():
            link.symlink_to(STAGING / item.folder / item.name)
    print("BF16 ready; INT8 preserved.", flush=True)


if __name__ == "__main__":
    main()
