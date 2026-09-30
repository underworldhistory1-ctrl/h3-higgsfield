"""Keep a local ComfyUI worker alive and recover H3 decode after a worker crash.

Run from a separate process on the same OpenBayes container. A completed H3
sampler checkpoint is decoded once in a clean worker if the original worker
dies before saving its MP4. The checkpoint remains if recovery also fails.
"""

import argparse
import fcntl
import json
import pathlib
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request


STOP = False


def stop(_signum, _frame):
    global STOP
    STOP = True


def api(base, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(base + path, data=data,
                                     headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


def wait_ready(base, child, timeout=180):
    until = time.monotonic() + timeout
    while not STOP and time.monotonic() < until and child.poll() is None:
        try:
            api(base, "/queue")
            return True
        except (OSError, ValueError):
            time.sleep(2)
    return False


def recovery_graph(token):
    latent = ["1", 0]
    return {
        "1": {"class_type": "H3LoadSavedLatent", "inputs": {"token": token}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        "4": {"class_type": "VAEDecode", "inputs": {"samples": latent, "vae": ["2", 0]}},
        "5": {"class_type": "VAEDecodeAudio", "inputs": {"samples": latent, "vae": ["3", 0]}},
        "6": {"class_type": "CreateVideo", "inputs": {"images": ["4", 0], "fps": 24, "audio": ["5", 0]}},
        "7": {"class_type": "H3SaveVideo", "inputs": {
            "video": ["6", 0], "filename_prefix": "video/h3_studio_" + token}},
    }


def recover(comfy, base):
    latent_dir = comfy / "output" / "latent"
    if not latent_dir.exists():
        return
    for checkpoint in sorted(latent_dir.glob("h3_studio_*.safetensors")):
        token = checkpoint.stem.removeprefix("h3_studio_")
        if len(token) != 12 or any(char not in "0123456789abcdef" for char in token):
            continue
        if list((comfy / "output" / "video").glob(f"h3_studio_{token}_*.mp4")):
            checkpoint.unlink(missing_ok=True)
            continue
        marker = checkpoint.with_suffix(".recovery-attempted")
        if marker.exists():
            continue
        marker.write_text("decode-only recovery submitted\n", encoding="utf-8")
        try:
            result = api(base, "/prompt", {"prompt": recovery_graph(token)})
            if "prompt_id" not in result:
                raise RuntimeError(f"Recovery prompt rejected: {result}")
            print(f"Recovery queued for {token}: {result['prompt_id']}", flush=True)
        except (OSError, ValueError, RuntimeError) as error:
            print(f"Recovery could not be queued for {token}: {error}", file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy-root", type=pathlib.Path, required=True)
    parser.add_argument("--port", type=int, default=8188)
    args = parser.parse_args()
    comfy = args.comfy_root.resolve()
    if not (comfy / "main.py").is_file():
        parser.error("ComfyUI main.py was not found")
    python = comfy / ".venv" / "bin" / "python"
    if not python.is_file():
        parser.error("ComfyUI Python environment was not found")
    lock_path = comfy / ".h3-supervisor.lock"
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("An H3 supervisor already owns this ComfyUI checkout")
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        base = f"http://127.0.0.1:{args.port}"
        log_path = comfy / "h3-higgsfield-server.log"
        recovering = False
        while not STOP:
            with log_path.open("a", encoding="utf-8") as log:
                child = subprocess.Popen([
                    str(python), "main.py", "--listen", "127.0.0.1", "--port", str(args.port),
                    "--cache-none", "--fp16-intermediates",
                ], cwd=comfy, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                print(f"ComfyUI started: PID {child.pid}", flush=True)
                if wait_ready(base, child) and recovering:
                    recover(comfy, base)
                while not STOP and child.poll() is None:
                    time.sleep(2)
                if STOP:
                    child.terminate()
                    try:
                        child.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        child.kill()
                    break
                print(f"ComfyUI exited with code {child.returncode}; restarting", flush=True)
            recovering = True
            time.sleep(5)


if __name__ == "__main__":
    main()
