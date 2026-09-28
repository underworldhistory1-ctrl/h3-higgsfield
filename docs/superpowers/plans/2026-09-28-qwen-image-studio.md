# Qwen Image Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a separate Qwen Image 2.1 Create/Edit workspace inside the authenticated H3 Studio deployment.

**Architecture:** Add a focused Qwen graph/model module and image-library API to the existing ComfyUI extension, plus a separate image workspace that shares the established design and Comfy queue. Extend Salad startup with opt-in verified Qwen profiles while preserving the H3 graph and model set.

**Tech Stack:** Python 3.12, ComfyUI core API graphs, vanilla JavaScript, HTML/CSS, nginx, Bash, Docker, Hugging Face Hub.

**Spec:** `docs/superpowers/specs/2026-09-28-qwen-image-studio-design.md`

## Global Constraints

- Keep H3 Original, References, native audio and VAE tile-fix behavior unchanged.
- Use one ComfyUI process and one queue.
- Keep ComfyUI private on `127.0.0.1:8188`; expose only authenticated nginx port 8000.
- Default Salad profile is Qwen INT8 only; BF16 is optional and must fail closed when absent.
- Do not report GPU validation before a real RTX 5090 generation succeeds.

---

### Task 1: Qwen model and graph contract

**Files:**
- Create: `qwen_image.py`
- Create: `tests/test_qwen_image.py`

**Interfaces:**
- Produces `MODEL_PROFILES`, `required_qwen_nodes()`, `profile_status(comfy_root)`, `build_qwen_graph(request, uploads, token)` and `QwenStudioSaveImage`.

- [ ] Write tests for exact model filenames/sizes, Create graph, Edit graph, ten-reference limit, transparent prompt wrapper and unavailable profile rejection.
- [ ] Run `python -m unittest tests.test_qwen_image -v` and confirm failures because the module is absent.
- [ ] Implement the minimal manifest, validation, graph builder and image saver.
- [ ] Run the test module and confirm all cases pass.
- [ ] Commit `feat: add Qwen image graph contract`.

### Task 2: Image backend API

**Files:**
- Modify: `__init__.py`
- Create: `tests/test_qwen_routes.py`

**Interfaces:**
- Consumes `profile_status()` and the `output/images` sidecar format.
- Produces `/h3_studio/image_readiness`, `/image_library`, `/image_file`, `/image_details`, `/delete_image`.

- [ ] Write route-helper tests for safe filenames, corrupt image exclusion, sidecar metadata and deletion.
- [ ] Run the tests and confirm expected missing-helper failures.
- [ ] Add the route helpers and registered Comfy node mapping.
- [ ] Run all Python tests and confirm success.
- [ ] Commit `feat: add Qwen image library API`.

### Task 3: Image workspace UI

**Files:**
- Create: `web/image.html`
- Create: `web/image-studio.js`
- Modify: `web/index.html`
- Create: `tests/test_image_ui_contract.py`

**Interfaces:**
- Consumes the image API and standard Comfy `/upload/image`, `/prompt`, `/queue`, `/history`, `/ws` endpoints.
- Produces Create/Edit forms, profile availability, progress recovery, preview, library and details dialog.

- [ ] Write contract tests for navigation, required control IDs, accessible dialogs, reference cap, profile gating and graph submission hook.
- [ ] Run tests and confirm the new files/controls are missing.
- [ ] Build the responsive Image workspace and add explicit Video/Image navigation.
- [ ] Implement uploads, graph submission, WebSocket progress, polling recovery, cancellation, cleanup, result details and deletion.
- [ ] Run `node --check web/image-studio.js` and UI contract tests.
- [ ] Commit `feat: add Qwen image workspace`.

### Task 4: Verified Qwen downloader

**Files:**
- Create: `deploy/download_qwen_image_models.py`
- Create: `tests/test_qwen_downloader.py`
- Modify: `install.sh`
- Modify: `install.ps1`

**Interfaces:**
- Consumes `QWEN_IMAGE_PROFILES` or CLI `--profiles`.
- Produces verified files in `models/diffusion_models`, `models/text_encoders`, and `models/vae`.

- [ ] Write tests for profile parsing, byte accounting, disk headroom, offline verification and partial-file reuse.
- [ ] Run tests and confirm missing downloader failures.
- [ ] Implement pinned revision downloads, size/hash checks and fail-closed free-space preflight.
- [ ] Wire optional image installation into Linux and Windows installers without changing default H3 behavior.
- [ ] Run downloader tests and Python compilation.
- [ ] Commit `feat: add verified Qwen model installer`.

### Task 5: Salad integration and Comfy pin

**Files:**
- Modify: `Dockerfile.salad`
- Modify: `deploy/salad/entrypoint.sh`
- Modify: `deploy/verify_h3_server.py`
- Modify: `.github/workflows/salad-image.yml`

**Interfaces:**
- Consumes `QWEN_IMAGE_PROFILES`, defaults to `int8`.
- Produces readiness only after H3 and selected Qwen profiles verify.

- [ ] Add build-contract tests for the new Comfy commit and required H3/Qwen markers.
- [ ] Confirm tests fail against the old image configuration.
- [ ] Pin Comfy `3b4c0b0e457cf0a51cf3038e0a6750d8f96ce251`, add source checks and Qwen startup download.
- [ ] Extend read-only verification with image readiness and node checks.
- [ ] Run shell syntax, Python compile and Docker/nginx configuration checks.
- [ ] Commit `feat: deploy Qwen Image on Salad`.

### Task 6: Documentation and release bundle

**Files:**
- Modify: `README.md`
- Modify: `START_HERE_AR.md`
- Modify: `docs/GRAPH_MAP.md`
- Modify: `docs/UX_FLOW.md`
- Modify: `docs/COMPATIBILITY_MATRIX_AR.md`
- Modify: `deploy/SALAD_DEPLOYMENT.md`
- Modify: `deploy/build_final_folder.py`

**Interfaces:**
- Documents exact storage profiles, license limits, URL, health/readiness and live-test boundary.

- [ ] Document Image workspace behavior, weights and 100/150 GB choices.
- [ ] Add new source, graph and test files to the final-folder builder.
- [ ] Rebuild `H3-Studio-Final` and its ZIP; record SHA-256.
- [ ] Commit `docs: document Qwen Image workspace`.

### Task 7: Final review, build and publication

**Files:**
- Review all modified files.

**Interfaces:**
- Produces a clean main branch, successful GitHub image build and public GHCR digest.

- [ ] Run all Python tests, JavaScript syntax checks and repository contract checks.
- [ ] Inspect the emitted Create and Edit API graphs against Comfy node schemas.
- [ ] Build/push through GitHub Actions and wait for completion.
- [ ] Verify anonymous GHCR pull metadata, compressed size and `linux/amd64` manifest.
- [ ] Update the deployment document with the final digest without retriggering the image build.
- [ ] Rebuild the local final ZIP after the final documentation commit and verify a clean Git tree.

