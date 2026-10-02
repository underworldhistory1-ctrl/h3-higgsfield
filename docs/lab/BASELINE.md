# H3 Studio Lab — Baseline Snapshot and Environment Verification

**Date:** 2026-10-03  
**Upstream Repository:** `https://github.com/underworldhistory1-ctrl/minimax-h3-higgsfield`  
**Upstream Base Commit:** `fd7e0db3b046fe21d5b55c1d51cbf874c92f0940` (2026-10-01)  
**Upstream Drift:** Zero drift detected on `main` at execution time (`refs/heads/main` matches HEAD `fd7e0db3b046fe21d5b55c1d51cbf874c92f0940`).

---

## 1. Local Development Test Environment

- **Operating System:** Windows 11 (64-bit AMD64)
- **Local Python:** Python 3.14.3
- **Local Node.js:** v25.8.1
- **Local Git:** git version 2.51.1.windows.1
- **Local FFmpeg:** ffmpeg 8.1-full_build (gyan.dev)
- **Local Test Scope:** CPU unit tests, contract mocks, syntax verification, bundle validation, offline JS/browser test journeys.
- **Hardware Isolation Rule:** Local PC is NEVER used for ComfyUI server execution, GPU inference, model weight downloads, or CUDA benchmarking.

---

## 2. Baseline Verification Results

Prior to making any lab modifications, the existing test suite and JS syntax checks were executed:

### Python Unit Tests
```bash
python -m unittest discover -s tests
```
- **Total Tests:** 21
- **Passed:** 21
- **Failed:** 0
- **Duration:** 0.245s
- **Verified modules:** `test_deployment_contract.py`, `test_image_ui_contract.py`, `test_qwen_downloader.py`, `test_qwen_image.py`, `test_qwen_routes.py`.

### JavaScript Syntax Checks
```bash
node --check web/studio.js
node --check web/image-studio.js
```
- `web/studio.js`: Valid JavaScript syntax (exit code 0).
- `web/image-studio.js`: Valid JavaScript syntax (exit code 0).

---

## 3. Remote Online Test Server Status

- **Status:** Pending explicit designation and authorization from user.
- **Rules Enforced:**
  - Production server (`/extensions/h3_studio/`) is strictly protected; no lab code or test renders will run on production.
  - Awaiting authorized isolated test server details (SSH host/port, token/credentials, spending/runtime limits).
  - All lab development proceeds with comprehensive mocks, contract unit tests, and deployable server scripts ready for authorized execution.

---

## 4. Upstream Audit Baseline Observations Confirmed

1. **Frames Node IDs:** Fixed in code (node 13 = `H3ReleaseForDecode`, node 15 = first frame, node 16 = last frame). Graph documentation (`docs/GRAPH_MAP.md`) still refers to legacy node IDs (13/14).
2. **Compiler Forced Preservation:** `resolvedPrompt()` in `web/studio.js` forces `fully_preserved` for non-storyboard image references regardless of user role or prompt intent.
3. **Structured Prompt Double-Wrapping:** References prompts are wrapped into standard sections (`subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`, `non_diegetic_music`). If user inputs an already structured prompt, it gets re-wrapped.
4. **Qwen Cancellation Flaw:** `web/image-studio.js` ignores HTTP 500 response from `/interrupt` and deletes input tracking unconditionally. AbortController can leave orphan server jobs when prompt submission was accepted but response was not yet processed.
5. **Session Cleanup & Stale Sweeping:** Temporary inputs swept after 6 hours without consulting durable job leases.
6. **Latent Lifecycle:** `h3_video_save.py` deletes sampled latent on successful video save (`h3_studio_<token>.safetensors`), preventing reuse for continuation context.
