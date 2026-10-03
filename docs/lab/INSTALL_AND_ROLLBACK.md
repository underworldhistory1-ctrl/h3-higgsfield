# Isolated online installation and rollback

The separate V2 repository is https://github.com/underworldhistory1-ctrl/minimax-h3-higgsfield-v2. The authorized online installation uses /output/h3-studio-v2, separate from production and LTX 2.5. Transfer a private repository using the reviewed Git bundle without placing GitHub credentials on the server. Server commands below never run local model inference.

## Required isolation

Use a separate ComfyUI checkout/process, separate custom_nodes, input, output, temporary directory and port. Do not install both H3 Studio copies in the same process: they share node IDs and HTTP paths. Stop or drain production inference before the lab loads models on the shared GPU; a second port does not isolate VRAM.

Use your server's existing compatible Python environment and existing weights. Do not infer CPU/CUDA versions from developer unit tests. Follow the baseline START_HERE.md and COMPATIBILITY.md for model dependencies. No weights or CUDA installation is required on the Windows development machine.

## Reproducible checkout

On the test server, replace LAB_ROOT and BUNDLE with actual isolated paths. First make a new empty LAB_ROOT; do not point it at production.

```bash
LAB_ROOT=/absolute/path/to/new-h3-lab
BUNDLE=/absolute/path/to/H3-Lab-Reviewed.bundle
mkdir -p "$LAB_ROOT"
git clone https://github.com/Comfy-Org/ComfyUI.git "$LAB_ROOT/ComfyUI"
cd "$LAB_ROOT/ComfyUI"
git checkout 3b4c0b0e457cf0a51cf3038e0a6750d8f96ce251
git clone -b lab/main "$BUNDLE" custom_nodes/h3_studio_v2
```

Confirm the lab checkout commit against the handoff manifest, then install only baseline dependencies missing from the authorized server environment. The source ZIP is an alternative for inspection, but the bundle preserves the review history.

## Optional masked-context engine

Image/audio/video guides use native AddGuide; they do not require this addon. Extend requires the separately installed pinned engine plus the lab-owned context and trim nodes.

```bash
cd "$LAB_ROOT/ComfyUI/custom_nodes"
git clone https://github.com/seitanism/ComfyUI-H3-Motion-Context-MultiRef.git
git -C ComfyUI-H3-Motion-Context-MultiRef checkout 361624fb406b63eb6694442eac6c895fc1533a70
```

Review that checkout's requirements before installing on the server. Keep its GPL source/license in its separate repository. Do not vendor it into this MIT project, install competing extenders, or apply core patches speculatively. The lab reports missing node readiness and blocks unavailable Extend. Installing the addon does not establish seam quality or promotion approval.

## Shared model paths

Create a lab-specific extra_model_paths.yaml referencing the actual existing server model folders (diffusion_models/text_encoders/vae/loras, including the baseline's actual aliases when needed). Path references and symlinks do not enforce read-only access: use server permissions or a read-only mount if needed. Never change permissions or files in production to set this up. The lab must not download or rewrite shared weights.

## Start and open a second interface

Use the existing compatible server interpreter and baseline launch flags, replacing only the checkout, port and data directories. Avoid the old unverified --highvram recommendation.

```bash
cd "$LAB_ROOT/ComfyUI"
python main.py --listen 127.0.0.1 --port 8190 \
  --input-directory "$LAB_ROOT/input" --output-directory "$LAB_ROOT/output" \
  --extra-model-paths-config "$LAB_ROOT/extra_model_paths.yaml"
```

Access port 8190 through your authorized tunnel/reverse proxy. Production H3 occupies 8188; LTX occupies 8189 and 7860. The workspace URL is `/extensions/h3_studio_v2/index.html`. Set its server URL to this lab endpoint. The queue bridge uses this process's actual listener port; use plain HTTP loopback behind the tunnel/proxy.

## Editing while production runs

The lightweight `deploy/v2_standby.py` serves the V2 editing interface, private projects and assets without importing torch or ComfyUI. Generation is blocked in both backend and UI. Only approved production status GETs are allowed; production queues and outputs are not exposed. Standby uses /output/h3-studio-v2/data/input and /output/h3-studio-v2/data/output, on loopback port 8190.

Open an SSH tunnel from Windows, keeping it running:

```bash
ssh -N -L 18190:127.0.0.1:8190 -p 31747 root@ssh.hyper.ai
```

Enter the server password interactively, then open http://127.0.0.1:18190/extensions/h3_studio_v2/index.html. Models and rendering remain on the server. Do not commit credentials.

`deploy/activate_v2.py` is a manual gate, not a scheduler: it refuses activation while H3/LTX queues, GPU or memory are busy. Editing standby does not establish GPU render quality.

Check `/h3_studio/readiness` and `/h3_studio/lab/capabilities` first. Missing optional continuation nodes should disable Extend while available normal modes remain usable. Check the installed object_info schema before enabling timed AV guides.

## GPU acceptance gates

Run the live matrix in TEST_REPORT.md sequentially, within a user-specified budget. Verify completed playable files, audio, exact frame counts and actual joins. Test direct Ref2VA continuation with image references; test the video-context path separately for checkpoint changes. CPU tests are not a substitute.

## Rollback

Stop only the process listening on the lab port. Production files and outputs were never changed. Preserve LAB_ROOT/output, including lab_storage, h3_lab_contexts and latent recovery files, until you decide to remove the lab. Do not issue automatic recursive deletion, move data into production, or promote code before the live gates pass.

The server Python3.10 build omits pidfd wrappers. V2 ownership checks prefer native wrappers and use a Linuxx86-64-only kernel syscall fallback when absent, preserving a stable process handle. Unknown platforms/kernels fail closed. The ABI identifiers follow the [official Linux x86-64 syscall table](https://github.com/torvalds/linux/blob/v6.8/arch/x86/entry/syscalls/syscall_64.tbl). No PID-based global kill is used.
