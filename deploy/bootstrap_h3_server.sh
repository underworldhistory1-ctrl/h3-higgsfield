#!/usr/bin/env bash
# Prepare a fresh ComfyUI server for the H3 Studio workflow.
# Safe to rerun: existing workflows stay intact; this bundle's extension files refresh.
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage: bootstrap_h3_server.sh /path/to/ComfyUI [--t2v-only] [--without-style-loras] [--without-speed-options]

Installs H3 Studio, downloads FL2VA and Ref2VA model files, and copies the
quality and short-smoke workflows into ComfyUI's default workflow folder. Spectrum,
MotionCache and the H3 Turbo LoRA are prepared by default but remain off in the UI. Model downloads
resume/reuse through Hugging Face and should target persistent storage.
EOF
}

COMFY_ROOT=""
WITH_REF2VA=1
WITH_OPTIONAL_LORAS=1
WITH_SPEED_OPTIONS=1
while (($#)); do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --with-ref2va) WITH_REF2VA=1; shift ;;
        --t2v-only) WITH_REF2VA=0; shift ;;
        --with-optional-loras) WITH_OPTIONAL_LORAS=1; shift ;;
        --without-style-loras) WITH_OPTIONAL_LORAS=0; shift ;;
        --without-speed-options) WITH_SPEED_OPTIONS=0; shift ;;
        -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
        *)
            if [[ -n "$COMFY_ROOT" ]]; then
                echo "Only one ComfyUI path is allowed." >&2; usage >&2; exit 2
            fi
            COMFY_ROOT="$1"; shift ;;
    esac
done

if [[ -z "$COMFY_ROOT" ]]; then usage >&2; exit 2; fi
COMFY_ROOT="$(cd "$COMFY_ROOT" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNTIME_FILES=(
    __init__.py h3_video_save.py qwen_image.py LICENSE README.md
    web/index.html web/studio.js web/image.html web/image-studio.js
    workflows/h3_t2v_ui.json workflows/h3_t2v_api.json
    workflows/h3_t2v_smoke_ui.json workflows/h3_t2v_smoke_api.json
    scripts/verify_h3_video.py
    deploy/activate_h3.py deploy/bootstrap_h3_server.sh
    deploy/download_h3_models.sh deploy/download_optional_loras.py deploy/download_qwen_image_models.py
    deploy/make_h3_landing.py deploy/verify_h3_server.py
    docs/COMPATIBILITY_MATRIX_AR.md docs/GRAPH_MAP.md docs/UX_FLOW.md
)
if [[ ! -f "$COMFY_ROOT/main.py" || ! -d "$COMFY_ROOT/comfy_extras" ]]; then
    echo "Not a ComfyUI checkout: $COMFY_ROOT" >&2
    exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1 \
        || ! command -v git >/dev/null 2>&1 || ! command -v tar >/dev/null 2>&1; then
    if [[ "$(id -u)" -eq 0 ]] && command -v apt-get >/dev/null 2>&1; then
        echo "Installing H3 system tools from the package manager..."
        DEBIAN_FRONTEND=noninteractive apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg git tar
    fi
    if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1 \
            || ! command -v git >/dev/null 2>&1 || ! command -v tar >/dev/null 2>&1; then
        echo "ffmpeg, ffprobe, git, and tar are required. Install them or run as root on an apt-based image." >&2
        exit 1
    fi
fi
encoder_list="$(ffmpeg -hide_banner -encoders 2>/dev/null)"
for encoder in libx264 aac; do
    if ! grep -Eq "^[[:space:]]+[VA][^[:space:]]*[[:space:]]+${encoder}[[:space:]]" <<< "$encoder_list"; then
        echo "FFmpeg is missing the ${encoder} encoder required to save H3 MP4 with audio." >&2
        exit 1
    fi
done

if [[ -n "${COMFY_PYTHON:-}" && -x "${COMFY_PYTHON}" ]]; then
    H3_PYTHON="$COMFY_PYTHON"
elif [[ -x "$COMFY_ROOT/.venv/bin/python" ]]; then
    H3_PYTHON="$COMFY_ROOT/.venv/bin/python"
