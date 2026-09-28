#!/usr/bin/env python3
"""Read-only check of a running H3 Studio server after ComfyUI restarts."""

import argparse
import getpass
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


REQUIRED_MODELS = ("fl2va", "ref2va", "text_encoder", "video_vae", "audio_vae")
REQUIRED_NODES = (
    "UNETLoader", "MiniMaxH3SigmaShift", "CLIPLoader", "VAELoader",
    "MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo", "KSampler",
    "VAEDecode", "VAEDecodeAudio", "CreateVideo", "H3SaveVideo",
    "LoadImage", "LoadVideo", "GetVideoComponents", "LoadAudio",
    "LoraLoaderModelOnly", "SpectrumApplyMiniMaxH3", "MiniMaxH3MotionCache",
)
REQUIRED_IMAGE_NODES = (
    "UNETLoader", "CLIPLoader", "VAELoader", "TextEncodeQwenImage21",
    "QwenImage21Cache", "EmptyLatentImage", "KSampler", "VAEDecode",
    "ImageScale", "QwenStudioSaveImage",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="ComfyUI base URL, direct or proxy")
    parser.add_argument("--token-env", default="H3_ACCESS_TOKEN",
                        help="Environment variable containing this rental's access token")
    args = parser.parse_args()
    supplied = urllib.parse.urlsplit(args.url)
    if not supplied.scheme or not supplied.netloc:
        parser.error("--url must be the ComfyUI HTTP(S) URL")
    base = supplied.scheme + "://" + supplied.netloc
    token = os.environ.get(args.token_env)
    if token is None:
        token = urllib.parse.parse_qs(supplied.query).get("arg", [None])[0]
    if token is None:
        token = getpass.getpass("Access token, if required (Enter for none): ") if sys.stdin.isatty() else ""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    if token:
        with opener.open(base + "/?" + urllib.parse.urlencode({"arg": token}), timeout=20):
            pass

    def read(path):
        with opener.open(base + path, timeout=30) as response:
            return json.load(response)

    try:
        ready = read("/h3_studio/readiness")
    except (ValueError, urllib.error.HTTPError):
        password = os.environ.get("H3_INSTANCE_PASSWORD")
        if not password:
            raise RuntimeError("Server login is required; set H3_INSTANCE_PASSWORD for this check.")
        body = urllib.parse.urlencode({"instanceId": password}).encode()
        with opener.open(base + "/simplepod-login", body, timeout=20):
            pass
        ready = read("/h3_studio/readiness")
    stats = read("/system_stats")
    loras = read("/h3_studio/loras")
    library = read("/h3_studio/library")
    image_ready = read("/h3_studio/image_readiness")
    image_library = read("/h3_studio/image_library")
    queue = read("/queue")
    missing_models = [name for name in REQUIRED_MODELS if not ready.get("models", {}).get(name)]
    missing_nodes = [name for name in REQUIRED_NODES if not ready.get("nodes", {}).get(name)]
    vae_tile_fix = bool(ready.get("quality", {}).get("h3_vae_tile_fix"))
    selected_profiles = tuple(name.strip() for name in os.environ.get("QWEN_IMAGE_PROFILES", "int8").split(",") if name.strip())
    missing_image_profiles = [name for name in selected_profiles if not image_ready.get("profiles", {}).get(name, {}).get("ready")]
    missing_image_nodes = [name for name in REQUIRED_IMAGE_NODES if not image_ready.get("nodes", {}).get(name)]
    names = loras.get("items", [])
    print(json.dumps({
        "gpu": [item.get("name") for item in stats.get("devices", [])],
        "models_ready": not missing_models,
        "missing_models": missing_models,
        "missing_nodes": missing_nodes,
        "h3_vae_tile_fix": vae_tile_fix,
        "installed_loras": names,
        "video_count": len(library.get("items", [])),
        "qwen_profiles_ready": not missing_image_profiles,
        "missing_qwen_profiles": missing_image_profiles,
        "missing_qwen_nodes": missing_image_nodes,
        "image_count": len(image_library.get("items", [])),
        "running_jobs": len(queue.get("queue_running", [])),
        "pending_jobs": len(queue.get("queue_pending", [])),
    }, indent=2, ensure_ascii=False))
    if missing_models or missing_nodes or not vae_tile_fix or missing_image_profiles or missing_image_nodes:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
