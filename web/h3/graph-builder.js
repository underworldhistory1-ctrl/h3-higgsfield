/**
 * H3 Studio Lab — Deterministic Graph Builder
 *
 * Constructs ComfyUI workflow graphs for Text, Frames, References, Temporal Guides,
 * and AV Continuation without reading DOM state.
 *
 * Guarantees:
 * - Deterministic node IDs and link mappings.
 * - Links first_frame to node 15 and last_frame to node 16 (never colliding with 13).
 * - Exact video index alignment for paired ref_video_audio soundtracks.
 * - Chaining native AddGuide nodes when temporal guides are specified.
 * - Integration with GeneratedAVMaskedContext and ExistingVideoMaskedContext.
 */

(function(root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.H3GraphBuilder = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  'use strict';

  const MODEL_FL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors";
  const MODEL_REF = "minimax_h3_ref2va_pruned_int8_convrot.safetensors";
  const TURBO_NAME = "experimental/minimax_h3_fl2v_lightx2v_turbo_4to8step_v0.1-v1.0_768p_v4_step600_dareties.safetensors";

  function buildGraph(renderSpec, resolvedAssets = {}, capabilities = {}) {
    const mode = renderSpec.mode || 'text';
    const isRefs = mode === 'refs';
    const token = renderSpec.token || '000000000000';
    const prompt = renderSpec.compiled_prompt || '';
    const width = Number(renderSpec.width) || 1280;
    const height = Number(renderSpec.height) || 704;
    const length = Number(renderSpec.target_frames || renderSpec.duration) || 124;
    const seed = Number(renderSpec.seed) || 42;
    const steps = Number(renderSpec.steps) || (renderSpec.render_method === 'turbo' ? 6 : 20);
    const method = renderSpec.render_method || 'native';

    const g = {
      "1": { class_type: "UNETLoader", inputs: { unet_name: isRefs ? MODEL_REF : MODEL_FL, weight_dtype: "default" } },
      "2": { class_type: "MiniMaxH3SigmaShift", inputs: { model: ["1", 0], shift_video: 12, shift_audio: 3 } },
      "3": { class_type: "CLIPLoader", inputs: { clip_name: "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", type: "minimax" } },
      "4": { class_type: "VAELoader", inputs: { vae_name: "minimax_h3_video_vae_fp16.safetensors" } },
      "5": { class_type: "VAELoader", inputs: { vae_name: "minimax_h3_audio_vae_fp32.safetensors" } },
      "6": {
        class_type: isRefs ? "MiniMaxH3ReferenceToVideo" : "MiniMaxH3ImageToVideo",
        inputs: {
          clip: ["3", 0],
          vae: ["4", 0],
          prompt: prompt,
          width: width,
          height: height,
          length: length,
        }
      },
      "7": { class_type: "ConditioningZeroOut", inputs: { conditioning: ["6", 0] } },
      "8": {
        class_type: "KSampler",
        inputs: {
          model: ["2", 0],
          seed: seed,
          steps: steps,
          cfg: 1,
          sampler_name: "res_multistep",
          scheduler: "simple",
          positive: ["6", 0],
          negative: ["7", 0],
          latent_image: ["6", 1],
          denoise: 1
        }
      },
      "13": { class_type: "H3ReleaseForDecode", inputs: { samples: ["8", 0], token: token } },
      "9": { class_type: "VAEDecode", inputs: { samples: ["13", 0], vae: ["4", 0] } },
      "10": { class_type: "VAEDecodeAudio", inputs: { samples: ["13", 0], vae: ["5", 0] } },
      "11": { class_type: "CreateVideo", inputs: { images: ["9", 0], fps: 24, audio: ["10", 0] } },
      "12": { class_type: "H3SaveVideo", inputs: { video: ["11", 0], filename_prefix: "video/h3_studio_" + token } },
    };

    // LoRA chain
    let modelLink = ["1", 0];
    const loras = (renderSpec.loras || []).filter(l => l.enabled);
    loras.forEach((lora, idx) => {
      const loraId = String(50 + idx);
      g[loraId] = {
        class_type: "LoraLoaderModelOnly",
        inputs: { model: modelLink, lora_name: lora.name, strength_model: Number(lora.strength) || 1.0 }
      };
      modelLink = [loraId, 0];
    });

    if (method === "turbo" && !isRefs) {
      g["60"] = {
        class_type: "LoraLoaderModelOnly",
        inputs: { model: modelLink, lora_name: TURBO_NAME, strength_model: 0.9 }
      };
      modelLink = ["60", 0];
    }
    g["2"].inputs.model = modelLink;

    if (method === "spectrum") {
      g["61"] = {
        class_type: "SpectrumApplyMiniMaxH3",
        inputs: {
          model: ["2", 0], enabled: true, blend_weight: 0.5, degree: 1, ridge_lambda: 0.1,
          window_size: 2, flex_window: 0.75, warmup_steps: 1, tail_actual_steps: 1,
          max_history: 8, debug: false, history_storage: "system_ram",
          offline_archive_storage: "system_ram", audio_blend_weight: 0, offline_smoothing_replay: true
        }
      };
      g["8"].inputs.model = ["61", 0];
    } else if (method === "motioncache") {
      g["61"] = {
        class_type: "MiniMaxH3MotionCache",
        inputs: {
          model: ["2", 0], reuse_threshold: 0.15, motion_strength: 1, warmup_steps: 4,
          max_consecutive_skips: 2, start_percent: 0.15, end_percent: 0.95,
          subsample_factor: 8, verbose: false
        }
      };
      g["8"].inputs.model = ["61", 0];
    }

    // Frames mode inputs
    if (mode === 'frames') {
      if (resolvedAssets.first) {
        g["15"] = { class_type: "LoadImage", inputs: { image: resolvedAssets.first } };
        g["6"].inputs.first_frame = ["15", 0];
      }
      if (resolvedAssets.last) {
        g["16"] = { class_type: "LoadImage", inputs: { image: resolvedAssets.last } };
        g["6"].inputs.last_frame = ["16", 0];
      }
    }

    // References mode inputs
    if (isRefs) {
      g["6"].inputs.audio_vae = ["5", 0];
      g["6"].inputs.ref_image_size = renderSpec.ref_image_size || "match";

      let nextNodeId = 20;
      let imgSlot = 0;
      let vidSlot = 0;
      let audSlot = 0;

      const ordered = renderSpec.references || [];
      for (const ref of ordered) {
        const file = resolvedAssets[ref.asset_id] || resolvedAssets[ref.alias];
        if (!file) continue;

        if (ref.kind === 'image') {
          const loadId = String(nextNodeId++);
          g[loadId] = { class_type: "LoadImage", inputs: { image: file } };
          g["6"].inputs[`ref_images.ref_image_${imgSlot++}`] = [loadId, 0];
        } else if (ref.kind === 'video') {
          const loadId = String(nextNodeId++);
          const splitId = String(nextNodeId++);
          const currentSlot = vidSlot++;
          g[loadId] = { class_type: "LoadVideo", inputs: { file: file } };
          g[splitId] = { class_type: "GetVideoComponents", inputs: { video: [loadId, 0] } };
          g["6"].inputs[`ref_videos.ref_video_${currentSlot}`] = [splitId, 0];
          if (ref.use_audio) {
            g["6"].inputs[`ref_video_audios.ref_video_audio_${currentSlot}`] = [splitId, 1];
          }
        } else if (ref.kind === 'audio') {
          const loadId = String(nextNodeId++);
          g[loadId] = { class_type: "LoadAudio", inputs: { audio: file } };
          g["6"].inputs[`ref_audios.ref_audio_${audSlot++}`] = [loadId, 0];
        }
      }
    }

    // Native temporal guides (MiniMaxH3AddGuide)
    let currentPositive = ["6", 0];
    const guides = renderSpec.guides || [];
    if (guides.length > 0) {
      guides.forEach((guide, gIdx) => {
        const guideNodeId = String(70 + gIdx * 2);
        const guideImgNodeId = String(70 + gIdx * 2 + 1);
        const guideFile = resolvedAssets[guide.asset_id] || guide.filename;

        g[guideImgNodeId] = { class_type: "LoadImage", inputs: { image: guideFile } };
        g[guideNodeId] = {
          class_type: "MiniMaxH3AddGuide",
          inputs: {
            positive: currentPositive,
            latent: ["6", 1],
            frame_idx: Number(guide.frame_idx) || 0,
            vae: ["4", 0],
            image: [guideImgNodeId, 0],
          }
        };
        currentPositive = [guideNodeId, 0];
      });
      g["8"].inputs.positive = currentPositive;
    }

    // AV Latent Continuation
    if (renderSpec.continuation) {
      const cont = renderSpec.continuation;
      const contNodeId = "85";
      const loadLatentNodeId = "86";

      if (cont.type === "generated") {
        g[loadLatentNodeId] = {
          class_type: "H3LoadSavedLatent",
          inputs: { token: cont.source_token }
        };
        g[contNodeId] = {
          class_type: "MiniMaxH3GeneratedAVMaskedContext",
          inputs: {
            latent: ["6", 1],
            source_latent: [loadLatentNodeId, 0],
            context_length: Number(cont.context_length) || 39,
            audio_feather_ticks: Number(cont.audio_feather_ticks) || 8,
          }
        };
        g["8"].inputs.latent_image = [contNodeId, 0];
      } else if (cont.type === "imported") {
        const loadVidNodeId = "87";
        g[loadVidNodeId] = {
          class_type: "LoadVideo",
          inputs: { file: cont.source_file }
        };
        const splitVidNodeId = "88";
        g[splitVidNodeId] = {
          class_type: "GetVideoComponents",
          inputs: { video: [loadVidNodeId, 0] }
        };
        g[contNodeId] = {
          class_type: "MiniMaxH3ExistingVideoMaskedContext",
          inputs: {
            latent: ["6", 1],
            vae: ["4", 0],
            audio_vae: ["5", 0],
            source_frames: [splitVidNodeId, 0],
            source_audio: [splitVidNodeId, 1],
            source_fps: 24,
            context_length: Number(cont.context_length) || 39,
            crop: "disabled",
            audio_feather_ticks: Number(cont.audio_feather_ticks) || 8,
          }
        };
        g["8"].inputs.latent_image = [contNodeId, 0];
      }
    }

    return g;
  }

  return {
    buildGraph,
  };
});