elif [[ -x "$COMFY_ROOT/venv/bin/python" ]]; then
    H3_PYTHON="$COMFY_ROOT/venv/bin/python"
else
    H3_PYTHON="$(command -v python3 || true)"
fi
[[ -n "$H3_PYTHON" ]] || { echo "Python 3 is required for H3 Studio." >&2; exit 1; }
export COMFY_PYTHON="$H3_PYTHON"

H3_NODE_FILE="$COMFY_ROOT/comfy_extras/nodes_minimax_h3.py"
if [[ ! -f "$H3_NODE_FILE" ]] || ! grep -q 'MiniMaxH3ImageToVideo' "$H3_NODE_FILE"; then
    echo "This ComfyUI build does not include native MiniMax H3 nodes." >&2
    echo "Update ComfyUI to a build that registers the native MiniMax H3 nodes." >&2
    exit 1
fi
if [[ "$WITH_REF2VA" -eq 1 ]] && ! grep -q 'MiniMaxH3ReferenceToVideo' "$H3_NODE_FILE"; then
    echo "This ComfyUI build lacks the native MiniMax H3 reference node." >&2
    exit 1
fi
if [[ "$WITH_REF2VA" -eq 1 ]] && {
    [[ ! -f "$COMFY_ROOT/comfy_extras/nodes_video.py" ]] ||
    [[ ! -f "$COMFY_ROOT/comfy_extras/nodes_audio.py" ]] ||
    ! grep -q 'class LoadVideo' "$COMFY_ROOT/comfy_extras/nodes_video.py" ||
    ! grep -q 'class GetVideoComponents' "$COMFY_ROOT/comfy_extras/nodes_video.py" ||
    ! grep -q 'class LoadAudio' "$COMFY_ROOT/comfy_extras/nodes_audio.py"
}; then
    echo "This ComfyUI build lacks LoadVideo, GetVideoComponents, or LoadAudio." >&2
    exit 1
fi

CUSTOM_NODES="$COMFY_ROOT/custom_nodes"
H3_STUDIO="$CUSTOM_NODES/h3_studio"
mkdir -p "$CUSTOM_NODES"
command -v tar >/dev/null || { echo "tar is required to install H3 Studio from this prepared bundle." >&2; exit 1; }
if [[ "$PROJECT_ROOT" == "$H3_STUDIO" ]]; then
    echo "Run the installer from a separate Git clone, not from the installed custom_nodes/h3_studio directory." >&2
    exit 1
fi
for source_file in "${RUNTIME_FILES[@]}"; do
    [[ -f "$PROJECT_ROOT/$source_file" ]] || { echo "Missing installer file: $source_file" >&2; exit 1; }
done
if [[ -e "$H3_STUDIO" ]]; then
    if [[ ! -f "$H3_STUDIO/__init__.py" || ! -f "$H3_STUDIO/web/index.html" ]]; then
        echo "Refusing to overwrite existing path without a recognizable H3 Studio install: $H3_STUDIO" >&2
        exit 1
    fi
    BACKUP="$H3_STUDIO/.h3-backup/$(date +%Y%m%d-%H%M%S)-$$"
    mkdir -p "$BACKUP/web"
    cp "$H3_STUDIO/__init__.py" "$BACKUP/__init__.py"
    cp "$H3_STUDIO/web/index.html" "$BACKUP/web/index.html"
    if [[ -f "$H3_STUDIO/web/studio.js" ]]; then
        cp "$H3_STUDIO/web/studio.js" "$BACKUP/web/studio.js"
    fi
    tar -cf - -C "$PROJECT_ROOT" "${RUNTIME_FILES[@]}" | tar -xf - -C "$H3_STUDIO"
    echo "Updated H3 Studio from the prepared bundle; previous UI/backend: $BACKUP"
else
    mkdir -p "$H3_STUDIO"
    tar -cf - -C "$PROJECT_ROOT" "${RUNTIME_FILES[@]}" | tar -xf - -C "$H3_STUDIO"
    echo "Installed the prepared H3 Studio bundle: $H3_STUDIO"
fi

