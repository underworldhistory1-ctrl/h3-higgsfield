"""Qwen Image 2.1 model contract, API graph builder and output node.

The module keeps pure validation and graph construction importable without a
running ComfyUI process. Comfy-only dependencies are loaded inside the saver.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import re
import time


@dataclass(frozen=True)
class ModelFile:
    folder: str
    name: str
    size: int
    sha256: str


VAE = ModelFile(
    "vae",
    "qwen_image_2.1_vae_bf16.safetensors",
    675_509_688,
    "bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9",
)

MODEL_PROFILES = {
    "int8": {
        "label": "Fast / efficient",
        "unet": "qwen_image_2.1_int8_convrot.safetensors",
        "clip": "qwen3vl_8b_int8_convrot.safetensors",
        "bytes": 17_283_091_112,
        "files": (
            ModelFile("diffusion_models", "qwen_image_2.1_int8_convrot.safetensors", 7_256_783_064,
                      "cb74113cb03faecd79611b01fd7fd642f0aa60d6f0b95086abee214d75eaa57d"),
            ModelFile("text_encoders", "qwen3vl_8b_int8_convrot.safetensors", 9_350_798_360,
                      "8bfd0f6e12abf2d2d697ecc888e5e90b0d6741d6708f05799f53afa560452e8f"),
            VAE,
        ),
    },
    "bf16": {
        "label": "Maximum precision",
        "unet": "qwen_image_2.1_bf16.safetensors",
        "clip": "qwen3vl_8b_bf16.safetensors",
        "bytes": 32_440_124_920,
        "files": (
            ModelFile("diffusion_models", "qwen_image_2.1_bf16.safetensors", 14_230_280_616,
                      "89f4158d066cc33906a199fca85634f766892dd78f49b6698dabf187ac86c4bc"),
            ModelFile("text_encoders", "qwen3vl_8b_bf16.safetensors", 17_534_334_616,
                      "68bdc82bc1b66851162ae656225e7e2068166b603db19bd5d5a3b90eb12669a9"),
            VAE,
        ),
    },
}

REQUIRED_QWEN_NODES = (
    "UNETLoader", "CLIPLoader", "VAELoader", "TextEncodeQwenImage21",
    "QwenImage21Cache", "EmptyLatentImage", "KSampler", "VAEDecode",
    "QwenStudioSaveImage",
)


def required_qwen_nodes():
    return REQUIRED_QWEN_NODES


def profile_status(comfy_root):
    models = Path(comfy_root) / "models"
    result = {}
    for key, profile in MODEL_PROFILES.items():
        missing = []
        invalid = []
        for item in profile["files"]:
            path = models / item.folder / item.name
            if not path.is_file():
                missing.append(item.name)
            elif path.stat().st_size != item.size:
                invalid.append(item.name)
        result[key] = {
            "label": profile["label"],
            "ready": not missing and not invalid,
            "missing": missing,
            "invalid": invalid,
            "bytes": profile["bytes"],
        }
    return result


def _integer(value, name, minimum, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _transparent_prompt(prompt):
    clean = prompt.strip().rstrip(".")
    return (
        "This is an RGBA image with transparency. " + clean
        + ". The image has an alpha channel and a transparent background."
    )


def build_qwen_graph(request, uploads, token):
    mode = str(request.get("mode", "create")).lower()
    if mode not in {"create", "edit"}:
        raise ValueError("Mode must be create or edit")
    profile_key = str(request.get("profile", "int8")).lower()
    if profile_key not in MODEL_PROFILES:
        raise ValueError(f"Unknown Qwen profile: {profile_key}")
    prompt = str(request.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("Prompt is required")
    uploads = list(uploads or [])
    if mode == "edit" and not uploads:
        raise ValueError("Edit mode requires at least one image")
    if len(uploads) > 10:
        raise ValueError("Qwen Image supports up to 10 reference images")
    width = _integer(request.get("width", 1024), "Width", 256, 2752)
    height = _integer(request.get("height", 1024), "Height", 256, 2752)
    if width % 32 or height % 32:
        raise ValueError("Width and height must be a multiple of 32")
    steps = _integer(request.get("steps", 40), "Steps", 1, 100)
    seed = _integer(request.get("seed", 0), "Seed", 0, 2**53 - 1)
    reference_resolution = _integer(request.get("reference_resolution", 1024), "Reference resolution", 0, 4096)
    safe_token = re.sub(r"[^A-Za-z0-9_-]", "", str(token))[:64]
    if not safe_token:
        raise ValueError("A safe generation token is required")
    if mode == "create" and request.get("transparent"):
        prompt = _transparent_prompt(prompt)
    profile = MODEL_PROFILES[profile_key]
    metadata = {
        "kind": "image", "mode": mode, "profile": profile_key, "prompt": prompt,
        "width": width, "height": height, "steps": steps, "seed": seed,
        "transparent": bool(mode == "create" and request.get("transparent")),
        "references": [os.path.basename(str(name)) for name in uploads],
        "submitted_at": time.time(), "token": safe_token,
    }
    graph = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": profile["unet"], "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": profile["clip"], "type": "qwen_image", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": VAE.name}},
        "enc": {"class_type": "TextEncodeQwenImage21", "inputs": {
            "clip": ["clip", 0], "prompt": prompt, "negative_prompt": "", "resolution": reference_resolution,
        }},
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": ["vae", 0]}},
        "save": {"class_type": "QwenStudioSaveImage", "inputs": {
            "images": ["decode", 0], "token": safe_token,
            "metadata_json": json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        }},
    }
    if mode == "create":
        graph["latent"] = {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}}
        model_link = ["unet", 0]
        latent_link = ["latent", 0]
    else:
        graph["enc"]["inputs"]["vae"] = ["vae", 0]
        for index, filename in enumerate(uploads, 1):
            node_id = f"load{index}"
            graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": filename}}
            graph["enc"]["inputs"][f"images.image_{index}"] = [node_id, 0]
        graph["cache"] = {"class_type": "QwenImage21Cache", "inputs": {
            "model": ["unet", 0], "device": "auto", "dtype": "default",
        }}
        model_link = ["cache", 0]
        latent_link = ["enc", 2]
    graph["sampler"] = {"class_type": "KSampler", "inputs": {
        "model": model_link, "seed": seed, "steps": steps, "cfg": 1.0,
        "sampler_name": "euler", "scheduler": "simple", "positive": ["enc", 0],
        "negative": ["enc", 1], "latent_image": latent_link, "denoise": 1.0,
    }}
    return graph


class QwenStudioSaveImage:
    """Save validated PNG outputs and an adjacent settings sidecar."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "token": ("STRING", {"default": ""}),
            "metadata_json": ("STRING", {"default": "{}", "multiline": True}),
        }}

    RETURN_TYPES = ()
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "H3 Studio"

    def save(self, images, token, metadata_json):
        import folder_paths
        import numpy as np
        from PIL import Image

        safe_token = re.sub(r"[^A-Za-z0-9_-]", "", str(token))[:64]
        if not safe_token:
            raise ValueError("Invalid Qwen Studio output token")
        metadata = json.loads(metadata_json)
        output_dir = Path(folder_paths.get_output_directory()) / "images"
        output_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for index, tensor in enumerate(images, 1):
            array = np.clip(tensor.detach().cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
            if array.ndim != 3 or array.shape[-1] not in (3, 4):
                raise ValueError(f"Expected RGB or RGBA image, got shape {array.shape}")
            name = f"qwen_studio_{safe_token}_{index:05d}.png"
            path = output_dir / name
            Image.fromarray(array, "RGBA" if array.shape[-1] == 4 else "RGB").save(path, format="PNG", compress_level=4)
            sidecar = dict(metadata)
            sidecar["filename"] = name
            sidecar["completed_at"] = time.time()
            sidecar_path = path.with_suffix(".json")
            temporary = sidecar_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, sidecar_path)
            saved.append({"filename": name, "subfolder": "images", "type": "output"})
        return {"ui": {"images": saved}}


