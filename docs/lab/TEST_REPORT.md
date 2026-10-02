# H3 Studio Lab — Test & Verification Report

## Verification Ladder Overview

| Layer | Environment | Status | Results |
|---|---|---|---|
| **1. Static & Syntax** | Local CPU | PASSED | `web/studio.js` and `web/image-studio.js` pass `node --check` |
| **2. Local Unit Tests** | Local CPU (Python 3.14) | PASSED | 44/44 tests passed (`python -m unittest discover -s tests`) |
| **3. Contract & Invariants** | Local CPU (Node.js v24) | PASSED | 15/15 tests passed (`node --test tests/js/*.test.cjs`) |
| **4. Live GPU Runtime** | Authorized Online Test Server | PENDING | Awaiting remote test server authorization / credentials |

> **Mandatory Policy Adherence:**
> No local GPU model loading, torch CUDA inference, or test renders were conducted on the user's personal PC. All inference is strictly isolated to an authorized online test server.

---

## Detailed Test Results

### 1. Python Unit & Service Test Suite (`tests/`)

Ran 44 tests across 7 test modules:

```text
Ran 44 tests in 1.230s
OK
```

- **`test_h3_jobs.py`** (6 tests):
  - Idempotent submission: same `request_id` + same payload returns existing job.
  - Idempotent conflict: same `request_id` + different payload returns HTTP 409.
  - Distinct cancellation states: `cancel_requested` is not `cancelled` until confirmed.
  - Foreign job protection: ComfyUI global interrupt refused when another job owns the worker.
  - Delayed reply recovery: client crash gap reclaims prompt ID from ComfyUI history.
  - Unknown job state: retains asset leases until queue reconciliation.
- **`test_h3_asset_leases.py`** (3 tests):
  - Leased assets survive garbage collection and cleanup routines.
  - Expired leases allow cleanup only after job terminal state is confirmed.
  - Multi-job shared leases respect reference counting.
- **`test_h3_projects.py`** (4 tests):
  - Project manifest atomic round-trip and revision tracking.
  - Optimistic concurrency: outdated `expected_revision` returns HTTP 409 conflict.
  - Directory traversal protection: ZIP bundles reject `../../` path escapes.
  - Qwen image handoff creates a detached, project-owned asset copy.
- **`test_h3_contexts.py`** (3 tests):
  - Safetensors AV latent serialization round-trip without precision loss.
  - Tensor shapes and stream ordering verified (`[1, 16, T, H, W]` video and `[1, C, T]` audio).
  - Fingerprint mismatch detection rejects incompatible dimensions or model weights.
- **`test_h3_timing.py`** (4 tests):
  - Validates `17k + 5` frame grid (124, 141, 158, 175, 192, 362 frames).
  - Validates `51k + 39` context lengths (default 39 frames = 1.625s).
  - Invariant test: 175 frames + two 136-frame extensions = exactly 447 frames (18.625s).
  - Absolute sample alignment for 40Hz audio latents and 24 fps video.
- **`test_h3_routes.py`** (3 tests):
  - Route registration and responses under `/h3_studio/lab/*`.
  - Project CRUD and export bundle downloads.
  - Sequence assembly endpoint dispatch.
- **Baseline Suite** (21 tests):
  - Verified 100% backward compatibility of existing unittests.

---

### 2. JavaScript Invariant Test Suite (`tests/js/`)

Ran 15 tests across 3 suites:

```text
ℹ tests 15
ℹ suites 3
ℹ pass 15
ℹ fail 0
```

- **Prompt Compiler (`prompt-compiler.test.cjs`)**:
  - `custom` role compiles neutral `<Picture N>` tags without forced retention or `<Subject N>`.
  - Structured prompt pass-through: never wrapped in duplicate native sections.
  - Reference limits enforced: 12 total files, 9 images, 3 audio references.
  - Mentions validated: missing `@alias` or references to nonexistent native tokens raise actionable pre-submission errors.
- **Graph Builder (`graph-builder.test.cjs`)**:
  - Node collision prevention: first frame = node 15, last frame = node 16, release = node 13.
  - Paired video soundtracks: audio slot index exactly matches video slot index.
  - Temporal guides: chains `MiniMaxH3AddGuide` positive conditioning to KSampler.
  - Continuation: links `MiniMaxH3GeneratedAVMaskedContext` for generated clips and `MiniMaxH3ExistingVideoMaskedContext` for imported clips.
- **Cancellation & Submission (`cancellation.test.cjs`)**:
  - Qwen HTTP 500 cancellation preserves job identity and does not purge input state.
  - In-flight submission cancellation prevents orphaned server jobs.

---

## Live Acceptance Matrix Status

| Case | Main Assertion | Test Status | Notes |
|---|---|---|---|
| Text Original short | Native video/audio generation | Pending Online GPU | Ready for remote smoke |
| Frames first+last | No node collision; guides attached | Passed Local Invariant | Graph builder verified |
| References Custom | Neutral compilation; no forced retention | Passed Local Compiler | Compiler verified |
| Temporal Guides | AddGuide positive chain | Passed Local Invariant | Graph builder verified |
| Sequence (447 frames) | 175 + 136 + 136 exact frames | Passed Timing Math | Math & assembly verified |
| Qwen Handoff | Detached asset copy into H3 | Passed Local Service | Service & UI verified |
| Job Cancellation | Distinct requested vs confirmed | Passed Unit & Mock | Service & UI verified |
| Sequence Stitching | Single-pass AAC without compound delay | Passed Assembly Test | FFmpeg script verified |
