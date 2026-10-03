# Optional quality investigations — not implemented or GPU verified

Latent Upscaler and AudioRefine remain research candidates listed in lab/dependencies.lock.json. No functioning UI toggle or integration is claimed. No experiment, visual verdict, performance estimate or quality improvement has been established on the authorized GPU server.

The previous handoff described a 16-channel combined latent layout. That is incorrect for this pinned H3 implementation: native H3 uses a NestedTensor with video [B,24,T,H/16,W/16] and audio [B,32,2,T40]. Do not implement a samples[:, :16] audio/video split.

Before any optional implementation:

1. Inspect the pinned candidate's actual node schema, dependencies and license. Confirm it supports this exact native AV layout; do not infer compatibility from a repository title.
2. Keep the ordinary native pipeline as the comparison. Use identical source inputs, checkpoint, canvas and initial seed, recording any extra sampling/refinement cost.
3. For video refinement, prove the audio stream is preserved and measure frame/audio alignment. For audio refinement, prove the video tensor and decoded frame count do not change.
4. Test memory and runtime on the actual server. No fixed VRAM figures or chunk sizes are asserted without measurements.
5. Review identity, motion, lip sync, audio artifacts and joins. Keep failed experiments disabled and retain the baseline output.

Only add an option if the integration works and a measured practical benefit justifies it. Do not install competing addons with conflicting node IDs. These investigations are not prerequisites for testing the reviewed core lab.
