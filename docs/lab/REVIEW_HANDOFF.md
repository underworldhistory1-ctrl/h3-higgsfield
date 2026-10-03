# Review handoff

Base delivered lab commit: f1042d22bbcd1e1804b39c51803f207ec9adb7eb. Upstream baseline: fd7e0db3b046fe21d5b55c1d51cbf874c92f0940. Follow-up reviewed source is committed on local lab/main and supplied as a ZIP and Git bundle, with a separate manifest naming the exact resulting commit.

## Do not trust the previous completion summary

Original blockers reproduced: constructor/export mismatch crashed startup; raw graph return destructured incorrectly; Frames render spec disagreed with compiler; continuation fields did not create context nodes; no visible guide wiring; cancellation route could not call the async service; lab submission did not queue; Qwen/assembly paths were wrong; arbitrary server files could enter bundles; project imports lost asset bindings; extension overlap was not trimmed. Helper-only tests did not establish an integrated working lab.

The corrective implementation reconnects these paths and adds real CPU browser acceptance. Read STATUS.md, ARCHITECTURE.md, TEST_REPORT.md and INSTALL_AND_ROLLBACK.md together. No GPU acceptance claim is made.

## Review groups

1. Prompt/graph/controller helpers and their JS contract tests.
2. Workspace controls, durable restoration, actual image/AV guide upload, effective settings, explicit accepted takes and lineage replacement.
3. Managed asset/project/context storage and portable bundle security.
4. Request-id queue submission/recovery/cancellation leases, including true response-loss tests and safe targeted interruption.
5. Lab-owned durable context and exact AV trim nodes; external masked-context dependency remains separately installed.
6. CPU media extraction/audio export and verified sequence assembly.

Promote only groups whose live integration gates pass. Do not copy the whole lab directly into the production custom_nodes folder or run both copies in one process.

## Pending live reviewer work

Use an authorized isolated online server. Verify actual object_info schemas and installed dependency pins. Run one native baseline render first, then guide/reference variants, direct generated context, video-context continuation, and two extensions. Verify actual media and joins. Record GPU peak memory, wall time, model/LoRA settings and failure recovery. Compare any optional quality experiment only after schema/layout compatibility and a concrete need are established.

The user's instruction remains: no local GPU rendering, no speculative controls, production stays separate until testing succeeds.
