# H3 Studio Lab — Installation, Server Setup & Rollback Guide

## 1. Remote Test Server Installation

> **Policy Reminder:**
> Inference must run ONLY on an authorized online GPU test server. Do not install ComfyUI, CUDA, or weights on the local development machine.

### Prerequisites on Online Test Server
- Ubuntu 22.04+ with NVIDIA GPU (24GB+ VRAM recommended, e.g. RTX 4090 / A100 / L40S)
- Python 3.10–3.12 with PyTorch 2.3+ CUDA
- FFmpeg 6.0+ installed and available in PATH
- Pinned ComfyUI: Commit `3b4c0b0e457cf0a51cf3038e0a6750d8f96ce251`

### Step-by-Step Remote Setup Script

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. Clone Lab Repository in an isolated directory
mkdir -p /opt/h3-lab
cd /opt/h3-lab
git clone -b lab/main https://github.com/underworldhistory1-ctrl/minimax-h3-higgsfield-lab.git custom_nodes/minimax-h3-higgsfield-lab

# 2. Install Pinned Low-Level Continuation Candidate
cd /opt/h3-lab/custom_nodes
git clone https://github.com/seitanism/ComfyUI-H3-Motion-Context-MultiRef.git
cd ComfyUI-H3-Motion-Context-MultiRef
git checkout 361624fb406b63eb6694442eac6c895fc1533a70

# 3. Share Production Model Directory Read-Only
# Link existing weights without duplicating 60+ GB
mkdir -p /opt/h3-lab/models
ln -s /opt/comfyui-production/models/unet /opt/h3-lab/models/unet
ln -s /opt/comfyui-production/models/clip /opt/h3-lab/models/clip
ln -s /opt/comfyui-production/models/vae /opt/h3-lab/models/vae
ln -s /opt/comfyui-production/models/loras /opt/h3-lab/models/loras

# 4. Install Lab Python Dependencies
pip install --no-cache-dir aiohttp safetensors

# 5. Launch Isolated Lab Server on Port 8189
python main.py --listen 127.0.0.1 --port 8189 --highvram
```

---

## 2. Model Weight Verification

Verify model headers using safetensors without loading weights into RAM:

```bash
python -c "
import safetensors.torch
files = [
    'models/unet/minimax_h3_fl2va_pruned_int8_convrot.safetensors',
    'models/unet/minimax_h3_ref2va_pruned_int8_convrot.safetensors',
    'models/clip/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',
    'models/vae/minimax_h3_video_vae_fp16.safetensors',
    'models/vae/minimax_h3_audio_vae_fp32.safetensors'
]
for f in files:
    with safetensors.safe_open(f, framework='pt') as sf:
        print(f'{f}: verified {len(sf.keys())} keys')
"
```

---

## 3. Rollback & Deactivation Procedure

The lab is completely decoupled from production:
1. **Zero Overwrite**: The lab resides in a separate repository and folder. No production files are modified.
2. **Deactivation**: Stop the ComfyUI process running on port 8189.
3. **Data Removal (Optional)**: To completely purge lab artifacts, remove `/opt/h3-lab/custom_nodes/minimax-h3-higgsfield-lab/lab_storage/`. Production outputs under `ComfyUI/output/` are untouched.
4. **Promotion Guard**: No changes may be merged to the production repository until another model reviews the handoff artifacts and completes live GPU smoke testing.
