"""Capabilities and Schema Inspection Service for H3 Studio Lab.

Evaluates host readiness, registered nodes, AV mask capabilities,
and FFmpeg tools, reporting actionable blocking reasons.
"""

import os
import pathlib
import shutil


def check_capabilities(folder_paths_module=None, nodes_module=None) -> dict:
    # 1. FFmpeg tools
    ffmpeg_ok = bool(shutil.which("ffmpeg"))
    ffprobe_ok = bool(shutil.which("ffprobe"))

    # 2. Native and candidate nodes
    registered_nodes = {}
    if nodes_module and hasattr(nodes_module, "NODE_CLASS_MAPPINGS"):
        registered_nodes = nodes_module.NODE_CLASS_MAPPINGS

    required_native = [
        "UNETLoader", "MiniMaxH3SigmaShift", "CLIPLoader", "VAELoader",
        "MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo",
        "ConditioningZeroOut", "KSampler", "VAEDecode", "VAEDecodeAudio",
        "CreateVideo", "H3SaveVideo", "H3ReleaseForDecode", "H3LoadSavedLatent"
    ]
    native_status = {name: (name in registered_nodes) for name in required_native}

    add_guide_ready = "MiniMaxH3AddGuide" in registered_nodes

    continuation_nodes = [
        "MiniMaxH3GeneratedAVMaskedContext",
        "MiniMaxH3ExistingVideoMaskedContext",
        "MiniMaxH3AssembleExtension"
    ]
    continuation_status = {name: (name in registered_nodes) for name in continuation_nodes}

    # 3. Model files
    models_status = {}
    if folder_paths_module and hasattr(folder_paths_module, "get_full_path"):
        expected_models = {
            "fl2va": ("diffusion_models", "minimax_h3_fl2va_pruned_int8_convrot.safetensors", 20970379616),
            "ref2va": ("diffusion_models", "minimax_h3_ref2va_pruned_int8_convrot.safetensors", 20970379616),
            "text_encoder": ("text_encoders", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", 15687142551),
            "video_vae": ("vae", "minimax_h3_video_vae_fp16.safetensors", 5207808496),
            "audio_vae": ("vae", "minimax_h3_audio_vae_fp32.safetensors", 605254808),
        }
        for key, (category, filename, expected_size) in expected_models.items():
            p = folder_paths_module.get_full_path(category, filename)
            try:
                models_status[key] = bool(p and os.path.isfile(p) and os.path.getsize(p) == expected_size)
            except OSError:
                models_status[key] = False

    # 4. Actionable reasons
    missing_reasons = []
    if not ffmpeg_ok:
        missing_reasons.append("FFmpeg executable not found in PATH.")
    if not ffprobe_ok:
        missing_reasons.append("FFprobe executable not found in PATH.")
    for name, ok in native_status.items():
        if not ok:
            missing_reasons.append(f"Required native ComfyUI node '{name}' is not registered.")
    if not add_guide_ready:
        missing_reasons.append("MiniMaxH3AddGuide node is missing from ComfyUI runtime.")
    for name, ok in continuation_status.items():
        if not ok:
            missing_reasons.append(f"Continuation engine node '{name}' is not registered.")

    return {
        "ffmpeg": {"ffmpeg": ffmpeg_ok, "ffprobe": ffprobe_ok},
        "native_nodes": native_status,
        "add_guide": add_guide_ready,
        "continuation_nodes": continuation_status,
        "models": models_status,
        "continuation_ready": all(continuation_status.values()),
        "ready": len(missing_reasons) == 0,
        "missing_reasons": missing_reasons,
    }
