# OpenBayes workspace notes

H3 Studio can run on an OpenBayes RTX 5090 workspace with the ComfyUI checkout
and this repository under `/output/h3-stack`, and the reusable model dataset
mounted read/write at `/input0/h3-models`. Keep input and output media in the
workspace's `/output`, not in the model dataset. Use `install.sh` to prepare a
new Linux ComfyUI checkout. The optional `deploy/openbayes_h3.sh` is for an
existing persistent stack with the paths below; it is not a fresh machine
installer.

If a new workspace attaches only the model dataset and has no `/output/h3-stack`,
run `deploy/openbayes_fresh_workspace.sh` from a clone of this repository. It
installs the pinned ComfyUI checkout and dependencies under the new workspace's
`/output`, links `ComfyUI/models` to `/input0/h3-models`, and reuses verified
weights instead of downloading them again. It refuses to proceed if port 8188
already serves ComfyUI or if an existing repository checkout has local edits.

For that existing stack, copy `deploy/openbayes_h3.sh` to `/output/h3-stack/h3`
and run `bash /output/h3-stack/h3 up`. It checks mounts and model sizes,
installs the pinned Spectrum and MotionCache nodes, checks for an active render
before any restart, and verifies the live API before reporting readiness.
`bash /output/h3-stack/h3 verify` reports the current server state without
restarting it. Its default ComfyUI bind address is localhost; use the SSH
tunnel below to reach the Studio UI.

The prepared H3 plus Qwen INT8 model set uses about 83 GB of the 100 GB model
dataset. The three H3 LoRAs are included. BF16 Qwen needs another 31.8 GB and
does not fit beside INT8 in that dataset. If `/output` has a separate 50 GB
quota and at least 40 GB available, `deploy/openbayes_prepare_bf16.py` can put
BF16 there and link it into the current workspace's model tree. It checks
downloaded files against the pinned size and SHA-256 before exposing them to
ComfyUI. This leaves INT8 intact. A second workspace bound to the same model
dataset can use H3 and INT8 immediately, but its `/output` is separate and it
will not inherit the first workspace's BF16 files through those links.

For a local UI tunnel, install Paramiko on the local machine and run:

```powershell
$env:H3_SSH_PASSWORD = Read-Host 'OpenBayes SSH password'
python deploy/openbayes_tunnel.py --ssh-port YOUR_CURRENT_PORT --local-port 8766
```

Open `http://127.0.0.1:8766/extensions/h3_studio/index.html`. The tunnel
listens on localhost only. Replace the SSH port whenever the workspace gets
a new one. Never put the password in Git or a saved script.

After installing optional Spectrum and MotionCache nodes, restart ComfyUI only
when its queue is empty. Confirm readiness from the running server, then use
the Studio UI. A ready API does not prove a full video or image render.

## System RAM for full Ref2VA renders

The 40 GB RAM OpenBayes RTX 5090 workspace hit its cgroup memory limit during
Video VAE decoding of a 362-frame, 1280×704 Ref2VA render after completing
20 sampling steps. A second 345-frame, 1280×704 MotionCache render completed
20 sampling steps in 50 minutes and was killed during Video VAE decoding.
`memory.events` recorded an `oom_kill`, and no output was saved. This is system
RAM, not the RTX 5090's VRAM. A later 362-frame MotionCache run on the 40 GB
workspace again completed all 26 sampler calls in about 23 minutes, then hit
`oom_kill` while loading the Video VAE. Studio warns but does not block this
combination. On hosts with under 56 GiB cgroup RAM, the installer starts
ComfyUI with `--cache-none --fp16-intermediates` to reduce retained node state
and halve intermediate frame buffers. This is a mitigation, not a confirmed
successful full-length render. Choose at least 64 GB advertised system RAM
for the more reliable long Ref2VA path; shorter or smaller combinations remain
unverified on the 40 GB host.
The Studio graph also saves the sampled latent under `ComfyUI/output/latent/`
and releases generation models before audio/video VAE decoding. On success,
the checkpoint is removed after MP4 save; on a decode failure it remains for a
decode-only recovery using `H3LoadSavedLatent`, both VAE loaders,
`VAEDecode`/`VAEDecodeAudio`, `CreateVideo`, and `H3SaveVideo`. The previous
OOM happened before this checkpoint node was installed, so that run cannot
be decoded without sampling again.
For unattended OpenBayes operation, start
`python3 deploy/openbayes_h3_supervisor.py --comfy-root /output/h3-stack/ComfyUI/ComfyUI`
from the repository after stopping any standalone ComfyUI process. The
supervisor restarts a worker that exits and submits each orphaned checkpoint
for decode once in a clean worker. It does not retry a repeatedly failing
checkpoint forever; the `.recovery-attempted` marker records that limit.
The observed sampler alone took about 88 minutes (~264 seconds per step), so
the initial uncalibrated ETA on that host was too optimistic. A successful
completed render is needed before Studio can calibrate its saved timing.

## RTX 5090 CUDA runtime

The OpenBayes PyTorch 2.8 image includes CUDA 12.8 and can report the 5090 as
available while ComfyUI disables `comfy_kitchen`'s optimized CUDA backend.
On a 2×5090 workspace, a 1280×704 Ref2VA MotionCache job then failed in the
first sampling call inside the eager INT8 linear fallback with a GPU allocation
error. The two 32 GB cards do not form one 64 GB pool for a single ComfyUI
process. `install.sh` now pins PyTorch 2.9.1 with CUDA 13.0 in the persistent
virtual environment. Restart ComfyUI after upgrading and check its log for
`pytorch version: 2.9.1+cu130` and a `comfy_kitchen backend cuda` entry with
`disabled: False`. This removes the fallback seen in that failure; a completed
full-length render remains the required proof of end-to-end stability.
