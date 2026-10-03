#!/usr/bin/env python3
"""Install H3 Higgsfield on native Windows ComfyUI (portable or source)."""

import argparse
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser


PROJECT = pathlib.Path(__file__).resolve().parent.parent
COMFY_REVISION = "3b4c0b0e457cf0a51cf3038e0a6750d8f96ce251"
H3_VAE_FIX_MARKER = "strip[..., :, x_idx[j]:x_idx[j] + x_len[j]]"
QWEN_NODE_MARKERS = ("TextEncodeQwenImage21", "QwenImage21Cache")
SPEED_NODES = (
    ("ComfyUI-Spectrum-MiniMax-H3", "https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3.git",
     "5161f0457bc8c52535212d6783eee73f439e1537"),
    ("ComfyUI-MiniMax-H3-MotionCache", "https://github.com/starsFriday/ComfyUI-MiniMax-H3-MotionCache.git",
     "bc2894102b2486661884371259a27080b0b137bf"),
)
RUNTIME_FILES = (
    "__init__.py", "h3_video_save.py", "qwen_image.py", "LICENSE", "README.md",
    "web/index.html", "web/studio.js", "web/image.html", "web/image-studio.js",
    "workflows/h3_t2v_ui.json", "workflows/h3_t2v_api.json",
    "workflows/h3_t2v_smoke_ui.json", "workflows/h3_t2v_smoke_api.json",
    "scripts/verify_h3_video.py", "deploy/activate_h3.py",
    "deploy/download_h3_models.py", "deploy/download_optional_loras.py", "deploy/download_qwen_image_models.py",
    "deploy/make_h3_landing.py", "deploy/verify_h3_server.py",
    "docs/COMPATIBILITY_MATRIX_AR.md", "docs/GRAPH_MAP.md", "docs/UX_FLOW.md",
)
RUNTIME_FILES += tuple((PROJECT / "deploy" / "v2_runtime_files.txt").read_text(encoding="utf-8").splitlines())
CONTEXT_NODE = ("ComfyUI-H3-Motion-Context-MultiRef", "https://github.com/seitanism/ComfyUI-H3-Motion-Context-MultiRef.git",
                "361624fb406b63eb6694442eac6c895fc1533a70")

WORKFLOWS = (
    "h3_t2v_ui.json", "h3_t2v_api.json", "h3_t2v_smoke_ui.json", "h3_t2v_smoke_api.json",
)


def call(*args, cwd=None, capture=False, env=None):
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env, text=True,
                            capture_output=capture, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise RuntimeError(f"Command failed ({result.returncode}): {args[0]} {detail}")
    return result.stdout.strip() if capture else ""


def executable(name, candidates=()):
    found = shutil.which(name)
    if found:
        return pathlib.Path(found)
    for item in candidates:
        path = pathlib.Path(item)
        if path.is_file():
            return path
    return None


def git_executable():
    git = executable("git", (r"C:\Program Files\Git\cmd\git.exe",
                             r"C:\Program Files (x86)\Git\cmd\git.exe"))
    if git:
        return git
    raise RuntimeError("Git is required. Install Git for Windows, then rerun this installer.")


def ffmpeg_tools():
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return
    links = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"
    if links.is_dir():
        os.environ["PATH"] = str(links) + os.pathsep + os.environ.get("PATH", "")
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    winget = shutil.which("winget")
    if winget:
        print("Installing FFmpeg and FFprobe with Windows Package Manager...", flush=True)
        call(winget, "install", "--id", "Gyan.FFmpeg", "--exact", "--source", "winget",
             "--accept-package-agreements", "--accept-source-agreements", "--silent")
        if links.is_dir():
            os.environ["PATH"] = str(links) + os.pathsep + os.environ.get("PATH", "")
        for base in (pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",):
            if base.is_dir():
                for package in base.glob("Gyan.FFmpeg*"):
                    for candidate in package.rglob("ffmpeg.exe"):
                        if (candidate.parent / "ffprobe.exe").is_file():
                            os.environ["PATH"] = str(candidate.parent) + os.pathsep + os.environ.get("PATH", "")
                            break
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and FFprobe are required. Install Gyan.FFmpeg with winget, open a new terminal, and rerun.")


def verify_ffmpeg_encoders():
    output = call(shutil.which("ffmpeg"), "-hide_banner", "-encoders", capture=True)
    available = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].startswith(("V", "A")):
            available.add(fields[1])
    missing = {"libx264", "aac"} - available
    if missing:
        raise RuntimeError("The installed FFmpeg lacks required MP4 encoders: " + ", ".join(sorted(missing)))


