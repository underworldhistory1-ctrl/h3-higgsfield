#!/usr/bin/env bash
set -Eeuo pipefail

COMFY_ROOT="${COMFY_ROOT:-/opt/ComfyUI}"
DATA_ROOT="${H3_DATA_ROOT:-/workspace}"
H3_NODE="$COMFY_ROOT/custom_nodes/h3_studio"
REMOTE="${H3_STORAGE_REMOTE:-}"
SYNC_SECONDS="${H3_SYNC_SECONDS:-20}"
MODEL_SOURCE="${H3_MODEL_SOURCE:-huggingface}"
QWEN_IMAGE_PROFILES="${QWEN_IMAGE_PROFILES:-int8}"
READY_FILE=/run/h3/ready.flag

log() { printf '[h3-salad] %s\n' "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

log "Initialising the Salad data disk at $DATA_ROOT."

[[ -n "${H3_UI_PASSWORD:-}" ]] || die "Set H3_UI_PASSWORD as a Salad secret environment variable."
[[ "$SYNC_SECONDS" =~ ^[0-9]+$ ]] && ((SYNC_SECONDS >= 10)) || die "H3_SYNC_SECONDS must be an integer of at least 10."
[[ "$MODEL_SOURCE" == "huggingface" || "$MODEL_SOURCE" == "remote" ]] \
    || die "H3_MODEL_SOURCE must be huggingface or remote."
[[ "$QWEN_IMAGE_PROFILES" == "int8" || "$QWEN_IMAGE_PROFILES" == "bf16" || "$QWEN_IMAGE_PROFILES" == "int8,bf16" ]] \
    || die "QWEN_IMAGE_PROFILES must be int8, bf16, or int8,bf16."

mkdir -p "$DATA_ROOT/models" "$DATA_ROOT/input" "$DATA_ROOT/output/video" "$DATA_ROOT/output/images" \
    "$DATA_ROOT/user/default/workflows" "$DATA_ROOT/.cache/huggingface" /run/h3
for name in models input output user; do
    rm -rf "$COMFY_ROOT/$name"
    ln -s "$DATA_ROOT/$name" "$COMFY_ROOT/$name"
done

htpasswd -bc /run/h3/htpasswd "${H3_UI_USER:-h3}" "$H3_UI_PASSWORD" >/dev/null
chmod 600 /run/h3/htpasswd
cp /opt/h3-salad/nginx.conf.template /run/h3/nginx.conf
rm -f "$READY_FILE"

nginx -c /run/h3/nginx.conf -g 'daemon off;' &
NGINX_PID=$!
COMFY_PID=''
SYNC_PID=''

rclone_common=(--checkers 16 --transfers 8 --retries 5 --low-level-retries 20 \
    --contimeout 20s --timeout 10m --stats 30s)

restore_data() {
    [[ -n "$REMOTE" ]] || return 0
    command -v rclone >/dev/null || die "rclone is missing from the image."
    log "Restoring H3 state from external storage."
    rclone copy "$REMOTE/output/video" "$DATA_ROOT/output/video" "${rclone_common[@]}" || die "Could not restore output library."
    rclone copy "$REMOTE/output/images" "$DATA_ROOT/output/images" "${rclone_common[@]}" || die "Could not restore image library."
    rclone copy "$REMOTE/input" "$DATA_ROOT/input" "${rclone_common[@]}" || die "Could not restore inputs."
    rclone copy "$REMOTE/user/default/workflows" "$DATA_ROOT/user/default/workflows" "${rclone_common[@]}" || die "Could not restore workflows."
}

restore_models() {
    [[ -n "$REMOTE" ]] || return 0
    log "Restoring cached H3 models from external storage."
    rclone copy "$REMOTE/models" "$DATA_ROOT/models" "${rclone_common[@]}" || {
        [[ "$MODEL_SOURCE" == huggingface ]] || die "Could not download H3 models from H3_STORAGE_REMOTE."
        log "Remote model cache was unavailable; the pinned Hugging Face downloader will fill missing files."
    }
}

download_models() {
    if python "$H3_NODE/deploy/download_h3_models.py" "$COMFY_ROOT" --offline-check >/dev/null 2>&1 \
       && python "$H3_NODE/deploy/download_optional_loras.py" "$COMFY_ROOT" >/dev/null 2>&1 \
       && python "$H3_NODE/deploy/download_qwen_image_models.py" "$COMFY_ROOT" \
            --profiles "$QWEN_IMAGE_PROFILES" --offline-check >/dev/null 2>&1; then
        log "Verified cached H3 and Qwen Image model sets."
        return 0
    fi
    [[ "$MODEL_SOURCE" == huggingface ]] || die "Remote storage does not contain the complete verified model set."
    log "Downloading missing pinned H3 weights. This is about 65.8 GB on a new node."
    python "$H3_NODE/deploy/download_h3_models.py" "$COMFY_ROOT"
    python "$H3_NODE/deploy/download_optional_loras.py" "$COMFY_ROOT"
    log "Preparing Qwen Image profile(s): $QWEN_IMAGE_PROFILES."
    python "$H3_NODE/deploy/download_qwen_image_models.py" "$COMFY_ROOT" --profiles "$QWEN_IMAGE_PROFILES"
    if [[ -n "$REMOTE" && "${H3_SEED_REMOTE_MODELS:-0}" == 1 ]]; then
        log "Uploading the verified model cache to external storage."
        rclone copy "$DATA_ROOT/models" "$REMOTE/models" "${rclone_common[@]}"
    fi
}

sync_once() {
    [[ -n "$REMOTE" ]] || return 0
    rclone sync "$DATA_ROOT/output/video" "$REMOTE/output/video" "${rclone_common[@]}" \
        --exclude '*.tmp*' --exclude '*.part' --min-age 5s
    rclone sync "$DATA_ROOT/output/images" "$REMOTE/output/images" "${rclone_common[@]}" \
        --exclude '*.tmp*' --exclude '*.part' --min-age 5s
    rclone sync "$DATA_ROOT/input" "$REMOTE/input" "${rclone_common[@]}" \
        --exclude '*.tmp*' --exclude '*.part' --min-age 5s
    rclone sync "$DATA_ROOT/user/default/workflows" "$REMOTE/user/default/workflows" "${rclone_common[@]}" \
        --exclude '*.tmp*' --min-age 5s
}

sync_loop() {
    while true; do
        sleep "$SYNC_SECONDS"
        sync_once || log "State sync failed; it will retry in ${SYNC_SECONDS}s."
    done
}

cleanup() {
    rm -f "$READY_FILE"
    [[ -z "$SYNC_PID" ]] || kill "$SYNC_PID" 2>/dev/null || true
    sync_once || true
    [[ -z "$COMFY_PID" ]] || kill -TERM "$COMFY_PID" 2>/dev/null || true
    kill -TERM "$NGINX_PID" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

restore_data
restore_models
download_models

python - <<'PY'
import torch
if not torch.cuda.is_available():
    raise SystemExit("CUDA is unavailable to PyTorch")
major, minor = torch.cuda.get_device_capability()
if (major, minor) < (12, 0):
    raise SystemExit(f"Expected RTX 50-series sm_120 or newer, got sm_{major}{minor}")
print("[h3-salad] GPU:", torch.cuda.get_device_name(), "capability:", (major, minor), flush=True)
PY

log "Starting ComfyUI privately on 127.0.0.1:8188."
cd "$COMFY_ROOT"
python main.py --listen 127.0.0.1 --port 8188 &
COMFY_PID=$!

for _ in $(seq 1 360); do
    kill -0 "$COMFY_PID" 2>/dev/null || die "ComfyUI exited during startup."
    if curl -fsS http://127.0.0.1:8188/h3_studio/readiness > /tmp/h3-readiness.json 2>/dev/null \
       && python "$H3_NODE/deploy/verify_h3_server.py" --url http://127.0.0.1:8188 >/dev/null; then
        printf 'ready\n' > "$READY_FILE"
        log "H3 Studio is ready on port 8000."
        break
    fi
    sleep 2
done
[[ -f "$READY_FILE" ]] || die "H3 Studio did not become ready within 12 minutes after model preparation."

if [[ -n "$REMOTE" ]]; then
    sync_loop &
    SYNC_PID=$!
else
    log "WARNING: H3_STORAGE_REMOTE is unset. Models can be fetched from Hugging Face, but inputs and outputs are ephemeral."
fi

wait "$COMFY_PID"
