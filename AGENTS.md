# H3 Studio handoff for a new assistant

The user operates MiniMax H3 through the **English H3 Studio web UI**. ComfyUI nodes are backend infrastructure. Read `START_HERE_AR.md`, then `README.md`, `docs/GRAPH_MAP.md`, and `docs/COMPATIBILITY_MATRIX_AR.md` before changing or deploying anything.

## What this folder contains

- `web/index.html` and `web/studio.js`: the production interface.
- `__init__.py`: ComfyUI extension API for uploads, readiness, library, thumbnails, timing samples, settings metadata, and cleanup.
- `workflows/`: quality and short smoke graphs in UI and API formats.
- `deploy/`: idempotent bootstrap, pinned model/LoRA downloads, landing-page redirect, installer, and read-only server verification.
- `docs/`: UX flow, compatibility boundaries, and dated audit.

## Operating rules

1. Get the **new** rental's SSH host, port, credentials, ComfyUI URL and authentication method from the user. Never reuse, print, or save credentials from an older rental. The scripts prompt for passwords or read a token from an environment variable.
2. Inspect the ComfyUI queue before changing a running server. Do not restart, interrupt, delete outputs, or switch checkpoints during an active render.
3. For a new Linux server, use `install.sh` or the SSH handoff `deploy/provision_h3.py` with the adjacent `h3-cloud-setup.zip`. For native Windows, use `install.ps1` with ComfyUI Portable or a source checkout. Both direct installers check the queue before restart; the older SSH handoff still requires a provider-managed restart. Never interrupt an active render.
4. Verify `/h3_studio/readiness`, `/h3_studio/loras`, `/h3_studio/library`, and the user-facing page after restart. `deploy/verify_h3_server.py` performs read-only API checks. A ready API is not proof that every LoRA or render method preserves quality.
5. Preserve `ComfyUI/output/video/` and its hidden `.h3-studio-*.json` metadata files. This is the generated video library and timing/settings history. A new empty server has none of those unless the optional `library-backup.zip` is restored with `--library-backup`. Take a fresh library snapshot only after the ComfyUI queue is empty.
6. Original quality (FL2VA for Text/Frames, Ref2VA for References) is the default. The default canvas is 1280×704, 362 frames / 15.1 s, 20 steps, 24 fps, with native audio. Spectrum, MotionCache, Turbo, Realism People, and Combat V2 are prepared but selected only by the user in the UI. Turbo targets FL2VA and stays disabled in References mode. Combat V2 can be selected in References with an experimental Ref2VA notice; its creator tested FL2VA only.
7. The model weights are **not** bundled: about 63.4 GB must be downloaded once onto a persistent model disk. Never promise a seconds-long install onto an empty disk. The pinned downloader skips verified cached model files on subsequent rentals.
8. Do not use the nodes editor as the user's main UI. H3 Studio is `/extensions/h3_studio/index.html`; the server root redirects there after login. `/?view=nodes` is for technical work.
9. In References mode, a multi-panel storyboard image uses the Storyboard role and compiles to `<Picture N>`; character images compile to `<Subject N>`. Both use the native `ref_images` input. This guides shots but does not lock exact frames.

## Proven scope and limits

The current RTX 5090 server produced a 5.2 s smoke clip and 15.1 s Original clip with decodable video/audio. The dated audit records the evidence. No finite check proves every prompt, LoRA combination, visual detail, or audio sync. Before claiming a new server fully ready, report the live readiness result and what was or was not rendered on that specific server.
