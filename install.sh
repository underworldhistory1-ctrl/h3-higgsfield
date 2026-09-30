#!/usr/bin/env bash
# Install H3 Higgsfield on an NVIDIA Linux server with or without ComfyUI.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMFY_ROOT=""
PORT=8188
BIND=127.0.0.1
AUTO_START=1
COMFY_REVISION="3b4c0b0e457cf0a51cf3038e0a6750d8f96ce251"
H3_VAE_FIX_MARKER='strip[..., :, x_idx[j]:x_idx[j] + x_len[j]]'
QWEN_NODE_FILE_REL='comfy_extras/nodes_qwen.py'

usage() {
    cat <<'EOF'
Usage: bash install.sh [--comfy-root PATH] [--port 8188] [--bind 127.0.0.1] [--no-start]

On an NVIDIA Linux server, detects an existing ComfyUI installation or installs
the verified H3-capable ComfyUI revision. It installs the H3 models, optional
accelerators and LoRAs, starts/restarts ComfyUI when safe, checks readiness,
and prints the H3 Higgsfield web address. Model downloads are about 63.4 GB.

The default bind address is local-only. Use --bind 0.0.0.0 only behind your
cloud provider's authenticated proxy or firewall.
EOF
}

while (($#)); do
    case "$1" in
        --comfy-root) COMFY_ROOT="${2:?Missing path}"; shift 2 ;;
        --port) PORT="${2:?Missing port}"; shift 2 ;;
        --bind) BIND="${2:?Missing address}"; shift 2 ;;
        --no-start) AUTO_START=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT >= 1 && PORT <= 65535)) || { echo "Invalid port." >&2; exit 2; }
[[ "$BIND" == "127.0.0.1" || "$BIND" == "0.0.0.0" ]] || { echo "Use --bind 127.0.0.1 or 0.0.0.0." >&2; exit 2; }

if [[ -z "$COMFY_ROOT" ]]; then
    for candidate in /app/ComfyUI /workspace/ComfyUI "$HOME/ComfyUI" "$ROOT/../ComfyUI"; do
        if [[ -f "$candidate/main.py" ]]; then COMFY_ROOT="$candidate"; break; fi
    done
    [[ -n "$COMFY_ROOT" ]] || COMFY_ROOT="$ROOT/../ComfyUI"
fi
mkdir -p "$(dirname "$COMFY_ROOT")"
COMFY_ROOT="$(cd "$(dirname "$COMFY_ROOT")" && pwd)/$(basename "$COMFY_ROOT")"
SERVER_URL="http://127.0.0.1:$PORT"

if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi -L >/dev/null 2>&1; then
    echo "An NVIDIA GPU and working driver are required (nvidia-smi failed)." >&2
    exit 1
fi

