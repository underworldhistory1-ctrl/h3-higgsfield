# H3 Higgsfield · V2

**V2 is the main release.** See [upgrade and rollback notes](docs/V2_UPGRADE_AR.md) before updating an existing instance. The installers include V2 projects, temporal guides and the separately pinned continuation engine. Keep one H3 Studio copy per ComfyUI process. Publishing this release does not move or replace your server data. See [review evidence](docs/lab/FOLLOWUP_REVIEW_2026-10-03.md) for tested paths and remaining quality boundaries.

An independent, creator-friendly interface for **MiniMax H3 video with native audio** and **Qwen Image 2.1 creation/editing**. ComfyUI runs behind the pages; creators never need the node canvas. This project is not affiliated with Higgsfield.

![H3 Higgsfield reference-mode interface with a generated video](docs/demo/interface-references.png)

**[Watch the Spectrum demo](docs/demo/spectrum-no-lora.mp4)** · **[Watch the MotionCache demo](docs/demo/motioncache-no-lora.mp4)** · **[Install](#install-on-windows-or-linux)** · **[Ask a question](https://github.com/underworldhistory1-ctrl/minimax-h3-higgsfield/discussions)**

Using an existing OpenBayes persistent workspace? Follow the [OpenBayes setup and recovery notes](docs/OPENBAYES.md). Its startup script checks model mounts, pinned speed nodes, the render queue, and live readiness before reporting success.

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

The top navigation separates **Video** and **Image**. Video retains the complete H3 workflow below. Image uses one creation workspace with optional named references (up to 10 images), INT8/BF16 availability checks, native aspect presets up to 4 MP, transparency, queue/progress recovery, output details, download, and deletion.

| Mode | Input | H3 path |
| --- | --- | --- |
| Text | Scene prompt | FL2VA |
| Frames | Prompt + start and/or end image | FL2VA |
| References | Prompt + named images, videos, or audio (`@name`) | Ref2VA |

- A single English UI for prompts, output size, duration, sampling steps (8–100; default 20), render method, and optional LoRAs. Enter a duration from 5 to 15.1 seconds; Studio shows the nearest H3-supported frame count and actual duration before submission.
- Attached references have an **Insert @name into prompt** button. The video workspace also shows named image, video, and audio references next to the prompt; start/end frames appear there as `@start` and `@end`.
- Video references at other frame rates are converted to **24 fps** on upload; their playback speed and available soundtrack are retained. H3's combined video-reference limit is 15 seconds.
- Original quality by default. Spectrum, MotionCache, and the FL2VA Turbo LoRA are prepared as separate, optional choices; they can change the result. Installed LoRAs appear as optional switches.
- Spectrum and MotionCache are selectable in References when their nodes are installed. The prepared Turbo LoRA targets FL2VA, so it is selectable only in Text and Frames.
- Native video and audio come from the same H3 sample. Audio is checked after saving; listening remains the final check.
- The H3 save node writes MP4 with H.264 and AAC; if PyAV fails to encode, it retries through the installed FFmpeg without rerunning the model.
- Upload and render progress, a changing time estimate, a queue, thumbnails, saved settings for each clip, and a library that survives page refreshes.
- Input videos cannot be mistaken for completed outputs: the UI accepts a result only from the Save Video node after the file appears on the server.

## Install on Windows or Linux

Both installers require an NVIDIA GPU with a working driver. H3 needs roughly **65.8 GB** including the prepared speed/LoRA files. Qwen Image is optional outside Salad: INT8 adds **17.3 GB**; BF16 adds **32.4 GB**, with a shared VAE. Use `QWEN_IMAGE_PROFILES=int8` (Linux) or `-QwenImageProfiles int8` (Windows) to install it. An empty disk cannot be ready in seconds because the models must download.

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

The Linux installer finds an existing ComfyUI or installs pinned revision `3b4c0b0e457cf0a51cf3038e0a6750d8f96ce251`. That revision contains the corrected H3 VAE tile decode and native Qwen Image 2.1 nodes. The installer checks those source markers before continuing, reuses or downloads verified model files, prepares the optional nodes and LoRAs, and opens **H3 Higgsfield** as the ComfyUI landing page.

For a different Linux ComfyUI location, use `bash install.sh --comfy-root /path/to/ComfyUI`. Both new installs bind to `127.0.0.1:8188` by default. Reach a remote server through an SSH tunnel or an authenticated cloud proxy; only use `--bind 0.0.0.0` (Linux) or `-Bind 0.0.0.0` (Windows) behind access control. Once ready, open `/extensions/h3_studio/index.html` at your server address. The server root also redirects to this page; the Comfy node editor is reserved for maintenance at `/?view=nodes`.

Model downloads can require accepting the [MiniMax H3 license](https://huggingface.co/MiniMaxAI/MiniMax-H3) or Hugging Face access. The model weights, private reference files, personal video library, and server passwords are **not** in this Git repository; only the two public demo clips above are included. Re-running the installer checks and reuses valid cached weights. For a portable handoff or optional video-library restore, see [the server guide](deploy/CLOUD_BOOTSTRAP_AR.md).

### SaladCloud

The repository includes a dedicated RTX 5090 image in `Dockerfile.salad`. It keeps all weights outside the container image, defaults to H3 plus Qwen INT8, verifies exact pinned sizes and SHA-256 values before reporting ready, protects both workspaces and WebSocket with one login, and can sync outputs to S3-compatible storage. See [the Salad deployment settings](deploy/SALAD_DEPLOYMENT.md).

## What has been verified

On the project's RTX 5090 server, Original mode produced a short clip and a 15.1-second clip with decodable video and audio. A 25 fps clip with audio was accepted as a reference, converted to 24 fps, and cleaned up afterward. New installs require the upstream H3 VAE tile fix before the UI permits generation. Qwen's graphs match the pinned ComfyUI 2.1 schemas and the source project's graph contract.

On one 39 GB system-RAM RTX 5090 workspace, both 15.1-second Original and 14.38-second MotionCache Ref2VA renders exhausted system RAM during video decoding after all 20 sampling steps. The latter sampled for 50 minutes before the process was killed, with no MP4 saved. Studio blocks 12.25 seconds and longer at 1280×704 or above on hosts with under 56 GB reported RAM, rather than risk another long render. This does not measure the GPU's 32 GB VRAM; for that full-length setting, at least 64 GB advertised system RAM is recommended, pending a successful validation render.

Qwen Image 2.1 weights use the Qwen Research License and are not licensed for commercial use. They are downloaded at runtime and are not redistributed by this repository.

DLSS 5 Visual Enhancer is an optional Windows post-processing path and is not installed on Linux cloud servers. The studio first preserves source quality through the corrected H3 VAE, INT8 ConvRot weights, and high-quality MP4 saving; enhancement can be applied later on a compatible Windows RTX machine.

Windows installation is newly supported, but a complete first-time Windows GPU install has not yet been run by this project. The verified render evidence above is from Linux.

This evidence does not guarantee every prompt, LoRA combination, speed method, or a new GPU image. A video reference guides **new generation**; it is not a pixel-locked one-object edit. Exact local editing needs a separate masked inpainting workflow, which this UI does not claim to provide.

## Feedback

Share a render or ask a setup question in [Discussions](https://github.com/underworldhistory1-ctrl/minimax-h3-higgsfield/discussions). Report a reproducible problem in [Issues](https://github.com/underworldhistory1-ctrl/minimax-h3-higgsfield/issues), with the ComfyUI revision, GPU, selected mode, render method, and the error message. Remove access tokens and private prompts before posting logs.

Built on [ComfyUI](https://github.com/Comfy-Org/ComfyUI) and [MiniMax H3](https://huggingface.co/MiniMaxAI/MiniMax-H3). This repository's code is MIT-licensed; demo media, model weights, and third-party nodes have separate rights and licenses.
