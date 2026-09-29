# H3 Studio UI behavior

The interface language is English. The layout follows the public Higgsfield Create Video pattern: a compact creation rail, a large preview workspace, and a generated video library. It uses H3 Studio branding and its own controls.

## Creation modes

| Mode | Inputs shown | H3 checkpoint | Submission rule |
|---|---|---|---|
| Text | Prompt | FL2VA | Non-empty prompt |
| Frames | Start/end upload and prompt | FL2VA | At least one frame; start-frame aspect must be close to the canvas |
| References | Named image/video/audio cards and prompt | Ref2VA | At least one reference; every attached asset mentioned with `@name` |

Each mode keeps a separate prompt draft when switching tabs. The server readiness response checks the required model files and actual registered ComfyUI node classes. Modes and reference types whose checkpoint or loader is absent are disabled; the connection message names missing dependencies for the selected mode. Uploaded files remain in browser memory during the tab session and are re-uploaded for each run. A draft prompt and settings survive browser reload in local storage; file objects do not. The UI asks the user to reattach files after restoring a Frames or References draft.

Image reference mentions become `<Subject N>` and are tied to `<Picture N>` in the six-section Ref2VA prompt. Video and audio mentions become `<Video N>` and `<Audio N>`; a selected video soundtrack gets the matching paired audio input and its own audio tag. Reference order is images, videos, then standalone audio. A selected soundtrack counts toward the conservative three-audio-reference and 15-second combined audio limits. Frame mode uses the MiniMax first/last alignment line and the three-section base prompt schema.

## Model and LoRA choices

The output canvas is an actual H3 Base dimension. Changing duration, canvas, steps, method, mode, references, or reference image detail updates every size estimate. No LoRA or accelerator is enabled on first connection. The Render method selector offers Original quality, Spectrum, MotionCache, and the pinned H3 Turbo LoRA; the latter three stay disabled unless their exact node or adapter is installed. Spectrum and MotionCache insert an exclusive patch after Sigma Shift. Turbo inserts the installed FL2VA model-only LoRA at strength 0.9 and sets six steps in Text/Frames only; References uses Ref2VA, so this Turbo choice is disabled there. It cannot be combined with another LoRA until validated. Installed style LoRAs are listed from ComfyUI; enabling one inserts `LoraLoaderModelOnly` between the H3 model loader and sampler. The toggle is unavailable if that node is absent. Multiple enabled LoRAs are applied in displayed order, each at its selected strength. An obvious FL2VA/Ref2VA filename mismatch is stopped before submission; Combat V2 is restricted to FL2VA modes and Realism People requires its trigger word in the user's prompt. Other compatibility is unknown until tested on the server.

The default 20-step graph does not use Turbo, cache, or sparse attention. Max reference image detail is the References default for identity fidelity; it does not raise output resolution. The 2K regeneration model is not part of this local bundle.

## Generation and results

| Event | Visible result | File handling |
|---|---|---|
| ComfyUI offline or extension missing | Connection error; generation blocked | Local draft remains |
| Required model missing | Missing model names shown; generation blocked | No upload starts |
| Bad prompt, unknown/missing mention, invalid step/seed, wrong start-frame aspect | Inline error | No upload starts |
| Upload fails or media violates limits | Inline error, form enabled again | Uploaded temporary inputs are wiped |
| Queued / model loading | Indeterminate or early progress, broad ETA | Uploaded inputs stay until job ends |
| Sampler steps | 10–90% progress, ETA updated from actual step times | Job remains tied to its prompt ID |
| Decode / mux / save | 91–98% progress with approximate remaining time | Inputs remain until saved output is found |
| WebSocket drops or page reloads | Queue/history polling restores job ID, elapsed time, queue position/count and eventual output | Job and uploaded inputs remain on server |
| Render succeeds | 100%, preview, actual time, library card, video/audio check result | Temporary inputs wiped; output remains in the server library |
| Cancel before queue response | Upload aborted; late prompt ID cancelled if returned | Temporary inputs wiped |
| Cancel after queue response | Deletes this job if pending; interrupts only when it is the active running job | Temporary inputs wiped after cancellation request succeeds |
| Generation error | Error message and retry available | Temporary inputs wiped |

The initial ETA is a broad estimate. A job stores its settings on the server when submitted; a completed render stores its measured time when the browser or ComfyUI history sees the result. The same configuration gives the strongest calibration; related samples with different LoRAs or modes produce a wider, lower-confidence range. Queue wait does not inflate the stored render time. Server history can clear on restart, so persistent clip metadata supplies later estimates. The history card shows measured render time when available. The progress bar reaches 100% after ComfyUI reports the saved output and the automated stream check finishes. That check catches absent or silent audio, corrupt streams and fully black sampled video. Listening and looking at the clip remains necessary for sync or artifact quality.

## Library and closing

- **All generated videos:** The server scans its H3 output folder every refresh and the page refreshes the library while open or on focus. It shows the total count, eight cards at a time, and a Show more button until every file is visible. Each card has a server-generated thumbnail, date, video length, render time when recorded, and a Details button.
- **Details:** A small, scrollable dialog shows mode, model, canvas, length, render method, steps, seed, LoRAs, frame/reference inputs and prompt when that metadata exists. Escape, Close, or backdrop click dismisses it; focus returns to its opening button. Older clips can have partial or unavailable settings.
- **Pin:** Marks a favorite. Pinning is optional; every video remains on this server until deletion or loss of server storage.
- **Download:** Downloads the selected video to the browser's device.
- **Delete / Delete unpinned:** Requires confirmation, then removes the selected file or unpinned files from the server. The library refreshes afterward.
- **Reload or close tab:** A queued or running job continues. Refresh restores the job ID and queue/history progress. Finished clips remain in the server library. Temporary uploaded inputs are cleaned after the job or when abandoned.
- **Switch server:** The new server loads its own LoRAs and generated video library. The old server's output files are not deleted by the switch.

Cloud GPU rendering, media decoding, and LoRA compatibility require a real connected server for final validation. The local page can verify visual interactions and emitted ComfyUI graph JSON only.

## Image workspace

`image.html` is one Qwen Image workspace with optional named references. With no image attached, it builds the empty-latent text-to-image graph. With one to ten images, it attaches them to `TextEncodeQwenImage21`; `@name` in the visible prompt resolves to the corresponding `<imageN>` in attachment order. The first reference determines the output canvas unless the user selects a new output size. A reference can guide a new composition or an edit; this is not a pixel-preserving editing guarantee. The interface offers optional fitting of oversized references before upload and only enables installed model profiles. Transparent output is offered only without references.

Upload progress precedes the Comfy queue. Sampling progress and ETA use WebSocket step events; refresh restores the active prompt ID and polls the server's sampler state as well as queue/history. Completed PNGs appear in the current preview and a paged server library with settings, measured time, download and delete. Failed, interrupted, or missing server outputs never appear as completed images. Browser-selected reference files must be reattached after a fresh page load for a new render.

The interface supports one submitted render at a time. Its queue display counts all ComfyUI jobs and shows this job's position. After it finishes or is cancelled, a new render can be sent. History cards include server file time and measured render time when recorded. Settings metadata and measured samples for new clips survive a ComfyUI restart while the output disk persists.
