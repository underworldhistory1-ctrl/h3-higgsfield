# Reviewed lab data flow

## Working interfaces

studio.js owns the visible workspace; lab-ui.js owns project hydration, guide controls and review actions. web/h3 modules compile prompts and deterministic raw Comfy graphs, and expose the project API client. Saved assets restore as real media objects, never fake File placeholders.

Reference slots use native image → video/paired soundtrack → standalone audio order. Aliases remain stable through reordering; Custom maps to neutral native media tags. Structured mode validates native sections and connected indices, passing a valid payload through exactly once. Storyboard is a visual shot sheet with explicit user panel/shot order; it is not a scheduler or automatic panel-to-keyframe converter.

Frames and image guides can crop or contain with visible canvas fitting. Image references can preserve proportions, crop or contain. Durable originals remain unchanged; render inputs are separate. Native Max reference detail caps the short edge at 2048 and never upscales; the upload's long-edge cap is a different limit.

Native AddGuide supports an image, video frame batch, audio, or paired AV. Runtime object_info gates the advanced options. Video guide batches crop to the native 5+17k grid; the UI/builder validate effective spans. Source trims, 24 fps normalization and paired audio use one input timeline. New-content guide times add the context offset when extending.

## Durable server state

Canonical roots are injected explicitly: Comfy output, Comfy input, and output/lab_storage. Projects use atomic manifests and required optimistic revision checks. Assets live under lab_storage/assets. Asset upload attaches ownership and changes the project revision; the UI refreshes the server project after uploading before saving its draft.

A draft records source prompts by mode, aliases/roles, original asset IDs, frame fitting, guide timing, canvas, sampling settings, LoRAs and continuation source. Takes record the actual effective settings, immutable draft, output path, clip identity, parent take, unique frames and creation state. Acceptance is explicit. Replacing a take for a clip invalidates accepted descendants without deleting historical takes.

Imported bundles remap asset/context IDs and preserve source media. Imported take media lives under lab_storage/imported_takes/<project>; Comfy view paths and media actions use its canonical output path. Missing referenced payloads fail export/import instead of claiming a complete bundle. Filesystem operations reject absolute/traversal/symlink escapes and restrict take media to managed H3 MP4 outputs.

## Queue ownership and recovery

Before POST /lab/jobs, the browser saves the request ID and exact submission body. The server saves the request hash, graph/specification, ownership and leases before queuing. Duplicate requests do not queue twice. A definite rejection terminates the job; a lost acknowledgement stays unknown and retains leases.

The HTTP bridge calls only this process's loopback listener. GET jobs/by_request resolves a lost browser response; identical replay is safe when no server record exists. Request cancellation can reserve a durable cancellation tombstone before a late submission arrives. Cancellation intent survives acknowledgement races. Input deletion primitives respect active ownership.

Running interruption is permitted only when this ComfyUI implementation supports a targeted prompt ID; older global-only versions refuse and keep tracking. Queue removal, cancellation request and confirmed terminal cancellation are different states. CPU tests simulate queue and network boundaries; actual GPU stop behavior remains a live gate.

## Context and continuation

H3ReleaseForDecode keeps the ordinary temporary decode-recovery checkpoint and creates a separate durable safetensors context under output/h3_lab_contexts/<12-hex-token>. It preserves tensor dtypes, records shapes/hash, canvas/FPS and loader/LoRA/accelerator/file identities. H3LabLoadContext checks integrity and genuine two-stream H3 AV shapes before restoring a NestedTensor. Direct context reuse requires matching checkpoint/configuration and canvas.

A second path imports an owned video into a permanent server asset and re-encodes its AV context through the external ExistingVideoMaskedContext node. This is an advanced GPU experiment, not proof that FL2VA and Ref2VA sampler latents are interchangeable. Source dimensions and 24 fps must match; current server import is bounded to 40–362 frames and 500 MB.

The masked context nodes stay in the separately installed pinned external engine. This repository supplies H3LabLoadContext and H3LabTrimAV. The sampler retains the full continuation window for future context. After decode, H3LabTrimAV removes overlap from both images and PCM before CreateVideo, saving only unique new content. At 24 fps: target175−context39=136 new frames; 175+136+136=447 unique frames=18.625 seconds.

Accepted clips declare overlap_frames explicitly. Newly saved trimmed extensions use0. FFmpeg checks compatible canvases/24 fps, removes any explicitly declared legacy overlap, aligns decoded audio, concatenates and encodes AAC once. It verifies actual frames, audio and decode before atomic output promotion. AAC input files cannot restore lost original PCM precision; no perceptual seam guarantee is claimed.

Context usage/purge targets the runtime directory and protects accepted lineage and active/uncertain queue references. Purging context must never delete its video.

## Evidence boundary

Actual CPU browser, HTTP/service, safetensors and FFmpeg checks are in TEST_REPORT.md. There has been no H3 weight loading, local CUDA inference, remote deployment, or live model quality approval. Optional Upscaler/AudioRefine remain investigations, with no visible nonfunctional controls.

## External continuation source and separated controls

`Continue a video` is a collapsible source panel, separate from Reference library and Temporal keyframes. Multipart `/h3_studio/lab/media/upload_continuation` requires an active project, caps uploads at 500 MB, supports MP4/MOV/WebM/MKV/AVI, and retains only the selected last 2–15.1 seconds (default 5). Source duration may be shorter if it still supplies at least 40 canonical frames. It normalizes to 24 fps and the selected fixed canvas, using source-coordinate crop or proportion-preserving contain. The protected context remains the final 39 frames/1.625 seconds, independent of uploaded duration.

Preparation is CPU-only: one conversion at a time, one FFmpeg thread, input dimensions/pixels bounded to 8192/8.8MP, output at most 2048 per dimension, timeout180s, and Linux host/cgroup memory headroom of at least512MiB. Relative audio start timestamps are preserved, opening gaps and missing/end-of-track audio are padded with silence. Prepared media is verified by decoded frame count before atomic project source-take append. Cancellation waits for workers before cleanup and keeps committed source media. Full original uploads are deleted; the prepared source/asset/take remains private and portable. No queue submission or model load occurs during preparation.

Source preview and summary show selected duration, protected context and net new duration. Uploaded sources choose prompt-only FL2VA or References/Storyboard Ref2VA. Start/end Frames mode and canvas/aspect changes are blocked during continuation; timed guides remain available in the new-content timeline. Direct saved-latent checkpoint switches are refused. The explicit References action re-encodes an owned source video before changing FL2VA to Ref2VA. Mode prompts survive these actions; references cannot bypass the checkpoint gate via Qwen/results handoff.

A storyboard stays a semantic shot-plan reference; it never creates panel crops or keyframe times automatically. Upload an individual panel as a timed keyframe separately if needed. The prepared source is a root take, not automatically accepted; accepting source and continuation separately determines assembled sequence content.
