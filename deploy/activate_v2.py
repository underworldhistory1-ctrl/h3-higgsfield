"""Manually activate the isolated V2 ComfyUI profile after fail-closed checks.

This script is never scheduled. It reads production/LTX status, terminates only
the verified V2 standby process, then starts V2 without installing dependencies.
"""
import argparse
import json
import os
import pathlib
import select
import signal
import socket
import subprocess
import urllib.request

ROOT = pathlib.Path("/output/h3-studio-v2")
INTERPRETER = pathlib.Path("/output/h3-stack/ComfyUI/ComfyUI/.venv/bin/python")


class ActivationBlocked(RuntimeError):
    pass


def get_json(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        if response.status != 200:
            raise ActivationBlocked("A required service status is unavailable")
        content = response.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ActivationBlocked("A required service response is too large")
        return json.loads(content)


def gpu_flags(status):
    flags = []
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if "busy" in key.lower():
                    if type(item) is not bool:
                        raise ActivationBlocked("GPU busy status is not a boolean")
                    flags.append(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(status)
    if not isinstance(status, dict) or status.get("gpu_available") is not True or not flags or any(flags):
        raise ActivationBlocked("LTX GPU availability is not confirmed idle")


def preflight(read_json=get_json, run=subprocess.run, read_text=None):
    read_text = read_text or (lambda path: pathlib.Path(path).read_text(encoding="utf-8"))
    try:
        for port in (8188, 8189):
            queue = read_json(f"http://127.0.0.1:{port}/queue")
            if not isinstance(queue, dict) or any(not isinstance(queue.get(key), list) or queue[key]
                                                   for key in ("queue_running", "queue_pending")):
                raise ActivationBlocked(f"Service {port} is busy or its queue status is unknown")
        gpu_flags(read_json("http://127.0.0.1:7860/api/gpu_status"))
        result = run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                     capture_output=True, text=True, check=True, timeout=10)
        rows = [row.strip() for row in result.stdout.splitlines() if row.strip()]
        if not rows:
            raise ActivationBlocked("NVIDIA GPU status is unavailable")
        for row in rows:
            utilization, memory = [float(value.strip()) for value in row.split(",")]
            if not 0 <= utilization <= 5 or not 0 <= memory <= 4096:
                raise ActivationBlocked("The shared GPU is still occupied")
        current = int(read_text("/sys/fs/cgroup/memory.current").strip())
        maximum = int(read_text("/sys/fs/cgroup/memory.max").strip())
        if maximum <= 0 or current < 0 or current > maximum * 0.8:
            raise ActivationBlocked("Cgroup memory usage exceeds the 80 percent activation limit")
    except ActivationBlocked:
        raise
    except Exception as error:
        raise ActivationBlocked("A required idle/resource check is unavailable; V2 was not activated") from error


def launch_command(root=ROOT, interpreter=INTERPRETER):
    return [str(interpreter), str(root / "ComfyUI" / "main.py"), "--listen", "127.0.0.1", "--port", "8190",
            "--cache-none", "--fp16-intermediates", "--input-directory", str(root / "data" / "input"),
            "--output-directory", str(root / "data" / "output"), "--temp-directory", str(root / "data" / "temp"),
            "--extra-model-paths-config", str(root / "extra_model_paths.yaml")]


def verify_standby_command(arguments, root=ROOT):
    if len(arguments) < 2 or not arguments[1].endswith("/deploy/v2_standby.py"):
        raise ActivationBlocked("Standby script is not the Python process entry point")
    script = pathlib.Path(arguments[1])
    if not script.is_absolute() or not script.resolve().is_relative_to(root.resolve()):
        raise ActivationBlocked("PID does not belong to the isolated V2 standby script")
    if not pathlib.Path(arguments[0]).name.startswith("python"):
        raise ActivationBlocked("Standby script is not the Python process entry point")
    port = None
    for index, value in enumerate(arguments):
        if value == "--port" and index + 1 < len(arguments):
            port = arguments[index + 1]
        elif value.startswith("--port="):
            port = value.split("=", 1)[1]
    if port != "8190":
        raise ActivationBlocked("PID is not the V2 standby process on port 8190")


def stop_owned_standby(pid_file, root=ROOT):
    try:
        pid = int(pathlib.Path(pid_file).read_text(encoding="utf-8").strip())
        if pid <= 1 or pid == os.getpid():
            raise ActivationBlocked("Invalid standby PID")
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise ActivationBlocked("Safe Linux PID ownership handles are unavailable")
        descriptor = os.pidfd_open(pid)
    except ActivationBlocked:
        raise
    except Exception as error:
        raise ActivationBlocked("Standby PID cannot be verified; no process was stopped") from error
    try:
        command = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8").split("\0")
        verify_standby_command([value for value in command if value], root)
        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        if not select.select([descriptor], [], [], 20)[0]:
            raise ActivationBlocked("V2 standby did not exit; full V2 was not started")
    except OSError as error:
        raise ActivationBlocked("Standby ownership changed or shutdown failed; full V2 was not started") from error
    finally:
        os.close(descriptor)
    pathlib.Path(pid_file).unlink(missing_ok=True)


def port_unused():
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 8190))
    except OSError as error:
        raise ActivationBlocked("Port 8190 is still in use; no additional server was launched") from error
    finally:
        probe.close()


def activate(standby_pid_file, root=ROOT, interpreter=INTERPRETER):
    for path in (interpreter, root / "ComfyUI" / "main.py", root / "extra_model_paths.yaml"):
        if not path.is_file():
            raise ActivationBlocked("V2 interpreter, checkout or model-path configuration is missing")
    preflight()
    stop_owned_standby(standby_pid_file, root)
    # Resource state can change while standby is exiting. Recheck before launch.
    preflight()
    port_unused()
    for name in ("input", "output", "temp"):
        (root / "data" / name).mkdir(parents=True, exist_ok=True)
    (root / "run").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    with (root / "logs" / "comfy-v2.log").open("ab") as log:
        process = subprocess.Popen(launch_command(root, interpreter), cwd=root / "ComfyUI",
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (root / "run" / "comfy-v2.pid").write_text(str(process.pid) + "\n", encoding="utf-8")
    return process.pid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standby-pid-file", default=str(ROOT / "run" / "standby.pid"))
    options = parser.parse_args()
    try:
        pid = activate(options.standby_pid_file)
    except ActivationBlocked as error:
        parser.exit(1, "Activation blocked: " + str(error) + "\n")
    print(f"V2 ComfyUI process started as PID {pid}; check its log and readiness before using inference.")


if __name__ == "__main__":
    main()