def resolve_root(raw):
    if raw:
        root = pathlib.Path(raw).expanduser().resolve()
        if (root / "ComfyUI" / "main.py").is_file():
            root /= "ComfyUI"
        return root
    home = pathlib.Path.home()
    for parent in PROJECT.parents:
        if (parent / "main.py").is_file() and (parent / "comfy_extras").is_dir():
            return parent.resolve()
    candidates = (
        PROJECT.parent / "ComfyUI", PROJECT.parent / "ComfyUI_windows_portable" / "ComfyUI",
        home / "ComfyUI", home / "Desktop" / "ComfyUI_windows_portable" / "ComfyUI",
        home / "Downloads" / "ComfyUI_windows_portable" / "ComfyUI",
    )
    for item in candidates:
        if (item / "main.py").is_file():
            return item.resolve()
    return (PROJECT.parent / "ComfyUI").resolve()


def queue_status(url):
    try:
        with urllib.request.urlopen(url + "/queue", timeout=6) as response:
            data = json.load(response)
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            return "offline"
        raise RuntimeError(f"Could not check ComfyUI queue: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Port is in use, but its /queue response is not ComfyUI: {exc}") from exc
    if not isinstance(data, dict) or not {"queue_running", "queue_pending"} <= data.keys():
        raise RuntimeError("The server on this port did not return a ComfyUI queue")
    running, pending = len(data["queue_running"]), len(data["queue_pending"])
    if running or pending:
        raise RuntimeError(f"ComfyUI has {running} running and {pending} queued job(s); rerun after they finish.")
    return "online"


def prepare_comfy(root, git):
    if (root / "main.py").is_file():
        print("Found ComfyUI:", root)
        return False
    if root.exists() and any(root.iterdir()):
        raise RuntimeError("Refusing to overwrite a non-ComfyUI directory: " + str(root))
    if root.exists():
        root.rmdir()
    root.parent.mkdir(parents=True, exist_ok=True)
    print("Installing the pinned, H3-capable ComfyUI source...", flush=True)
    call(git, "init", "-q", root)
    call(git, "-C", root, "remote", "add", "origin", "https://github.com/Comfy-Org/ComfyUI.git")
    call(git, "-C", root, "fetch", "--depth", "1", "origin", COMFY_REVISION)
    call(git, "-C", root, "checkout", "--detach", "-q", "FETCH_HEAD")
    return True


def ensure_native_nodes(root, git):
    node_file = root / "comfy_extras" / "nodes_minimax_h3.py"
    vae_file = root / "comfy" / "ldm" / "minimax" / "vae.py"
    qwen_file = root / "comfy_extras" / "nodes_qwen.py"
    native_ready = node_file.is_file() and "MiniMaxH3ReferenceToVideo" in node_file.read_text(encoding="utf-8")
    vae_ready = vae_file.is_file() and H3_VAE_FIX_MARKER in vae_file.read_text(encoding="utf-8")
    qwen_ready = qwen_file.is_file() and all(marker in qwen_file.read_text(encoding="utf-8") for marker in QWEN_NODE_MARKERS)
    if native_ready and vae_ready and qwen_ready:
        return
    if not (root / ".git").is_dir():
        raise RuntimeError("This ComfyUI Portable build lacks the verified H3 VAE fix or Qwen Image 2.1 nodes. Run its official update\\update_comfyui.bat, then rerun. Existing files were not changed.")
    if call(git, "-C", root, "status", "--porcelain", "--untracked-files=no", capture=True):
        raise RuntimeError("ComfyUI has local source edits; update it manually before installing H3.")
    call(git, "-C", root, "fetch", "--depth", "1", "origin", COMFY_REVISION)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    call(git, "-C", root, "branch", "h3-before-update-" + stamp, "HEAD")
    call(git, "-C", root, "checkout", "--detach", "-q", "FETCH_HEAD")
    if not node_file.is_file() or "MiniMaxH3ReferenceToVideo" not in node_file.read_text(encoding="utf-8"):
        raise RuntimeError("Pinned ComfyUI checkout still lacks the H3 reference node")
    if not vae_file.is_file() or H3_VAE_FIX_MARKER not in vae_file.read_text(encoding="utf-8"):
        raise RuntimeError("Pinned ComfyUI checkout still lacks the H3 VAE tile fix")
    if not qwen_file.is_file() or not all(marker in qwen_file.read_text(encoding="utf-8") for marker in QWEN_NODE_MARKERS):
        raise RuntimeError("Pinned ComfyUI checkout still lacks Qwen Image 2.1 nodes")


