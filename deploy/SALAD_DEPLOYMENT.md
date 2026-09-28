# SaladCloud deployment

The Salad image contains both workspaces, pinned ComfyUI, CUDA 12.8 PyTorch,
FFmpeg, Spectrum and MotionCache. It deliberately contains no model weights.
The default runtime downloads the 65.84 GB prepared H3 set and the 17.28 GB
Qwen Image INT8 set onto the assigned machine. This keeps the image below
Salad's 35 GB compressed-image limit.

The published `linux/amd64` image built from commit `9d81246` is **5.307 GB
compressed** (14 layers), digest
`sha256:d39913847efeef05f6a4c8da91a37d69f6d309bf57610b2fbf9cbc0d6b0004b0`.
The GHCR package is public, so Salad does not need registry credentials.

## Container Group

- Image: `ghcr.io/underworldhistory1-ctrl/minimax-h3-higgsfield:salad`
- GPU: RTX 5090 32 GB
- Replicas: 1
- Priority: Lowest (Low is more reliable for interactive work)
- RAM: 32 GB minimum; 64 GB preferred
- CPU: 8 vCPU minimum
- Ephemeral storage: 100 GB minimum for H3 + Qwen INT8; 150 GB for INT8 + BF16
- Container Gateway port: 8000
- Gateway authentication: disabled; H3 Studio supplies its own HTTP Basic Auth
- Request concurrency: 1

Health probes:

- Startup: `GET /started`
- Liveness: `GET /live`
- Readiness: `GET /ready`
- Allow at least 3 hours for the startup probe on a cold, slow node.

## Required secret

- `H3_UI_PASSWORD`: a long password used to protect the entire UI and WebSocket.

Optional variables:

- `H3_UI_USER` (default `h3`)
- `HF_TOKEN` if Hugging Face requires accepted-license authentication
- `H3_MODEL_SOURCE=huggingface` (default) or `remote`
- `H3_STORAGE_REMOTE`, for example `r2:h3-studio` (recommended for durable inputs and outputs, not required for first startup)
- `H3_SYNC_SECONDS` (default `20`, minimum `10`)
- `H3_SEED_REMOTE_MODELS=1` to upload a newly downloaded verified model cache
- `QWEN_IMAGE_PROFILES` (`int8` default, `bf16`, or `int8,bf16`)

For Rclone, inject its standard runtime configuration as Salad secrets. Example
for Cloudflare R2:

```text
RCLONE_CONFIG_R2_TYPE=s3
RCLONE_CONFIG_R2_PROVIDER=Cloudflare
RCLONE_CONFIG_R2_ACCESS_KEY_ID=...
RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=...
RCLONE_CONFIG_R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
RCLONE_CONFIG_R2_ACL=private
```

Never commit these values. The simplest first deployment uses Hugging Face as
the pinned model origin and only needs `H3_UI_PASSWORD` (plus `HF_TOKEN` if the
license gate requests it). Adding `H3_STORAGE_REMOTE` does not change H3 or its
graphs; it makes inputs, finished videos and generation metadata durable across
node replacement. Without it those user files remain ephemeral.

## Cold start

Each newly allocated machine downloads the image and then restores or downloads
about 83.1 GB with the default INT8 image profile. At 300 Mbps the theoretical
transfer floor is about 37 minutes; allow roughly 45-75 minutes including hash
verification and ComfyUI startup. Slow nodes can take longer. Lowest priority
can be interrupted and moved without warning.
An interrupted H3 sampling pass cannot resume mid-denoise; its saved request can
be submitted again from the beginning. Completed files are retained only when
external storage is configured and their upload has finished.

The container runs PyTorch 2.11 with CUDA 12.8 because this is the documented
safe Blackwell baseline without assuming every Salad host has an R580+ driver.
Qwen's separate desktop project reports faster optimized kernels on CUDA 13;
switching this deployment to CUDA 13 must wait for a real assigned node driver
check. H3 quality is independent of that choice: the image build checks the
exact corrected H3 VAE source line and aborts if it is missing.

## URLs

- Video: `/extensions/h3_studio/index.html`
- Image: `/extensions/h3_studio/image.html`
- Comfy node editor for maintenance: `/?view=nodes`