"$H3_PYTHON" "$H3_STUDIO/deploy/make_h3_landing.py"

install_speed_node() {
    local name="$1" url="$2" revision="$3"
    local target="$CUSTOM_NODES/$name"
    command -v git >/dev/null || { echo "git is required to install speed options." >&2; exit 1; }
    if [[ -e "$target" && ! -d "$target/.git" ]]; then
        echo "Speed node path exists but is not a Git checkout: $target" >&2; exit 1
    fi
    if [[ ! -d "$target/.git" ]]; then
        mkdir -p "$target"
        git -C "$target" init -q
        git -C "$target" remote add origin "$url"
    fi
    local origin
    origin="$(git -C "$target" remote get-url origin)"
    [[ "$origin" == "$url" ]] || { echo "Unexpected speed node origin in $target: $origin" >&2; exit 1; }
    local current
    current="$(git -C "$target" rev-parse HEAD 2>/dev/null || true)"
    if [[ "$current" != "$revision" ]]; then
        [[ -z "$(git -C "$target" status --porcelain)" ]] || {
            echo "Speed node has local edits; refusing to replace them: $target" >&2; exit 1;
        }
        git -C "$target" fetch --depth 1 origin "$revision"
        git -C "$target" checkout --detach -q FETCH_HEAD
    fi
    [[ "$(git -C "$target" rev-parse HEAD)" == "$revision" ]] || {
        echo "Could not pin speed node $name" >&2; exit 1;
    }
    echo "Prepared optional speed node: $name ($revision)"
}

if [[ "$WITH_SPEED_OPTIONS" -eq 1 ]]; then
    install_speed_node "ComfyUI-Spectrum-MiniMax-H3" \
        "https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3.git" \
        "5161f0457bc8c52535212d6783eee73f439e1537"
    install_speed_node "ComfyUI-MiniMax-H3-MotionCache" \
        "https://github.com/starsFriday/ComfyUI-MiniMax-H3-MotionCache.git" \
        "bc2894102b2486661884371259a27080b0b137bf"
fi

echo "Downloading/reusing H3 T2V model files into persistent ComfyUI storage..."
DOWNLOAD_ARGS=("$COMFY_ROOT")
if [[ "$WITH_REF2VA" -eq 0 ]]; then DOWNLOAD_ARGS+=(--t2v-only); fi
if [[ "$WITH_SPEED_OPTIONS" -eq 1 ]]; then DOWNLOAD_ARGS+=(--with-speed-lora); fi
bash "$H3_STUDIO/deploy/download_h3_models.sh" "${DOWNLOAD_ARGS[@]}"
if [[ "$WITH_OPTIONAL_LORAS" -eq 1 ]]; then
    "$H3_PYTHON" "$H3_STUDIO/deploy/download_optional_loras.py" "$COMFY_ROOT"
fi

WORKFLOWS="$COMFY_ROOT/user/default/workflows"
mkdir -p "$WORKFLOWS"
for name in h3_t2v_ui.json h3_t2v_api.json h3_t2v_smoke_ui.json h3_t2v_smoke_api.json; do
    src="$PROJECT_ROOT/workflows/$name"
    dst="$WORKFLOWS/$name"
    if [[ -e "$dst" ]]; then
        if cmp -s "$src" "$dst"; then
            echo "Workflow already installed: $dst"
        else
            prepared="${dst%.json}_prepared.json"
            if [[ -e "$prepared" ]]; then
                if cmp -s "$src" "$prepared"; then
                    echo "Prepared workflow already installed: $prepared"
                else
                    echo "Keeping both existing workflows; prepared workflow not overwritten: $prepared" >&2
                fi
            else
                cp "$src" "$prepared"
                echo "Kept existing workflow and installed prepared copy: $prepared"
            fi
        fi
    else
        cp "$src" "$dst"
        echo "Installed workflow: $dst"
    fi
done

echo
echo "H3 setup files are ready. Restart ComfyUI once to load the H3 Studio page."
echo "Native H3 nodes were found; model files and workflow copies were checked."
echo "After restart, open: http://<server>:8188/extensions/h3_studio/index.html"