NODE_CLASS_MAPPINGS = {"QwenStudioSaveImage": QwenStudioSaveImage}
NODE_DISPLAY_NAME_MAPPINGS = {"QwenStudioSaveImage": "Qwen Studio Save Image"}


_OUTPUT_RE = re.compile(r"qwen_studio_[A-Za-z0-9_-]{1,64}_[0-9]{5}\.png")


def safe_image_path(image_dir, filename):
    if not isinstance(filename, str) or not _OUTPUT_RE.fullmatch(filename):
        return None
    root = Path(image_dir).resolve()
    candidate = (root / filename).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _load_sidecar(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def scan_image_library(image_dir):
    from PIL import Image

    root = Path(image_dir)
    if not root.is_dir():
        return []
    items = []
    for candidate in root.iterdir():
        path = safe_image_path(root, candidate.name)
        if path is None or not path.is_file():
            continue
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
                mode = image.mode
            stat = path.stat()
        except (OSError, ValueError):
            continue
        settings = _load_sidecar(path.with_suffix(".json"))
        items.append({
            "filename": path.name, "subfolder": "images", "type": "output",
            "bytes": stat.st_size, "modified": stat.st_mtime,
            "width": width, "height": height, "has_alpha": "A" in mode,
            "settings": settings,
            "render_seconds": settings.get("render_seconds"),
        })
    items.sort(key=lambda item: item["modified"], reverse=True)
    return items


def update_image_details(image_dir, filename, details):
    path = safe_image_path(image_dir, filename)
    if path is None or not path.is_file() or not isinstance(details, dict):
        return False
    sidecar_path = path.with_suffix(".json")
    metadata = _load_sidecar(sidecar_path)
    render_seconds = details.get("render_seconds")
    if isinstance(render_seconds, (int, float)) and not isinstance(render_seconds, bool) and 0 < render_seconds < 86400:
        metadata["render_seconds"] = round(float(render_seconds), 2)
    settings = details.get("settings")
    if isinstance(settings, dict):
        allowed = {"mode", "profile", "prompt", "width", "height", "steps", "seed",
                   "transparent", "references", "reference_resolution"}
        for key in allowed:
            if key in settings:
                metadata[key] = settings[key]
    temporary = sidecar_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, sidecar_path)
    return True


def delete_image_output(image_dir, filename):
    path = safe_image_path(image_dir, filename)
    if path is None or not path.is_file():
        return False
    try:
        path.unlink()
        sidecar = path.with_suffix(".json")
        if sidecar.is_file():
            sidecar.unlink()
        return True
    except OSError:
        return False
