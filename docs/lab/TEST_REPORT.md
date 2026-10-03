# V2 verification report — 2026-10-03

The original f1042d2 helper-only success report is superseded. Main UI/backend integration, native AV tensor shapes, imported output paths, cancellation races and project recovery defects were reproduced and repaired.

## Verified CPU boundaries

- Python service, HTTP, filesystem, project/asset/context integrity, real FFmpeg synthetic-media assembly and standby isolation tests: 80 tests passed.
- JavaScript compiler, graph and project controller contracts: 26 passing tests.
- Real Chromium exercised the connected video interface with an isolated CPU backend: 13 journeys passing with zero uncaught JavaScript errors after the final video standby guards.
- Additional real Chromium image acceptance passes create/edit graph submission, zero seed, duplicate-click prevention, queued-only cancellation, running-job protection, lost-acknowledgement recovery and standby refusal; zero uncaught JavaScript errors.
- Browser journeys cover restored frames/references, neutral custom roles, fitted server images, Qwen image handoff, timed image/audio/video guides, imported continuation, request acknowledgement loss, and cancellation before and after backend acceptance.
- Synthetic media validation verifies real decoding, audio/video timing and exact frame counts. Example two clips: 24 + 24 - 6 overlap = 42 frames. The continuation accounting fixture verifies 175 + 136 + 136 = 447 frames (18.625 seconds at 24 fps).
- CPU safetensor tests use native H3 video [B,24,T,H/16,W/16] and audio [B,32,2,T] contracts, hashes, floating dtype, checkpoint compatibility and recovery metadata. They do not prove native GPU integration.
- Standby tests establish no torch/Comfy import, no production storage adoption, approved production GET allowlist, and inference mutation rejection with no phantom job creation.

## Online inspection

The authorized HyperAI server has production H3 on 8188, LTX backend on 8189 and LTX UI on 7860. The GPU was busy and container memory near its limit. No inference, interrupt, queue mutation, package upgrade, model download or production service restart was performed. V2 was deployed using isolated source/data and port 8190. A real browser connected through SSH, verified V2 branding, saved and reloaded a server-owned project, and verified disabled generation on video/image pages with zero JavaScript errors. Direct /prompt and /h3_studio/lab/jobs requests return HTTP 403; V2 queue remains empty. Existing production and LTX process IDs remained alive. Editing standby deliberately blocks inference.

## Pending GPU acceptance — do not claim passed

Run sequentially only after current production completes and manual resource checks pass:

1. Normal FL2VA text and start/end frames; playable MP4 and native audio, correct dimensions/frame count.
2. Ref2VA neutral custom image, video and audio references; confirm exact tag binding and audible guidance.
3. Native timed image/audio/video guides; inspect timing, identity and frame positioning against requested guide times.
4. Direct saved AV-latent Extend with image references; correct checkpoint/canvas match and exact net-added frames.
5. Re-encoded video-context Extend; correct 24 fps handling, trim offset and reference/guide placement.
6. Three-take sequence assembly: exact duration, no accumulating AAC padding, inspect cuts, lip sync and perceptual seam quality.
7. Live request recovery and cancellation with only V2-owned jobs; production and LTX jobs must remain intact.
8. Qwen image generation/handoff if an actual complete profile is installed. Missing models must stay unavailable.

Latent Upscaler and AudioRefine remain documented investigations, not implemented controls. No GPU output or quality judgment is inferred from mocks, CPU tensors, syntax checks or a responsive page.
