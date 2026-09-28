# H3 Higgsfield — MiniMax H3 video studio

An independent, creator-friendly interface for **MiniMax H3 video with native audio**. ComfyUI runs behind the page; creators work with scenes, references, settings, a queue, and a video library instead of a node canvas. This project is not affiliated with Higgsfield.

![H3 Higgsfield reference-mode interface with a generated video](docs/demo/interface-references.png)

**[Watch the Spectrum demo](docs/demo/spectrum-no-lora.mp4)** · **[Watch the MotionCache demo](docs/demo/motioncache-no-lora.mp4)** · **[Install](#install-on-windows-or-linux)** · **[Ask a question](https://github.com/underworldhistory1-ctrl/minimax-h3-higgsfield/discussions)**

## See it in action

Two renders of the same comedy-club scene and dialogue: **Spectrum** and **MotionCache**. Both are 15 seconds with native audio, 1280 × 704 at 24 fps, 20 steps, and **no LoRAs**. Watch the clips and compare the results.

| Spectrum | MotionCache |
| --- | --- |
| [![Spectrum video preview](docs/demo/spectrum-preview.jpg)](docs/demo/spectrum-no-lora.mp4) | [![MotionCache video preview](docs/demo/motioncache-preview.jpg)](docs/demo/motioncache-no-lora.mp4) |
| [Watch with audio](docs/demo/spectrum-no-lora.mp4) | [Watch with audio](docs/demo/motioncache-no-lora.mp4) |

The published MP4s retain their video and audio streams; private prompt metadata was removed.

<details>
<summary>See the MotionCache settings and live render progress</summary>

![MotionCache selected in the H3 interface during generation](docs/demo/interface-motioncache.png)

</details>

## What you get

| Mode | Input | H3 path |
| --- | --- | --- |
| Text | Scene prompt | FL2VA |
| Frames | Prompt + start and/or end image | FL2VA |
| References | Prompt + named images, videos, or audio (`@name`) | Ref2VA |

- A single English UI for prompts, output size, duration, steps, render method, and optional LoRAs.
- Video references at other frame rates are converted to **24 fps** on upload; their playback speed and available soundtrack are retained. H3's combined video-reference limit is 15 seconds.
- Original quality by default. Spectrum, MotionCache, and the FL2VA Turbo LoRA are prepared as separate, optional choices; they can change the result. Installed LoRAs appear as optional switches.
- Native video and audio come from the same H3 sample. Audio is checked after saving; listening remains the final check.
- The H3 save node writes MP4 with H.264 and AAC; if PyAV fails to encode, it retries through the installed FFmpeg without rerunning the model.
- Upload and render progress, a changing time estimate, a queue, thumbnails, saved settings for each clip, and a library that survives page refreshes.
- Input videos cannot be mistaken for completed outputs: the UI accepts a result only from the Save Video node after the file appears on the server.

## Install on Windows or Linux

Both installers require an NVIDIA GPU with a working driver and room for roughly **63.4 GB of H3 weights** plus dependencies and outputs; 100 GB free is recommended for a first setup. An empty disk cannot be ready in seconds because the models must download.

### Windows (native PowerShell; no WSL)

Use the [official ComfyUI Portable NVIDIA build](https://github.com/Comfy-Org/ComfyUI/releases/latest) or an existing ComfyUI source checkout. The installer detects Portable's `python_embeded`, installs the H3 interface and optional methods, checks the queue, and opens the H3 page when ready. Git for Windows and an NVIDIA driver are required; the installer attempts to get missing FFmpeg tools through Windows Package Manager.

```powershell
git clone https://github.com/underworldhistory1-ctrl/minimax-h3-higgsfield.git
cd minimax-h3-higgsfield
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -ComfyRoot "C:\path\to\ComfyUI_windows_portable"
```

Point `-ComfyRoot` to the Portable parent folder **or** its `ComfyUI` subfolder. For an existing source install, pass its ComfyUI path and, if needed, `-ComfyPython "C:\path\to\python.exe"`. If ComfyUI is absent, omit `-ComfyRoot`: with Git and Python 3.12/3.13 installed, the script installs the pinned ComfyUI source into a sibling folder. A Portable build that lacks H3 nodes must first be updated with its official `update\update_comfyui.bat`; the installer preserves that build rather than replacing its files.

To inspect this Windows PC before setup, add `-Preflight` to the PowerShell command. It only reports local prerequisites and cached model sizes; it makes no changes or downloads.

If an already-running ComfyUI has no Manager restart endpoint, rerun with `-NoStart`, then restart ComfyUI normally. The H3 page is `http://127.0.0.1:8188/extensions/h3_studio/index.html`. The installer never restarts while jobs are running or queued.

### Linux (Bash)

```bash
git clone https://github.com/underworldhistory1-ctrl/minimax-h3-higgsfield.git
cd minimax-h3-higgsfield
bash install.sh
```

The Linux installer finds an existing ComfyUI or installs the pinned H3-capable revision containing the September 22 H3 VAE tile-blending fix, checks missing tools and Python packages, reuses or downloads verified model files, prepares the optional nodes and LoRAs, and opens **H3 Higgsfield** as the ComfyUI landing page. Both installers check the queue before restarting an existing server. If the provider's login or process manager blocks an automatic restart, the installer stops with a clear restart instruction rather than claiming the app is ready.

For a different Linux ComfyUI location, use `bash install.sh --comfy-root /path/to/ComfyUI`. Both new installs bind to `127.0.0.1:8188` by default. Reach a remote server through an SSH tunnel or an authenticated cloud proxy; only use `--bind 0.0.0.0` (Linux) or `-Bind 0.0.0.0` (Windows) behind access control. Once ready, open `/extensions/h3_studio/index.html` at your server address. The server root also redirects to this page; the Comfy node editor is reserved for maintenance at `/?view=nodes`.

Model downloads can require accepting the [MiniMax H3 license](https://huggingface.co/MiniMaxAI/MiniMax-H3) or Hugging Face access. The model weights, private reference files, personal video library, and server passwords are **not** in this Git repository; only the two public demo clips above are included. Re-running the installer checks and reuses valid cached weights. For a portable handoff or optional video-library restore, see [the server guide](deploy/CLOUD_BOOTSTRAP_AR.md).

## Run on Comfy Cloud (macOS or any machine without an NVIDIA GPU)

`cloud/bridge.py` serves the same H3 page locally and sends each render to [Comfy Cloud](https://cloud.comfy.org), which hosts the H3 weights and nodes. No local GPU or model download is needed. Finished MP4s are downloaded into `.cloud-data/output/video/`, which acts as the video library. A Comfy Cloud API key on a paid plan is required; renders draw from its credits.

```bash
uv venv cloud/.venv --python 3.13
uv pip install --python cloud/.venv/bin/python -r cloud/requirements.txt
COMFYUI_API_KEY=comfyui-… cloud/.venv/bin/python cloud/bridge.py   # or --env-file path/to/.env
# long-running: H3_CLOUD_ENV_FILE=path/to/.env pm2 startOrRestart cloud/pm2.config.cjs --only h3-cloud-bridge
```

Open `http://127.0.0.1:8188/`. Limits in Cloud mode:

- Comfy Cloud currently runs ComfyUI v0.37.4, which does **not** contain the September 22 H3 VAE tile-blending fix. The page therefore blocks generation until you start the bridge with `--accept-cloud-vae` (or `H3_ACCEPT_CLOUD_VAE=1` for PM2).
- Spectrum and MotionCache are not hosted on Cloud and stay disabled. The Cloud H3 LoRAs appear as optional switches; the pinned Turbo file is not hosted, so the Turbo method stays disabled.
- Reference files are uploaded to your Comfy Cloud account (50 MB per file). Deleting a clip in the page removes only the local copy.
- Execution time and billed GPU seconds for each job are appended to `.cloud-data/output/video/.h3-cloud-usage.jsonl`.

## What has been verified

On the project's RTX 5090 server, Original mode produced a short clip and a 15.1-second clip with decodable video and audio. A 25 fps clip with audio was accepted as a reference, converted to 24 fps, and cleaned up afterward. The interface's three modes map to their intended H3 nodes, and output files are checked before being shown as complete. New installs additionally require the upstream H3 VAE tile-blending fix before the UI permits generation. The [compatibility map](docs/COMPATIBILITY_MATRIX_AR.md) and [workflow map](docs/GRAPH_MAP.md) record the boundaries.

DLSS 5 Visual Enhancer is an optional Windows post-processing path and is not installed on Linux cloud servers. The studio first preserves source quality through the corrected H3 VAE, INT8 ConvRot weights, and high-quality MP4 saving; enhancement can be applied later on a compatible Windows RTX machine.

Windows installation is newly supported, but a complete first-time Windows GPU install has not yet been run by this project. The verified render evidence above is from Linux.

This evidence does not guarantee every prompt, LoRA combination, speed method, or a new GPU image. A video reference guides **new generation**; it is not a pixel-locked one-object edit. Exact local editing needs a separate masked inpainting workflow, which this UI does not claim to provide.

## Feedback

Share a render or ask a setup question in [Discussions](https://github.com/underworldhistory1-ctrl/minimax-h3-higgsfield/discussions). Report a reproducible problem in [Issues](https://github.com/underworldhistory1-ctrl/minimax-h3-higgsfield/issues), with the ComfyUI revision, GPU, selected mode, render method, and the error message. Remove access tokens and private prompts before posting logs.

Built on [ComfyUI](https://github.com/Comfy-Org/ComfyUI) and [MiniMax H3](https://huggingface.co/MiniMaxAI/MiniMax-H3). This repository's code is MIT-licensed; demo media, model weights, and third-party nodes have separate rights and licenses.