def select_python(root, override, fresh):
    if override:
        result = pathlib.Path(override).expanduser().resolve()
        if not result.is_file():
            raise RuntimeError("ComfyUI Python not found: " + str(result))
        return result, result.parent.name.lower() == "python_embeded"
    portable = root.parent / "python_embeded" / "python.exe"
    for candidate in (portable, root / ".venv" / "Scripts" / "python.exe",
                      root / "venv" / "Scripts" / "python.exe"):
        if candidate.is_file():
            return candidate, candidate == portable
    if fresh:
        call(sys.executable, "-m", "venv", root / ".venv")
        return root / ".venv" / "Scripts" / "python.exe", False
    return pathlib.Path(sys.executable).resolve(), False


def ensure_python_env(python, root, fresh):
    probe = subprocess.run([str(python), "-c",
                            "import torch, av, aiohttp, PIL, comfyui_frontend_package; assert torch.cuda.is_available()"],
                           capture_output=True, text=True)
    if probe.returncode == 0:
        return
    print("Installing missing ComfyUI Python dependencies...", flush=True)
    if fresh:
        call(python, "-m", "pip", "install", "--upgrade", "pip")
    cuda = subprocess.run([str(python), "-c", "import torch; assert torch.cuda.is_available()"],
                          capture_output=True, text=True)
    if cuda.returncode:
        print("Preparing CUDA PyTorch...", flush=True)
        call(python, "-m", "pip", "install", "torch", "torchvision", "torchaudio",
             "--index-url", "https://download.pytorch.org/whl/cu130")
    call(python, "-m", "pip", "install", "-r", root / "requirements.txt", "av")
    call(python, "-c", "import torch, av, aiohttp, PIL, comfyui_frontend_package; assert torch.cuda.is_available()")


def copy_runtime(root):
    target = root / "custom_nodes" / "h3_studio"
    if (root / "custom_nodes" / "h3_studio_v2").exists():
        raise RuntimeError("A separate V2 copy already exists in this ComfyUI process. Upgrade that copy in place; do not install duplicate H3 nodes.")
    if PROJECT.resolve() == target.resolve():
        raise RuntimeError("Run from a separate source checkout, outside custom_nodes/h3_studio")
    for name in RUNTIME_FILES:
        if not (PROJECT / name).is_file():
            raise RuntimeError("Missing H3 source file: " + name)
    if target.exists():
        if not (target / "__init__.py").is_file() or not (target / "web" / "index.html").is_file():
            raise RuntimeError("Existing custom_nodes/h3_studio is not a recognizable H3 install; not overwriting it")
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = target / ".h3-backup" / stamp
        for name in RUNTIME_FILES:
            old = target / name
            if old.is_file():
                saved = backup / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, saved)
        print("Previous H3 UI/backend saved at:", backup)
    for name in RUNTIME_FILES:
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT / name, dest)
    print("Installed H3 custom node and interface:", target)


