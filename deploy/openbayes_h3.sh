#!/usr/bin/env bash
# ==============================================================================
#  h3 — one-command bootstrap for MiniMax H3 / Higgsfield on any Hyper.ai server
#      attached to the same persistent volume (/output) and dataset (/input0).
# ------------------------------------------------------------------------------
#  What this does, from a COMPLETELY FRESH container (nothing installed):
#    1. verifies the persistent volume + dataset are mounted
#    2. checks the tools needed by the persistent stack
#    3. installs the few OS tools the stack needs (only if missing)
#    4. re-links the weights  /output/h3-stack/models -> /input0/h3-models
#    5. re-links ComfyUI models and re-installs the H3 Studio extension
#    6. checks the pinned model set by exact byte size
#    7. starts ComfyUI and waits for the UI
#    8. prints the UI link (via the built-in tunnel helper, if asked)
#
#  Usage:
#     bash /output/h3-stack/h3            # full bootstrap + start
#     bash /output/h3-stack/h3 status     # state only
#     bash /output/h3-stack/h3 stop       # stop the server
#     bash /output/h3-stack/h3 verify     # run the repo's own verifier
#     bash /output/h3-stack/h3 link       # just re-create all symlinks
#     bash /output/h3-stack/h3 models     # model inventory + size check
#     bash /output/h3-stack/h3 logs       # tail the server log
#     bash /output/h3-stack/h3 doctor     # full environment diagnosis
#
#  Safe to re-run when no render is queued or running; it refuses to interrupt one.
# ==============================================================================
set -uo pipefail

STACK="/output/h3-stack"
REPO="$STACK/repo/minimax-h3-higgsfield"
COMFY="$STACK/ComfyUI/ComfyUI"
MODELS="$STACK/models"
MODELS_REAL="${MODELS_REAL:-/input0/h3-models}"
VENV="$COMFY/.venv"
PY="$VENV/bin/python"
H3_STUDIO="$COMFY/custom_nodes/h3_studio"
PIDFILE="$COMFY/.h3-server.pid"
LOGFILE="$STACK/logs/comfyui.log"
PORT="${PORT:-8188}"
BIND="${BIND:-127.0.0.1}"
UI_PATH="/extensions/h3_studio/index.html"

RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YEL=$'\033[1;33m'; CYN=$'\033[0;36m'
BLD=$'\033[1m'; RST=$'\033[0m'
say()  { printf '%s[*]%s %s\n' "$CYN" "$RST" "$*"; }
ok()   { printf '%s[+]%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s[!]%s %s\n' "$YEL" "$RST" "$*" >&2; }
die()  { printf '%s[x]%s %s\n' "$RED" "$RST" "$*" >&2; exit 1; }
hr()   { printf '%s\n' "------------------------------------------------------------"; }

# Expected pinned sizes (bytes) — used for a fast, exact presence check.
declare -A EXPECT=(
  ["diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors"]=20970379616
  ["diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors"]=20970379616
  ["text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"]=15687142551
  ["vae/minimax_h3_video_vae_fp16.safetensors"]=5207808496
  ["vae/minimax_h3_audio_vae_fp32.safetensors"]=605254808
)

# ---------------------------------------------------------------- 1. mounts
check_mounts() {
    local bad=0
    if [[ ! -d "$STACK" ]]; then
        die "Persistent stack not found at $STACK.
     -> Is the storage volume (/output) attached to this job?"
    fi
    ok "persistent volume : $STACK"
    if [[ -d "$MODELS_REAL" ]]; then
        ok "dataset weights   : $MODELS_REAL"
    else
        warn "dataset weights NOT found at $MODELS_REAL"
        warn "  -> attach the dataset (input0) to this job."
        bad=1
    fi
    [[ -x "$PY" ]] || die "ComfyUI python env missing at $VENV. The volume may be incomplete."
    return $bad
}

