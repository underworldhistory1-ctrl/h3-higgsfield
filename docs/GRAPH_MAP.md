# H3 Studio graph contract

The user-facing app is `web/index.html` + `web/studio.js`. Keep these files byte-for-byte when moving to a new server; rebuilding a similar UI will lose behavior. The ComfyUI graphs for Frames and References are assembled by `graph()` in `web/studio.js` from the user's controls and uploaded file names. The four JSON files in `workflows/` are the fixed quality and short smoke starting points.

## Common graph

| Node ID | ComfyUI class | Purpose / connection |
|---|---|---|
| 1 | `UNETLoader` | FL2VA for Text/Frames, Ref2VA for References |
| 2 | `MiniMaxH3SigmaShift` | Model from node 1 or the last selected LoRA; video shift 12, audio shift 3 |
| 3 | `CLIPLoader` | H3 Qwen3VL text encoder |
| 4 / 5 | `VAELoader` | H3 video VAE / H3 audio VAE |
| 6 | `MiniMaxH3ImageToVideo` or `MiniMaxH3ReferenceToVideo` | Prompt, output width/height and length; Ref2VA also gets audio VAE and reference detail |
| 7 | `ConditioningZeroOut` | Negative conditioning from node 6 |
| 8 | `KSampler` | Positive and latent from node 6, negative from node 7, model from node 2 or optional speed patch |
| 9 / 10 | `VAEDecode` / `VAEDecodeAudio` | Decode video and audio from the **same** sampler samples |
| 11 | `CreateVideo` | The decoded video frames plus decoded native audio, 24 fps |
| 12 | `H3SaveVideo` | MP4/H.264 with audio; retries through FFmpeg if PyAV fails, prefix `video/h3_studio_<token>` |

The Original method uses 20 steps by default with `res_multistep`, `simple`, CFG 1, denoise 1. The UI offers 124/175/226/294/362 frames and a practical 20–100 step input. Landscape canvas choices are 1344×768, 1280×704, 1024×576, and 864×480; Portrait transposes each pair (768×1344, 704×1280, 576×1024, 480×864). Node 6 receives the selected width and height directly in all three modes. The ratios are approximate because dimensions use H3-friendly multiples. The Turbo path uses 4–8 steps, default 6.

## Mode branches

- **Text:** Node 6 is FL2VA with no picture inputs.
- **Frames:** Node 6 is FL2VA. Node 13 (`LoadImage`) connects `first_frame` when selected; node 14 connects `last_frame` when selected. One or both frames are required.
- **References:** Node 6 is Ref2VA. Uploaded images connect through `LoadImage` to `ref_images.ref_image_N`; videos through `LoadVideo` and `GetVideoComponents` to `ref_videos.ref_video_N`; optional matching video audio goes to `ref_video_audios.ref_video_audio_N` with the **same video index**; audio files connect through `LoadAudio` to `ref_audios.ref_audio_N`. IDs start at 20 and grow as needed. The UI orders references by image, video, audio and rewrites their `@name` mentions to H3 tags before submitting.

Video `Use as` choices are prompt guidance over the same Ref2VA video input, not separate identity or motion conditioning channels. `Performance + camera` asks the model to follow movement and camera without retaining the source actor or location; `Whole scene` follows source subjects and setting only where the shot prompt does not explicitly replace them. Neither provides frame-locked actor replacement. The source video soundtrack reaches Ref2VA only when its audio checkbox is enabled; a visual video reference alone does not supply spoken words. For exact dialogue with a new voice, the user must write the lines and language in the prompt, and the generated audio still requires review.

The prompt shows thumbnail chips for attached frames and mentioned image/video references. These chips are a visual representation of the actual uploaded files, not separate conditioning inputs. In Frames mode `@start` and `@end` compile to the corresponding `<Picture 1>`/`<Picture 2>` tokens, matching the node 6 first/last frame inputs. In References mode `@sara` and other named mentions compile to the numbered `<Subject N>`, `<Video N>` or `<Audio N>` tokens while the actual files remain connected to node 6. The picture numbers shown next to image chips match the image input order; a `<Subject N>` definition points to its `<Picture N>`.

## Optional model path

- User-selected style LoRAs chain through `LoraLoaderModelOnly` starting at ID 50, in displayed order and selected strength, before Sigma Shift.
- Turbo is another `LoraLoaderModelOnly` at ID 60, strength 0.9, selected by Render method **only in Text/Frames (FL2VA)**. The installed file targets FL2VA; References uses Ref2VA and disables this Turbo choice. The UI disallows mixing Turbo with other LoRAs until that combination is validated.
- Spectrum **or** MotionCache occupies ID 61 after Sigma Shift and before KSampler. They are exclusive. Both are experimental approximations; neither is part of Original quality.

The extension's `/h3_studio/readiness` route checks model sizes and registered node names. The frontend checks mode-specific nodes and installed LoRA file names before `/prompt`. The readiness result does not prove output quality or audio sync. Keep the `web/` implementation and backend API together when deploying; copying only workflows is insufficient.

During an active render, `/h3_studio/job_progress` reads ComfyUI's sampler progress for node 8. The Studio also saves the last observed step in browser session storage. On refresh it restores that step and polls the server, so a lost or replaced WebSocket does not reset the visible progress. This endpoint becomes available after ComfyUI loads the updated extension on restart; deployment must not restart an active render.

`MiniMaxH3AddGuide` and latent `denoise_mask` are supported by newer ComfyUI builds but are not part of the Studio graphs above. Arbitrary timeline anchors and masked regeneration require explicit user inputs and separate graph paths; they do not silently enhance Text, Frames, or References. `Use as` reference roles affect prompt guidance, while actual reference files continue to use node 6's image, video and audio inputs.

## Qwen Image 2.1 graphs

The Image workspace is separate from all H3 video branches. Both Create and Edit load the selected Qwen diffusion model, Qwen VL encoder and Qwen 2.1 VAE, then use `TextEncodeQwenImage21`, `KSampler` (Euler/simple, CFG 1), `VAEDecode` and `QwenStudioSaveImage`.

- **Create:** `EmptyLatentImage` supplies the selected 1/1.6/4 MP canvas. Transparency adds Qwen's documented RGBA instruction.
- **Edit:** `LoadImage` supplies 1–10 ordered photos through `images.image_N`; the first photo defines the canvas unless the user explicitly resizes it. `QwenImage21Cache` is enabled and the encoder's latent output feeds the sampler.
- **Profiles:** INT8 and BF16 are selectable only when every exact file in that profile has the expected size. Salad defaults to INT8.