def speed_node(root, git, name, url, revision):
    target = root / "custom_nodes" / name
    if target.exists() and not (target / ".git").is_dir():
        raise RuntimeError("Optional node path exists without Git; not overwriting: " + str(target))
    if not target.exists():
        target.mkdir(parents=True)
        call(git, "-C", target, "init", "-q")
        call(git, "-C", target, "remote", "add", "origin", url)
    if call(git, "-C", target, "remote", "get-url", "origin", capture=True) != url:
        raise RuntimeError("Unexpected Git origin for optional node: " + str(target))
    current = subprocess.run([str(git), "-C", str(target), "rev-parse", "HEAD"],
                             capture_output=True, text=True)
    if current.returncode == 0 and current.stdout.strip() == revision:
        print("Reusing optional node:", name)
        return
    if current.returncode == 0 and call(git, "-C", target, "status", "--porcelain", capture=True):
        raise RuntimeError("Optional node has local edits; refusing to replace: " + str(target))
    call(git, "-C", target, "fetch", "--depth", "1", "origin", revision)
    call(git, "-C", target, "checkout", "--detach", "-q", "FETCH_HEAD")
    if call(git, "-C", target, "rev-parse", "HEAD", capture=True) != revision:
        raise RuntimeError("Could not pin optional node: " + name)
    print("Prepared optional node:", name)


def install_workflows(root):
    target_dir = root / "user" / "default" / "workflows"
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in WORKFLOWS:
        source, target = PROJECT / "workflows" / name, target_dir / name
        if target.is_file() and target.read_bytes() == source.read_bytes():
            continue
        if target.exists():
            target = target.with_name(target.stem + "_prepared.json")
            if target.exists():
                if target.read_bytes() != source.read_bytes():
                    print("Keeping existing workflow and prepared copy:", target)
                continue
        shutil.copy2(source, target)
        print("Installed workflow:", target)


