"""Lab-owned AV trimming and durable, integrity-checked sampler contexts.

The masked-context engine remains a separately installed dependency.
"""
import hashlib
import json
import os
import pathlib
import re

import folder_paths
from safetensors.torch import load_file


def file_hash(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_av(tensors, width, height, frames=None):
    if set(tensors) != {"nested_0", "nested_1"}:
        raise ValueError("Continuation requires exactly two AV latent streams")
    video, audio = tensors["nested_0"], tensors["nested_1"]
    if video.ndim != 5 or audio.ndim != 4:
        raise ValueError("Invalid AV latent ranks")
    if video.shape[0] != 1 or audio.shape[0] != 1:
        raise ValueError("Continuation supports a single AV sample")
    if video.shape[-4] != 24 or audio.shape[-3] != 32 or audio.shape[-2] != 2:
        raise ValueError("Invalid H3 AV latent channels")
    if tuple(video.shape[-2:]) != (height // 16, width // 16) or min(video.shape) < 1 or min(audio.shape) < 1:
        raise ValueError("AV latent canvas mismatch")
    if not video.is_floating_point() or not audio.is_floating_point():
        raise ValueError("AV context must use floating-point tensors")
    if frames is not None:
        if not isinstance(frames, int) or isinstance(frames, bool) or frames < 5 or (frames - 5) % 17:
            raise ValueError("Invalid recorded H3 frame grid")
        if video.shape[-3] != 2 + 5 * ((frames - 5) // 17) or audio.shape[-1] != round(frames * 40 / 24):
            raise ValueError("Context video/audio temporal shapes disagree with recorded frames")


def checkpoint_signature(prompt):
    signature = []
    kinds = {"UNETLoader", "CLIPLoader", "VAELoader", "LoraLoaderModelOnly", "MiniMaxH3SigmaShift",
             "SpectrumApplyMiniMaxH3", "MiniMaxH3MotionCache"}
    categories = {"unet_name": "diffusion_models", "clip_name": "text_encoders",
                  "vae_name": "vae", "lora_name": "loras"}
    for node in prompt.values():
        if not isinstance(node, dict) or node.get("class_type") not in kinds:
            continue
        fields = {k: v for k, v in node.get("inputs", {}).items() if not isinstance(v, list)}
        identities = {}
        for field, category in categories.items():
            if field in fields and hasattr(folder_paths, "get_full_path"):
                path = folder_paths.get_full_path(category, fields[field])
                if not path or not pathlib.Path(path).is_file():
                    raise ValueError("Context dependency is missing: " + fields[field])
                stat = pathlib.Path(path).stat()
                identities[field] = [stat.st_size, stat.st_mtime_ns]
        signature.append({"node": node["class_type"], "fields": fields, "files": identities})
    return signature


def context_directory():
    path = pathlib.Path(folder_paths.get_output_directory()) / "h3_lab_contexts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def preserve_context(checkpoint, token, prompt, tensors):
    if not isinstance(prompt, dict):
        return  # Decode-only recovery does not invent an engine identity.
    inputs = [n.get("inputs", {}) for n in prompt.values() if isinstance(n, dict)]
    model = next((i["unet_name"] for i in inputs if "unet_name" in i), None)
    canvas = next((i for i in inputs if all(k in i for k in ("width", "height", "length"))), None)
    if not model or canvas is None:
        return
    folder = context_directory()
    target = folder / (token + ".safetensors")
    part = folder / (token + ".part")
    # Stream the already CPU-safe checkpoint rather than duplicating tensor buffers.
    import shutil
    shutil.copyfile(checkpoint, part)
    digest = file_hash(part)
    record = {"version": 1, "token": token, "model_id": model,
              "width": canvas["width"], "height": canvas["height"], "fps": 24,
              "frames": canvas["length"], "sha256": digest, "checkpoint_signature": checkpoint_signature(prompt),
              "tensors": {k: {"shape": list(v.shape), "dtype": str(v.dtype)} for k, v in tensors.items()}}
    metadata = folder / (token + ".json")
    metadata_part = folder / (token + ".json.part")
    metadata_part.write_text(json.dumps(record), encoding="utf-8")
    os.replace(part, target)
    os.replace(metadata_part, metadata)


class H3LabLoadContext:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"token": ("STRING",), "model_id": ("STRING",),
                "width": ("INT", {"min": 32}), "height": ("INT", {"min": 32}),
                "fps": ("INT", {"default": 24})}, "hidden": {"prompt": "PROMPT"}}

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "load"
    CATEGORY = "H3 Studio/Lab"

    def load(self, token, model_id, width, height, fps, prompt=None):
        if fps != 24 or width <= 0 or height <= 0 or width % 32 or height % 32:
            raise ValueError("H3 contexts require a multiple-of-32 canvas and 24 fps")
        if not re.fullmatch(r"[a-f0-9]{12}", token):
            raise ValueError("Invalid context token")
        folder = context_directory()
        record = json.loads((folder / (token + ".json")).read_text(encoding="utf-8"))
        if (record.get("model_id"), record.get("width"), record.get("height"), record.get("fps")) != (model_id, width, height, fps):
            raise ValueError("Context checkpoint, canvas or frame rate differs from this render")
        if isinstance(prompt, dict) and record.get("checkpoint_signature") != checkpoint_signature(prompt):
            raise ValueError("Context model, VAE, LoRA or acceleration configuration changed")
        path = folder / (token + ".safetensors")
        if file_hash(path) != record.get("sha256"):
            raise ValueError("Context checksum mismatch")
        tensors = load_file(str(path), device="cpu")
        shapes = {k: {"shape": list(v.shape), "dtype": str(v.dtype)} for k, v in tensors.items()}
        if shapes != record.get("tensors"):
            raise ValueError("Context tensor schema mismatch")
        if "frames" not in record:
            raise ValueError("Context has no recorded temporal shape")
        validate_av(tensors, width, height, record["frames"])
        from comfy.nested_tensor import NestedTensor
        samples = NestedTensor([tensors["nested_0"], tensors["nested_1"]])
        return ({"samples": samples},)


class H3LabTrimAV:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"images": ("IMAGE",), "audio": ("AUDIO",),
                "trim_frames": ("INT", {"min": 0}), "fps": ("INT", {"default": 24})}}

    RETURN_TYPES = ("IMAGE", "AUDIO")
    FUNCTION = "trim"
    CATEGORY = "H3 Studio/Lab"

    def trim(self, images, audio, trim_frames, fps=24):
        if fps != 24 or not isinstance(trim_frames, int) or not 0 <= trim_frames < len(images):
            raise ValueError("Invalid AV overlap trim")
        rate = audio.get("sample_rate", 0)
        waveform = audio.get("waveform")
        if rate <= 0 or waveform is None or waveform.ndim != 3:
            raise ValueError("A valid generated audio buffer is required")
        start = round(trim_frames * rate / fps)
        count = round((len(images) - trim_frames) * rate / fps)
        if waveform.shape[-1] < start + count:
            raise ValueError("Generated audio is shorter than the unique continuation")
        return images[trim_frames:], {**audio, "waveform": waveform[..., start:start + count]}


NODE_CLASS_MAPPINGS = {"H3LabLoadContext": H3LabLoadContext, "H3LabTrimAV": H3LabTrimAV}