# --------------------------------------------------------- 3. OS packages
ensure_tools() {
    local need=()
    for t in git git-lfs ffmpeg aria2c rsync curl; do
        command -v "$t" >/dev/null 2>&1 || need+=("$t")
    done
    if (( ${#need[@]} == 0 )); then
        ok "OS tools present"
        return
    fi
    say "installing missing OS tools: ${need[*]}"
    export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
    ( apt-get update -qq && apt-get install -y -qq --force-confold "${need[@]}" ) \
        >/dev/null 2>&1 && ok "OS tools installed" || warn "OS tool install had errors (continuing)"
}

# --------------------------------------------------------- 4. weight links
link_weights() {
    if [[ -d "$MODELS_REAL" ]]; then
        if [[ -L "$MODELS" ]]; then
            ln -sfn "$MODELS_REAL" "$MODELS"
        elif [[ -d "$MODELS" ]]; then
            if [[ -n "$(ls -A "$MODELS" 2>/dev/null)" ]]; then
                say "relocating weights from $MODELS to $MODELS_REAL"
                for d in "$MODELS"/*; do
                    [[ -e "$d" ]] || continue
                    b="$(basename "$d")"
                    if [[ -e "$MODELS_REAL/$b" ]]; then
                        mkdir -p "$MODELS_REAL/$b"
                        for f in "$d"/*; do
                            [[ -e "$f" ]] || continue
                            fb="$(basename "$f")"
                            [[ -e "$MODELS_REAL/$b/$fb" ]] || mv "$f" "$MODELS_REAL/$b/$fb"
                        done
                        rmdir "$d" 2>/dev/null || true
                    else
                        mv "$d" "$MODELS_REAL/$b"
                    fi
                done
                rmdir "$MODELS" 2>/dev/null || true
            else
                rm -rf "$MODELS" 2>/dev/null || true
            fi
            ln -sfn "$MODELS_REAL" "$MODELS"
        else
            ln -sfn "$MODELS_REAL" "$MODELS"
        fi
        ok "weights  $MODELS -> $MODELS_REAL"
    fi

    if [[ -e "$MODELS" ]]; then
        if [[ ! -L "$COMFY/models" ]]; then
            if [[ -d "$COMFY/models" && -n "$(ls -A "$COMFY/models" 2>/dev/null)" ]]; then
                cp -an "$(readlink -f "$MODELS")/." "$COMFY/models/" 2>/dev/null || true
            else
                rm -rf "$COMFY/models" 2>/dev/null || true
                ln -sfn "$(readlink -f "$MODELS")" "$COMFY/models"
            fi
        else
            ln -sfn "$(readlink -f "$MODELS")" "$COMFY/models"
        fi
        ok "comfy    $COMFY/models -> $(readlink -f "$COMFY/models")"
    fi
}

# ---------------------------------------------------- 5. extension install
install_extension() {
    [[ -d "$REPO" ]] || die "Repo missing at $REPO"
    mkdir -p "$H3_STUDIO"
    local files=(
        __init__.py h3_video_save.py qwen_image.py LICENSE README.md
        web/index.html web/studio.js web/image.html web/image-studio.js
        workflows/h3_t2v_ui.json workflows/h3_t2v_api.json
        workflows/h3_t2v_smoke_ui.json workflows/h3_t2v_smoke_api.json
        scripts/verify_h3_video.py
        deploy/bootstrap_h3_server.sh deploy/download_h3_models.sh
        deploy/download_optional_loras.py deploy/download_qwen_image_models.py
        deploy/activate_h3.py deploy/make_h3_landing.py deploy/verify_h3_server.py
        docs/COMPATIBILITY_MATRIX_AR.md docs/GRAPH_MAP.md docs/UX_FLOW.md
    )
    for f in "${files[@]}"; do
        [[ -f "$REPO/$f" ]] || die "Required repo file missing: $f"
        mkdir -p "$H3_STUDIO/$(dirname "$f")"
        cp -a "$REPO/$f" "$H3_STUDIO/$f" || die "Could not install $f"
    done
    [[ -f "$H3_STUDIO/web/index.html" ]] \
        || die "Extension files incomplete at $H3_STUDIO"
    ok "extension installed at $H3_STUDIO"
}

install_speed_nodes() {
    local name url revision target current
    while read -r name url revision; do
        [[ -n "$name" ]] || continue
        target="$COMFY/custom_nodes/$name"
        if [[ -e "$target" && ! -d "$target/.git" ]]; then
            die "Speed node path exists but is not a Git checkout: $target"
        fi
        if [[ ! -d "$target/.git" ]]; then
            git clone -q "$url" "$target" || die "Could not download $name"
        fi
        [[ "$(git -C "$target" remote get-url origin)" == "$url" ]] || die "Unexpected origin for $name"
        current="$(git -C "$target" rev-parse HEAD 2>/dev/null || true)"
        if [[ "$current" != "$revision" ]]; then
            [[ -z "$(git -C "$target" status --porcelain)" ]] || die "Local edits in $name; refusing to replace them"
            git -C "$target" fetch -q --depth 1 origin "$revision" || die "Could not fetch $name"
            git -C "$target" checkout --detach -q FETCH_HEAD || die "Could not pin $name"
        fi
        [[ "$(git -C "$target" rev-parse HEAD)" == "$revision" ]] || die "Wrong revision for $name"
        ok "speed node $name ready"
    done <<'NODES'
ComfyUI-Spectrum-MiniMax-H3 https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3.git 5161f0457bc8c52535212d6783eee73f439e1537
ComfyUI-MiniMax-H3-MotionCache https://github.com/starsFriday/ComfyUI-MiniMax-H3-MotionCache.git bc2894102b2486661884371259a27080b0b137bf
NODES
}

# -------------------------------------------------------- 6. model check
models() {
    local ok_n=0 miss_n=0
    printf '%s--- required models ---%s\n' "$BLD" "$RST"
    for rel in "${!EXPECT[@]}"; do
        local want="${EXPECT[$rel]}" got
        got=$(stat -c %s "$MODELS/$rel" 2>/dev/null || echo 0)
        if [[ "$got" == "$want" ]]; then
            printf '  %sOK  %s%s  %s\n' "$GRN" "$RST" "$rel" "$want"
            ok_n=$((ok_n+1))
        else
            printf '  %sBAD %s  %s  (got %s want %s)\n' "$RED" "$RST" "$rel" "$got" "$want"
            miss_n=$((miss_n+1))
        fi
    done
    printf '  => %d/%d present\n' "$ok_n" "${#EXPECT[@]}"
    (( miss_n == 0 )) || return 1
}

# ------------------------------------------------------------ helpers
is_running() {
    [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null
}

status() {
    printf '%s--- H3 Higgsfield ---%s\n' "$BLD" "$RST"
    printf '  stack      : %s\n' "$STACK"
    [[ -d "$STACK" ]] && printf '  stack size : %s\n' "$(du -sh "$STACK" 2>/dev/null | cut -f1)"
    printf '  repo       : %s\n' "$([[ -f "$REPO/install.sh" ]] && echo present || echo MISSING)"
    printf '  ComfyUI    : %s\n' "$([[ -f "$COMFY/main.py" ]] && echo present || echo MISSING)"
    printf '  python env : %s\n' "$([[ -x "$PY" ]] && echo present || echo MISSING)"
    printf '  models     : %s\n' "$([[ -e "$MODELS" ]] && du -shL "$MODELS" 2>/dev/null | cut -f1 || echo MISSING)"
    if [[ -L "$MODELS" ]]; then
        if [[ -e "$MODELS" ]]; then
            printf '  weights    : %s (ok)\n' "$(readlink "$MODELS")"
        else
            printf '  weights    : %s %s(DANGLING - dataset not attached)%s\n' "$(readlink "$MODELS")" "$RED" "$RST"
        fi
    fi
    printf '  process    : %s\n' "$(is_running && echo "running (pid $(cat "$PIDFILE"))" || echo stopped)"
    local ip; ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    printf '  container IP: %s\n' "${ip:-?}"
    printf '  UI (inside): http://%s:%s%s\n' "${ip:-127.0.0.1}" "$PORT" "$UI_PATH"
    if curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORT$UI_PATH" 2>/dev/null | grep -qE '^(200|302)$'; then
        printf '  reachable  : %syes%s\n' "$GRN" "$RST"
    else
        printf '  reachable  : %sno%s\n' "$YEL" "$RST"
    fi
}

preflight() {
    [[ -f "$COMFY/main.py" ]] || die "ComfyUI not found at $COMFY"
    [[ -x "$PY" ]] || die "python env missing at $VENV"
    command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1 \
        || die "No working NVIDIA GPU detected"
    "$PY" -c 'import torch; assert torch.cuda.is_available()' 2>/dev/null \
        || die "PyTorch cannot see CUDA"
    mkdir -p "$STACK/logs" "$STACK/outputs"
}

start() {
    preflight
    if is_running || pgrep -f "[.]venv/bin/python main.py" >/dev/null 2>&1; then
        queue_idle
    fi
    if is_running; then
        say "stopping existing server (pid $(cat "$PIDFILE"))"
        kill "$(cat "$PIDFILE")" 2>/dev/null
        for _ in $(seq 1 20); do
            is_running || break; sleep 1
        done
        kill -9 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null
        rm -f "$PIDFILE"
    fi
    pkill -f "[.]venv/bin/python main.py" 2>/dev/null && sleep 2 || true

    export HF_HUB_DISABLE_XET=1
    export HF_HOME="$STACK/cache/huggingface"
    export TORCH_HOME="$STACK/cache/torch"
    export COMFY_PYTHON="$PY"
    mkdir -p "$HF_HOME" "$TORCH_HOME"

    say "starting ComfyUI on $BIND:$PORT"
    ( cd "$COMFY" && nohup "$PY" main.py --listen "$BIND" --port "$PORT" \
        > "$LOGFILE" 2>&1 < /dev/null & echo $! > "$PIDFILE" )
    sleep 3
    ok "started (pid $(cat "$PIDFILE" 2>/dev/null)). log: $LOGFILE"
}

queue_idle() {
    local queue
    queue="$(curl -fsS --max-time 5 "http://127.0.0.1:$PORT/queue")" \
        || die "Could not check ComfyUI queue; refusing to interrupt it"
    "$PY" -c 'import json,sys; q=json.load(sys.stdin); sys.exit(bool(q.get("queue_running") or q.get("queue_pending")))' \
        <<< "$queue" || die "A render is queued or running; wait before restarting ComfyUI"
}

wait_ready() {
    local timeout="${1:-300}" waited=0
    say "waiting for the UI (max ${timeout}s)"
    while (( waited < timeout )); do
        if curl -sS -o /dev/null --max-time 4 "http://127.0.0.1:$PORT/queue" 2>/dev/null; then
            local code
            code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
                   "http://127.0.0.1:$PORT$UI_PATH" 2>/dev/null)
            if [[ "$code" =~ ^(200|302)$ ]]; then
                hr
                ok "UI is UP"
                hr
                return 0
            fi
        fi
        sleep 5; waited=$((waited+5))
        (( waited % 30 == 0 )) && say "…still starting (${waited}s)"
    done
    warn "UI did not respond in ${timeout}s; last log:"
    tail -n 25 "$LOGFILE" 2>/dev/null
    return 1
}

verify() {
    local v="$REPO/deploy/verify_h3_server.py"
    [[ -f "$v" ]] || { warn "verifier not found"; return 1; }
    QWEN_IMAGE_PROFILES="${QWEN_IMAGE_PROFILES:-int8}" "$PY" "$v" --url "http://127.0.0.1:$PORT" 2>&1
}

doctor() {
    hr; printf '%sEnvironment diagnosis%s\n' "$BLD" "$RST"; hr
    printf 'hostname      : %s\n' "$(hostname)"
    printf 'container IP  : %s\n' "$(hostname -I 2>/dev/null | awk '{print $1}')"
    printf '/output       : %s\n' "$([[ -d /output ]] && echo present || echo MISSING)"
    printf '/input0       : %s\n' "$([[ -d /input0 ]] && echo present || echo MISSING)"
    printf 'nvidia-smi    : %s\n' "$(command -v nvidia-smi >/dev/null && nvidia-smi -L 2>/dev/null | head -n1 || echo missing)"
    printf 'torch cuda    : %s\n' "$([[ -x "$PY" ]] && "$PY" -c 'import torch;print(torch.cuda.is_available())' 2>/dev/null || echo '?')"
    printf 'git/ffmpeg/aria2c: %s\n' "$(for t in git ffmpeg aria2c; do command -v $t >/dev/null && printf '%s ' $t; done)"
    hr
    models
    hr
}

# ---------------------------------------------------------------- main
case "${1:-up}" in
    up|start|"")
        hr; printf '%sH3 bootstrap%s\n' "$BLD" "$RST"; hr
        if is_running || pgrep -f "[.]venv/bin/python main.py" >/dev/null 2>&1; then queue_idle; fi
        check_mounts || die "Required persistent mounts are missing"
        ensure_tools
        link_weights; install_extension; install_speed_nodes
        models || die "Required H3 weights are incomplete; refusing to show a ready UI"
        start
        wait_ready 300 || exit 1
        verify || die "Server started, but model/node verification failed. Review the output above."
        hr; status; hr
        ok "Open the UI with the tunnel helper on your own machine:"
        printf '     python deploy/openbayes_tunnel.py --ssh-port YOUR_SSH_PORT\n'
        printf '     then browse http://127.0.0.1:8766%s\n' "$UI_PATH"
        ;;
    status)  status ;;
    stop)
        if is_running || pgrep -f "[.]venv/bin/python main.py" >/dev/null 2>&1; then queue_idle; fi
        if is_running; then kill "$(cat "$PIDFILE")" 2>/dev/null && ok "stopped"; rm -f "$PIDFILE"; else warn "not running"; fi
        pkill -f "[.]venv/bin/python main.py" 2>/dev/null || true
        ;;
    verify)  verify ;;
    link)    check_mounts; link_weights; install_extension ;;
    models)  models ;;
    logs)    tail -n "${2:-60}" "$LOGFILE" ;;
    doctor)  doctor ;;
    *)  die "unknown command: $1 (try: up|status|stop|verify|link|models|logs|doctor)" ;;
esac
