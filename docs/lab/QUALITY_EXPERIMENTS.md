# H3 Studio Lab — Bounded Quality Experiments

This document records the evaluation, architecture, and promotion criteria for the two optional quality investigations specified in Task 8.

---

## 1. Latent Upscaler / Refine

### Target Repository
`LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler` (commit `40316cf008b2fd8663263270669eb4da23f89d2c`, MIT).

### Invariants & Gotchas
1. **Node Schema Incompatibility**:
   Do NOT install the community `Plus` fork alongside the original upscaler. They register identical node IDs (`MiniMaxH3LatentUpscaler`) with conflicting widget schemas, leading to silent graph execution failures in ComfyUI.
2. **Audio Protection**:
   H3 latent upscaling operates strictly on the 16-channel video latent. Audio latents must be detached prior to the refine pass and re-muxed untouched. Refinement must NEVER resample or drift dialogue.
3. **Memory Limits**:
   Temporal chunking must be strictly enforced. Full 15-second latents (362 frames) at 2K exceed 48 GB VRAM during full-window cross-attention. Chunk size must default to 32–64 frames with overlap blending.

### Verdict & Gating
- **Local/Development Status:** Schemas cataloged; dependency pinned in `lab/dependencies.lock.json`.
- **GPU Testing Gate:** Pending remote validation on the authorized online GPU server.
- **Promotion Rule:** Gated behind Advanced settings. It will NOT replace the `Original quality` default pipeline.

---

## 2. AudioRefine

### Target Repository
`Adudeguyman/ComfyUI-H3-AudioRefine` (commit `d78d34f2f1100b0422047e9d784a3bdb4e1061d3`, MIT).

### Invariants & Gotchas
1. **Video Latent Invariance**:
   AudioRefine targets the audio latent stream to reduce noise in high-frequency background ambience. A tensor-level invariance check must prove that video latents (`samples[:, :16, ...]`) remain bit-identical before and after the audio refine node.
2. **Computational Cost**:
   Audio refinement steps run additional diffusion iterations on the audio VAE / denoiser. It must not be marketed as a zero-cost post-process or TTS engine.
3. **Turbo Compatibility**:
   When used with the FL2VA Turbo LoRA, audio refine steps must be matched to Turbo's 4–8 step schedule to avoid audio over-smoothing.

### Verdict & Gating
- **Local/Development Status:** Architecture reviewed; adapter contracts isolated.
- **GPU Testing Gate:** Pending remote validation on the authorized online GPU server.
- **Promotion Rule:** Off by default. Available only when explicitly enabled on compatible audio workloads.
