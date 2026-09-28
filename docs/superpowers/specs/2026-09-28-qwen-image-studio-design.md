# Qwen Image Studio Integration Design

## Objective

Add a separate Qwen Image 2.1 workspace to H3 Studio while keeping the existing
MiniMax H3 video workflow unchanged. Both workspaces use one private ComfyUI
process, one authenticated web origin, and one GPU queue.

## Product structure

The existing page remains the Video workspace. A new `image.html` page is the
Image workspace. The header provides explicit `Create Video` and `Create Image`
navigation. Each page owns its drafts, uploads, active job and result library;
both observe the same ComfyUI queue so the interface never implies that two GPU
jobs can run concurrently.

Image workspace modes:

- **Create:** prompt-only generation, optional native RGBA transparency.
- **Edit:** one primary image plus up to nine additional references. The primary
  image determines the edit canvas unless the user explicitly selects a custom
  output size. Prompt references use `<image1>` through `<image10>`.

The workspace exposes aspect ratio, output scale, steps, seed and model
precision. Advanced sampler internals remain fixed to the official path: CFG 1,
Euler and simple scheduler. Negative prompt stays hidden while CFG is 1.

## Visual system

The Image workspace reuses the established black, graphite and acid-lime H3
Studio system, spacing, compact creation rail, large preview stage and library.
The identifying element is an `I2` model tile and image-specific copy; controls
and vocabulary remain plain English. Video and image controls never appear in
the same creation form.

## Graph contract

Both Create and Edit use only ComfyUI core nodes:

- `UNETLoader`
- `CLIPLoader` with type `qwen_image`
- `VAELoader`
- `TextEncodeQwenImage21`
- `QwenImage21Cache` for Edit only
- `EmptyLatentImage` for Create only
- `KSampler`
- `VAEDecode`
- `QwenStudioSaveImage`

Create uses the selected width and height. Edit passes uploaded images to
`TextEncodeQwenImage21`, uses its latent output and applies the prefix cache with
device `auto`, dtype `default`. References are uploaded under collision-proof
session names and deleted after completion, cancellation or error.

## Precision profiles

The downloader installs named files, never an entire Hugging Face repository:

| Profile | Diffusion model | Text encoder | VAE | Weight total |
|---|---|---|---|---:|
| INT8 ConvRot | `qwen_image_2.1_int8_convrot.safetensors` | `qwen3vl_8b_int8_convrot.safetensors` | `qwen_image_2.1_vae_bf16.safetensors` | 17.28 GB |
| BF16 | `qwen_image_2.1_bf16.safetensors` | `qwen3vl_8b_bf16.safetensors` | shared VAE | 32.41 GB |

The server reports each profile independently. The UI never offers a missing or
size-mismatched profile. `QWEN_IMAGE_PROFILES=int8` is the 100 GB Salad default;
`int8,bf16` requires at least 150 GB. INT8 is called **Fast / efficient** and
BF16 **Maximum precision**; the interface makes no lossless claim for INT8.

## Backend and persistence

`qwen_image.py` owns the model manifest, graph builder and image saver.
`__init__.py` exposes:

- `GET /h3_studio/image_readiness`
- `GET /h3_studio/image_library`
- `GET /h3_studio/image_file`
- `POST /h3_studio/image_details`
- `POST /h3_studio/delete_image`

Images and sidecar metadata live under `output/images/`. Only finished images
enter the library. Sidecars include mode, profile, dimensions, steps, seed,
references, transparency, prompt and elapsed time. Temporary uploads are not
listed and are removed through the existing session cleanup endpoints.

## Deployment

The Salad image pins ComfyUI commit
`3b4c0b0e457cf0a51cf3038e0a6750d8f96ce251`, which contains both the corrected
MiniMax H3 tiled VAE and Qwen Image 2.1 core nodes. Startup verifies both markers
before declaring ready. Qwen model download is controlled by
`QWEN_IMAGE_PROFILES`; H3 remains required. Model files use pinned sizes and
SHA-256 hashes. Downloads resume, fail closed on hash mismatch, and run before
ComfyUI starts.

## Failure prevention

- Preflight checks required free space before Qwen downloads.
- Part files remain resumable and never count as installed weights.
- A profile appears only after all three exact files verify.
- The browser opens WebSocket before queue submission and restores the active
  prompt from local storage after refresh.
- Upload validation rejects unsupported types, images over 25 MB, empty prompts,
  missing Edit sources, more than ten references and invalid dimensions.
- Queue errors expose the failing node message instead of reporting completion.
- Library entries are created only by the dedicated saver after PNG/WebP output
  exists and can be opened.
- H3 and Qwen share the Comfy queue; one page cannot bypass an active job.
- Build checks validate JavaScript syntax, Python imports, model manifests,
  graph contracts, required Comfy node source markers and nginx configuration.

## Verification boundary

Local tests prove graph shape, routes, file accounting, UI syntax and container
construction. A real RTX 5090 render is still required after Salad allocation to
prove CUDA execution, peak VRAM, image quality and measured ETA on that host.

