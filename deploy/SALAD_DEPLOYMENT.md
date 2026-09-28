# SaladCloud deployment

The Salad image contains the application, pinned ComfyUI, CUDA 12.8 PyTorch,
FFmpeg, Spectrum and MotionCache. It deliberately contains no model weights.
The complete H3 model set is 65.84 GB and would exceed Salad's 35 GB compressed
container-image limit.

## Container Group

- Image: `ghcr.io/underworldhistory1-ctrl/minimax-h3-higgsfield:salad`
- GPU: RTX 5090 32 GB
- Replicas: 1
- Priority: Lowest (Low is more reliable for interactive work)
- RAM: 32 GB minimum; 64 GB preferred
- CPU: 8 vCPU minimum
- Ephemeral storage: 100 GB minimum; 150 GB recommended
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
65.84 GB of weights. A typical 300 Mbps effective model transfer takes roughly
35-50 minutes including validation and ComfyUI startup. Slow nodes can take
90-130 minutes. Lowest priority can be interrupted and moved without warning.
An interrupted H3 sampling pass cannot resume mid-denoise; its saved request can
be submitted again from the beginning. Completed files are retained only when
external storage is configured and their upload has finished.
