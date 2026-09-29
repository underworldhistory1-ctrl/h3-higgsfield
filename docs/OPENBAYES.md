# OpenBayes workspace notes

H3 Studio can run on an OpenBayes RTX 5090 workspace with the ComfyUI checkout
and this repository under `/output/h3-stack`, and the reusable model dataset
mounted read/write at `/input0/h3-models`. Keep input and output media in the
workspace's `/output`, not in the model dataset.

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
