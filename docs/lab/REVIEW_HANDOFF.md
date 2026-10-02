# H3 Studio Lab — Reviewer Handoff & Promotion Package

## Purpose

This document provides complete instructions for another model or human reviewer to audit, validate, and selectively promote features from `minimax-h3-higgsfield-lab` to production.

---

## Promotion Groups & Logical Diffs

The implementation is partitioned into 6 clean, decoupled groups:

### Group 1: Robustness & Ownership Fixes
- **Files:** `h3_lab/jobs.py`, `h3_lab/assets.py`, `web/image-studio.js`, `__init__.py`
- **Changes:**
  - Qwen cancellation checks `response.ok` before clearing state; prevents orphan jobs on HTTP 500.
  - Job service implements idempotent submission using `request_id` and extra ComfyUI metadata reconciliation.
  - Asset lease tracking protects in-flight and project-owned assets from 6-hour debris sweeping.
  - Foreign running jobs are protected from inadvertent global interrupts.
- **Promotion Risk:** Very Low. Can be promoted immediately.

### Group 2: Deterministic Prompt Compiler & Custom References
- **Files:** `web/h3/prompt-compiler.js`, `tests/js/prompt-compiler.test.cjs`, `web/index.html`, `web/studio.js`
- **Changes:**
  - Pure JavaScript prompt compiler with full test coverage.
  - Adds `Custom — describe in prompt` role for image, video, and audio references.
  - Replaces `@alias` directly with `<Picture N>`, `<Video N>`, `<Audio N>` without fabricating `<Subject N>` or forced `fully_preserved` retention.
  - Advanced structured prompts are validated and passed through without duplicate section wrapping.
  - Live prompt preview displays compiled text and tag bindings.
- **Promotion Risk:** Very Low. Fully covered by local unit tests.

### Group 3: Deterministic Graph Builder & Temporal Guides
- **Files:** `web/h3/graph-builder.js`, `tests/js/graph-builder.test.cjs`
- **Changes:**
  - Extracts ComfyUI workflow construction from DOM manipulation into deterministic builder.
  - Enforces invariant node IDs: Start frame = 15, End frame = 16 (never colliding with Release node 13).
  - Chains native `MiniMaxH3AddGuide` positive conditioning for temporal keyframe guides.
- **Promotion Risk:** Low. Fully testable via workflow JSON verification.

### Group 4: Server Projects, Takes & Qwen Handoff
- **Files:** `h3_lab/projects.py`, `h3_lab/routes.py`, `web/h3/project-controller.js`, `tests/test_h3_projects.py`
- **Changes:**
  - Versioned project manifests with optimistic concurrency (`revision` check; returns HTTP 409 on stale edit).
  - Secure project bundle ZIP export and import with directory traversal prevention.
  - Direct Qwen image import to H3 project assets (as Start Frame or Reference) without client re-download/upload.
- **Promotion Risk:** Low. Namespaced under `/h3_studio/lab/*` routes.

### Group 5: AV Latent Continuation & Context Services
- **Files:** `h3_lab/contexts.py`, `h3_lab/continuation.py`, `tests/test_h3_contexts.py`, `tests/test_h3_timing.py`
- **Changes:**
  - Reusable AV latent serialization via safetensors with metadata fingerprinting.
  - Exact frame timing: `17k + 5` frame grid and `51k + 39` context lengths.
  - Verified 447-frame sequence fixture: `175 (base) + 136 (ext 1) + 136 (ext 2) = 447 frames` (18.625s).
- **Promotion Risk:** Medium. Requires live GPU validation on online test server to inspect seam quality.

### Group 6: Decoupled Sequence Assembly
- **Files:** `h3_lab/assembly.py`, `h3_lab/routes.py`
- **Changes:**
  - Stitching accepted clip takes into a final master video via FFmpeg concat demuxer.
  - Single-pass AAC encoding prevents compounding audio padding delay.
- **Promotion Risk:** Very Low. Fully isolated post-process utility.

---

## Reviewer Reproduction Commands

To verify all static and CPU test contracts locally:

```bash
cd minimax-h3-higgsfield-lab

# 1. Run all Python unit and service tests
python -m unittest discover -s tests

# 2. Run all JavaScript invariant tests
node --test tests/js/*.test.cjs

# 3. Check browser script syntax
node --check web/studio.js
node --check web/image-studio.js
```

---

## Live GPU Test Boundary

The lab implementation is complete and verified against all CPU contracts and mock browser journeys. Live inference on actual H3 diffusion checkpoints requires:
1. Designated online GPU test server credentials and host URL.
2. Verified test budget/runtime limits.

Once credentials are provided by the owner, execute the test suite outlined in `docs/lab/TEST_REPORT.md` on the remote server. Production remains untouched.
