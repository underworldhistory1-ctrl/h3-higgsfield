# OpenBayes workspace notes

H3 Studio can run on an OpenBayes RTX 5090 workspace with the ComfyUI checkout
and this repository under `/output/h3-stack`, and the reusable model dataset
mounted read/write at `/input0/h3-models`. Keep input and output media in the
workspace's `/output`, not in the model dataset. Use `install.sh` to prepare a
new Linux ComfyUI checkout. The optional `deploy/openbayes_h3.sh` is for an
existing persistent stack with the paths below; it is not a fresh machine
installer.

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
RAM, not the RTX 5090's VRAM. Choose at least 64 GB advertised system RAM for
long Ref2VA at this canvas. Studio blocks 294 frames and longer on hosts that
report under 56 GB of system RAM, rather than silently lowering quality or
wasting another long render. Shorter combinations remain unverified.
The observed sampler alone took about 88 minutes (~264 seconds per step), so
the initial uncalibrated ETA on that host was too optimistic. A successful
completed render is needed before Studio can calibrate its saved timing.