def preflight(args):
    """Inspect local prerequisites without changing files or using the network."""
    root = resolve_root(args.comfy_root)
    print("H3 WINDOWS PREFLIGHT (read-only; no downloads or changes)")
    gpu = executable("nvidia-smi", (pathlib.Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe",))
    if gpu:
        probe = subprocess.run([str(gpu), "-L"], capture_output=True, text=True)
        print("NVIDIA:", probe.stdout.strip() if probe.returncode == 0 else "driver command failed")
    else:
        print("NVIDIA: driver command not found")
    print("Git:", str(executable("git", (r"C:\Program Files\Git\cmd\git.exe",
                                   r"C:\Program Files (x86)\Git\cmd\git.exe"))) or "missing")
    print("FFmpeg:", shutil.which("ffmpeg") or "missing")
    print("FFprobe:", shutil.which("ffprobe") or "missing")
    if shutil.which("ffmpeg"):
        try:
            verify_ffmpeg_encoders()
            print("MP4 encoders: H.264 and AAC available")
        except (OSError, RuntimeError) as exc:
            print("MP4 encoders:", exc)
    print("ComfyUI:", root if (root / "main.py").is_file() else "not found at " + str(root))
    if (root / "main.py").is_file():
        native = root / "comfy_extras" / "nodes_minimax_h3.py"
        print("H3 native nodes:", "present" if native.is_file() and "MiniMaxH3ReferenceToVideo" in native.read_text(encoding="utf-8") else "missing")
        vae = root / "comfy" / "ldm" / "minimax" / "vae.py"
        print("H3 VAE tile fix:", "present" if vae.is_file() and H3_VAE_FIX_MARKER in vae.read_text(encoding="utf-8") else "missing")
        python, portable = select_python(root, args.comfy_python, False)
        probe = subprocess.run([str(python), "-c",
                                "import torch, av, aiohttp, PIL, comfyui_frontend_package; assert torch.cuda.is_available(); print(torch.__version__)"],
                               capture_output=True, text=True)
        print("ComfyUI Python:", python, "(portable)" if portable else "")
        print("Python dependencies and CUDA:", probe.stdout.strip() if probe.returncode == 0 else "incomplete or unavailable")
        models = root / "models"
        from download_h3_models import MODELS, TURBO_FILE, TURBO_SIZE
        for name, size in (*MODELS, ("loras/" + TURBO_FILE, TURBO_SIZE)):
            target = models / name
            actual = target.stat().st_size if target.is_file() else 0
            print("Model cache:", name, "size matches" if actual == size else f"missing/incomplete ({actual:,} of {size:,} bytes)")
    else:
        print("Fresh-install Python:", sys.executable, sys.version.split()[0])
        if not ((3, 12) <= sys.version_info[:2] <= (3, 13)):
            print("Fresh-install Python 3.12/3.13: needed")
    disk = root if root.exists() else next((p for p in root.parents if p.exists()), None)
    if disk:
        print("Disk free:", f"{shutil.disk_usage(disk).free / 1024**3:.1f} GiB")
    print("NO_DOWNLOADS_OR_CHANGES")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy-root")
    parser.add_argument("--comfy-python")
    parser.add_argument("--port", type=int, default=8188)
    parser.add_argument("--bind", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    parser.add_argument("--no-start", action="store_true")
    parser.add_argument("--qwen-image-profiles", default="", choices=("", "int8", "bf16", "int8,bf16"))
    parser.add_argument("--preflight", action="store_true", help="Read-only local check; never downloads or changes files")
    args = parser.parse_args()
    if not sys.platform.startswith("win"):
        parser.error("Use install.sh on Linux")
    if not 1 <= args.port <= 65535:
        parser.error("Invalid port")
    if args.preflight:
        return preflight(args)
    nvidia_smi = executable("nvidia-smi", (pathlib.Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe",))
    if not nvidia_smi:
        raise RuntimeError("An NVIDIA GPU and a working driver are required (nvidia-smi not found)")
    call(nvidia_smi, "-L")
    git = git_executable()
    ffmpeg_tools()
    verify_ffmpeg_encoders()
    root = resolve_root(args.comfy_root)
    url = f"http://127.0.0.1:{args.port}"
    status = queue_status(url)
    if status == "online" and not (root / "main.py").is_file():
        raise RuntimeError("ComfyUI is online but its folder was not found. Pass -ComfyRoot to its actual path.")
    if not (root / "main.py").is_file() and not ((3, 12) <= sys.version_info[:2] <= (3, 13)):
        raise RuntimeError("A fresh Windows install needs Python 3.12 or 3.13 for the prepared CUDA stack. Install Python 3.13 and rerun.")
    fresh = prepare_comfy(root, git)
    ensure_native_nodes(root, git)
    python, portable = select_python(root, args.comfy_python, fresh)
    ensure_python_env(python, root, fresh)
    free_gb = shutil.disk_usage(root).free / 1024**3
    if free_gb < 100:
        print(f"Warning: only {free_gb:.1f} GiB free; first-time H3 weights need roughly 100 GiB of free space.")
    copy_runtime(root)
    call(python, PROJECT / "deploy" / "make_h3_landing.py")
    for name, source, revision in (*SPEED_NODES, CONTEXT_NODE):
        speed_node(root, git, name, source, revision)
    if subprocess.run([str(python), "-c", "import huggingface_hub"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        call(python, "-m", "pip", "install", "huggingface_hub==1.32.0")
    call(python, PROJECT / "deploy" / "download_h3_models.py", root)
    call(python, PROJECT / "deploy" / "download_optional_loras.py", root)
    if args.qwen_image_profiles:
        call(python, PROJECT / "deploy" / "download_qwen_image_models.py", root,
             "--profiles", args.qwen_image_profiles)
    install_workflows(root)
    page = url + "/extensions/h3_studio/index.html"
    if args.no_start:
        print("Setup complete. Start/restart ComfyUI to load H3 Higgsfield:", page)
        return 0
    if status == "online":
        print("Restarting idle ComfyUI through ComfyUI Manager...", flush=True)
        call(python, PROJECT / "deploy" / "activate_h3.py", "restart", "--url", url, "--timeout", "240")
    else:
        log_path = root / "h3-higgsfield-server.log"
        log = log_path.open("a", encoding="utf-8")
        command = [str(python), "main.py", "--listen", args.bind, "--port", str(args.port)]
        if portable:
            command.append("--windows-standalone-build")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, creationflags=flags, close_fds=True,
                                env=os.environ.copy())
        log.close()
        (root / ".h3-higgsfield-server.pid").write_text(str(proc.pid), encoding="ascii")
        call(python, PROJECT / "deploy" / "activate_h3.py", "wait", "--url", url, "--timeout", "240")
    env = os.environ.copy()
    env["H3_ACCESS_TOKEN"] = ""
    env["QWEN_IMAGE_PROFILES"] = args.qwen_image_profiles
    call(python, PROJECT / "deploy" / "verify_h3_server.py", "--url", url, env=env)
    print("H3 Higgsfield is ready:", page)
    webbrowser.open(page)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("H3_WINDOWS_SETUP_ERROR:", exc, file=sys.stderr)
        raise SystemExit(1)