missing=()
for tool in git python3 curl ffmpeg ffprobe tar; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if ((${#missing[@]})); then
    if [[ "$(id -u)" -eq 0 ]] && command -v apt-get >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl ffmpeg tar python3 python3-venv python3-pip libgl1
    else
        echo "Missing ${missing[*]}. Install them with your package manager, or run on root-access Ubuntu/Debian." >&2
        exit 1
    fi
fi

existing=0
if [[ -f "$COMFY_ROOT/main.py" ]]; then
    existing=1
    echo "Found ComfyUI: $COMFY_ROOT"
    if [[ -z "${H3_INSTANCE_PASSWORD:-}" ]] \
            && [[ "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 4 "$SERVER_URL/queue" || true)" == "302" ]]; then
        read -r -s -p "ComfyUI instance password (not saved): " H3_INSTANCE_PASSWORD
        echo
        export H3_INSTANCE_PASSWORD
    fi
    python3 "$ROOT/deploy/activate_h3.py" check --url "$SERVER_URL"
elif [[ -d "$COMFY_ROOT/.git" ]] \
        && [[ "$(git -C "$COMFY_ROOT" remote get-url origin 2>/dev/null || true)" == "https://github.com/Comfy-Org/ComfyUI.git" ]]; then
    echo "Resuming the official ComfyUI source checkout..."
    git -C "$COMFY_ROOT" fetch --depth 1 origin "$COMFY_REVISION"
    git -C "$COMFY_ROOT" checkout --detach -q FETCH_HEAD
elif [[ -e "$COMFY_ROOT" && -n "$(ls -A "$COMFY_ROOT" 2>/dev/null)" ]]; then
    echo "Refusing to overwrite a non-ComfyUI directory: $COMFY_ROOT" >&2
    exit 1
else
    if curl -sS -o /dev/null --max-time 3 "$SERVER_URL/queue"; then
        echo "A server already uses port $PORT, but its ComfyUI folder was not found. Pass --comfy-root before installing." >&2
        exit 1
    fi
    echo "Installing official ComfyUI at the verified H3 revision..."
    git init -q "$COMFY_ROOT"
    git -C "$COMFY_ROOT" remote add origin https://github.com/Comfy-Org/ComfyUI.git
    git -C "$COMFY_ROOT" fetch --depth 1 origin "$COMFY_REVISION"
    git -C "$COMFY_ROOT" checkout --detach -q FETCH_HEAD
fi

H3_NODE_FILE="$COMFY_ROOT/comfy_extras/nodes_minimax_h3.py"
H3_VAE_FILE="$COMFY_ROOT/comfy/ldm/minimax/vae.py"
QWEN_NODE_FILE="$COMFY_ROOT/$QWEN_NODE_FILE_REL"
if [[ ! -f "$H3_NODE_FILE" ]] || ! grep -q MiniMaxH3ReferenceToVideo "$H3_NODE_FILE" \
        || [[ ! -f "$H3_VAE_FILE" ]] || ! grep -Fq "$H3_VAE_FIX_MARKER" "$H3_VAE_FILE" \
        || [[ ! -f "$QWEN_NODE_FILE" ]] || ! grep -q TextEncodeQwenImage21 "$QWEN_NODE_FILE" \
        || ! grep -q QwenImage21Cache "$QWEN_NODE_FILE"; then
    if [[ ! -d "$COMFY_ROOT/.git" ]] || [[ -n "$(git -C "$COMFY_ROOT" status --porcelain --untracked-files=no)" ]]; then
        echo "ComfyUI lacks the native H3 nodes or the September 22 H3 VAE tile fix, and has no clean Git checkout to update safely." >&2
        exit 1
    fi
    echo "Updating ComfyUI to the pinned H3 build with the VAE tile fix..."
    git -C "$COMFY_ROOT" fetch --depth 1 origin "$COMFY_REVISION"
    git -C "$COMFY_ROOT" branch "h3-before-update-$(date +%Y%m%d-%H%M%S)" HEAD
    git -C "$COMFY_ROOT" checkout --detach -q FETCH_HEAD
    echo "Native H3 nodes and the H3 VAE tile fix are now present; keeping the existing Python environment."
fi
grep -q MiniMaxH3ReferenceToVideo "$H3_NODE_FILE" || { echo "Pinned ComfyUI lacks the H3 reference node." >&2; exit 1; }
grep -Fq "$H3_VAE_FIX_MARKER" "$H3_VAE_FILE" || { echo "Pinned ComfyUI lacks the H3 VAE tile fix." >&2; exit 1; }
grep -q TextEncodeQwenImage21 "$QWEN_NODE_FILE" || { echo "Pinned ComfyUI lacks Qwen Image 2.1 text encoding." >&2; exit 1; }
grep -q QwenImage21Cache "$QWEN_NODE_FILE" || { echo "Pinned ComfyUI lacks Qwen Image 2.1 edit caching." >&2; exit 1; }

if [[ -n "${COMFY_PYTHON:-}" && -x "${COMFY_PYTHON}" ]]; then
    H3_PYTHON="$COMFY_PYTHON"
elif [[ -x "$COMFY_ROOT/.venv/bin/python" ]]; then
    H3_PYTHON="$COMFY_ROOT/.venv/bin/python"
elif [[ -x "$COMFY_ROOT/venv/bin/python" ]]; then
    H3_PYTHON="$COMFY_ROOT/venv/bin/python"
elif ((existing)); then
    H3_PYTHON="$(command -v python3)"
else
    echo "Creating the ComfyUI Python environment..."
    if ! python3 -m venv "$COMFY_ROOT/.venv"; then
        if [[ "$(id -u)" -eq 0 ]] && command -v apt-get >/dev/null 2>&1; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv
            python3 -m venv "$COMFY_ROOT/.venv"
        else
            echo "Python venv is unavailable; install python3-venv and rerun." >&2
            exit 1
        fi
    fi
    H3_PYTHON="$COMFY_ROOT/.venv/bin/python"
fi
export COMFY_PYTHON="$H3_PYTHON"

if ! "$H3_PYTHON" -c 'import torch, av, aiohttp, PIL; assert torch.cuda.is_available(); assert tuple(map(int, torch.version.cuda.split(".")[:2])) >= (13, 0)' 2>/dev/null; then
    if [[ "$H3_PYTHON" == "$(command -v python3)" ]]; then
        echo "The running ComfyUI Python is missing CUDA/PyAV dependencies. Pass COMFY_PYTHON pointing to its environment." >&2
        exit 1
    fi
    echo "Installing CUDA PyTorch and ComfyUI dependencies (first setup can take time)..."
    "$H3_PYTHON" -m pip install --upgrade pip
    "$H3_PYTHON" -m pip install --upgrade 'torch==2.9.1+cu130' 'torchvision==0.24.1+cu130' 'torchaudio==2.9.1+cu130' --index-url https://download.pytorch.org/whl/cu130
    "$H3_PYTHON" -m pip install -r "$COMFY_ROOT/requirements.txt" av
    "$H3_PYTHON" -c 'import torch, av, aiohttp, PIL; assert torch.cuda.is_available(), "CUDA is unavailable to PyTorch"; assert tuple(map(int, torch.version.cuda.split(".")[:2])) >= (13, 0), "CUDA 13.0 is required for optimized RTX 5090 operations"'
fi

free_gib="$(df -Pk "$COMFY_ROOT" | awk 'NR==2 {printf "%.0f", $4/1048576}')"
if ((free_gib < 100)); then
    echo "Warning: only about ${free_gib} GiB free here. The first full H3 download needs about 100 GiB unless models are cached on another disk." >&2
fi

bash "$ROOT/deploy/bootstrap_h3_server.sh" "$COMFY_ROOT"
if [[ -n "${QWEN_IMAGE_PROFILES:-}" ]]; then
    "$H3_PYTHON" "$ROOT/deploy/download_qwen_image_models.py" "$COMFY_ROOT" --profiles "$QWEN_IMAGE_PROFILES"
fi
if ((AUTO_START == 0)); then
    echo "Files installed. Start/restart ComfyUI before opening /extensions/h3_studio/index.html."
    exit 0
fi

status="$(python3 "$ROOT/deploy/activate_h3.py" check --url "$SERVER_URL")"
if [[ "$status" == "COMFYUI_OFFLINE" ]]; then
    echo "Starting ComfyUI with H3 Higgsfield as its landing page..."
    (cd "$COMFY_ROOT" && nohup "$H3_PYTHON" main.py --listen "$BIND" --port "$PORT" \
        > "$COMFY_ROOT/h3-higgsfield-server.log" 2>&1 < /dev/null & echo $! > "$COMFY_ROOT/.h3-higgsfield-server.pid")
    python3 "$ROOT/deploy/activate_h3.py" wait --url "$SERVER_URL" --timeout 240
else
    echo "Restarting the idle ComfyUI process to load H3 Higgsfield..."
    python3 "$ROOT/deploy/activate_h3.py" restart --url "$SERVER_URL" --timeout 240
fi
H3_ACCESS_TOKEN='' "$H3_PYTHON" "$ROOT/deploy/verify_h3_server.py" --url "$SERVER_URL"
echo "Open H3 Higgsfield: $SERVER_URL/extensions/h3_studio/index.html"
echo "For a remote local-only server, tunnel port $PORT from your computer or use your provider's authenticated ComfyUI URL."
