#!/usr/bin/env bash
# Prepare a fresh OpenBayes workspace while reusing an attached model dataset.
set -Eeuo pipefail

STACK="${H3_STACK:-/output/h3-stack}"
MODELS="${H3_MODELS:-/input0/h3-models}"
REPO="$STACK/repo/minimax-h3-higgsfield"
COMFY="$STACK/ComfyUI/ComfyUI"
COMFY_REVISION="3b4c0b0e457cf0a51cf3038e0a6750d8f96ce251"

[[ -d "$MODELS/diffusion_models" && -d "$MODELS/text_encoders" && -d "$MODELS/vae" ]] || {
    echo "The attached H3 model dataset is missing: $MODELS" >&2
    exit 1
}
nvidia-smi -L >/dev/null
if curl -fsS --max-time 3 http://127.0.0.1:8188/queue >/dev/null 2>&1; then
    echo "ComfyUI already uses port 8188; inspect its queue before changing this workspace." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git ffmpeg curl tar python3-venv python3-pip libgl1

mkdir -p "$STACK/repo" "$(dirname "$COMFY")" "$STACK/logs"
if [[ ! -d "$REPO/.git" ]]; then
    [[ ! -e "$REPO" ]] || { echo "Unexpected repository path: $REPO" >&2; exit 1; }
    git clone --depth 1 https://github.com/underworldhistory1-ctrl/minimax-h3-higgsfield.git "$REPO"
else
    [[ -z "$(git -C "$REPO" status --porcelain)" ]] || { echo "Repository has local changes; inspect before updating: $REPO" >&2; exit 1; }
    git -C "$REPO" pull --ff-only origin main
fi

if [[ ! -f "$COMFY/main.py" ]]; then
    [[ ! -e "$COMFY" ]] || { echo "Unexpected ComfyUI path: $COMFY" >&2; exit 1; }
    git init -q "$COMFY"
    git -C "$COMFY" remote add origin https://github.com/Comfy-Org/ComfyUI.git
    git -C "$COMFY" fetch --depth 1 origin "$COMFY_REVISION"
    git -C "$COMFY" checkout --detach -q FETCH_HEAD
fi

if [[ -L "$COMFY/models" ]]; then
    [[ "$(readlink -f "$COMFY/models")" == "$(readlink -f "$MODELS")" ]] || {
        echo "ComfyUI models points at a different location; inspect it before continuing." >&2
        exit 1
    }
elif [[ -e "$COMFY/models" ]]; then
    saved="$COMFY/models-stock-$(date +%Y%m%d-%H%M%S)"
    mv "$COMFY/models" "$saved"
    echo "Preserved ComfyUI's stock model folders at $saved"
    ln -s "$MODELS" "$COMFY/models"
else
    ln -s "$MODELS" "$COMFY/models"
fi

export QWEN_IMAGE_PROFILES=int8
if [[ ! -x "$COMFY/.venv/bin/python" ]]; then
    python3 -m venv "$COMFY/.venv"
fi
export COMFY_PYTHON="$COMFY/.venv/bin/python"
bash "$REPO/install.sh" --comfy-root "$COMFY" --bind 127.0.0.1
