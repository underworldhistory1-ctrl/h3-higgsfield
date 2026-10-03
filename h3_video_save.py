"""Save H3 video with audio, recovering from a PyAV codec failure via FFmpeg."""

from fractions import Fraction
import logging
import os
import pathlib
import shutil
import subprocess
import tempfile
import wave
import re

import folder_paths
import av
import numpy as np
import torch
from safetensors.torch import load_file, save_file


class H3ReleaseForDecode:
    """Checkpoint the sampled latent and evict generation models before video decode."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "samples": ("LATENT",),
            "token": ("STRING", {"default": ""}),
        }, "hidden": {"prompt": "PROMPT"}}

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "release"
    CATEGORY = "H3 Studio"

    def release(self, samples, token, prompt=None):
        if not re.fullmatch(r"[a-f0-9]{12}", token):
            raise ValueError("Invalid H3 render token")
        folder = pathlib.Path(folder_paths.get_output_directory()) / "latent"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"h3_studio_{token}.safetensors"
        part = folder / f"h3_studio_{token}.part"
        import comfy.model_management as model_management
        model_management.unload_all_models()
        model_management.soft_empty_cache()
        sampled = samples["samples"]
        if getattr(sampled, "is_nested", False):
            tensors = {f"nested_{index}": tensor.detach().to("cpu").contiguous()
                       for index, tensor in enumerate(sampled.unbind())}
            if not tensors:
                raise ValueError("H3 returned an empty nested latent")
        else:
            tensors = {"samples": sampled.detach().to("cpu").contiguous()}
        try:
            save_file(tensors, str(part))
            os.replace(part, target)
            from .h3_continuation import preserve_context
            preserve_context(target, token, prompt, tensors)
        finally:
            part.unlink(missing_ok=True)

        logging.info("H3 Studio: sampled latent saved at %s; generation models released before VAE decode", target)
        return (samples,)


class H3LoadSavedLatent:
    """Load a completed sampler checkpoint for a decode-only recovery prompt."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"token": ("STRING", {"default": ""})}}

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "load"
    CATEGORY = "H3 Studio"

    def load(self, token):
        if not re.fullmatch(r"[a-f0-9]{12}", token):
            raise ValueError("Invalid H3 render token")
        path = pathlib.Path(folder_paths.get_output_directory()) / "latent" / f"h3_studio_{token}.safetensors"
        if not path.is_file():
            raise FileNotFoundError(f"No sampled H3 latent for {token}")
        tensors = load_file(str(path), device="cpu")
        if "samples" in tensors:
            sampled = tensors["samples"]
        else:
            indices = sorted(int(key.removeprefix("nested_")) for key in tensors
                             if re.fullmatch(r"nested_\d+", key))
            if not indices or indices != list(range(len(indices))):
                raise ValueError("Saved H3 nested latent is incomplete")
            from comfy.nested_tensor import NestedTensor
            sampled = NestedTensor([tensors[f"nested_{index}"] for index in indices])
        return ({"samples": sampled},)


def _streams_ok(path):
    try:
        with av.open(str(path), mode="r") as container:
            kinds = {stream.type for stream in container.streams}
            return {"video", "audio"} <= kinds
    except (OSError, ValueError, av.error.FFmpegError):
        return False


def _ffmpeg_executable():
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        winget = pathlib.Path(local) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"
        if winget.is_file():
            return str(winget)
    raise FileNotFoundError("FFmpeg was not found; install it and restart ComfyUI")


def _write_audio_wav(audio, path):
    if not audio:
        raise ValueError("H3 produced no audio for the video")
    sample_rate = int(audio["sample_rate"])
    waveform = audio["waveform"]
    if sample_rate <= 0 or waveform.ndim != 3 or waveform.shape[0] != 1:
        raise ValueError("H3 produced an invalid audio buffer")
    samples = waveform[0].detach().to(device="cpu", dtype=torch.float32).numpy()
    if not samples.size:
        raise ValueError("H3 produced an empty audio buffer")
    invalid = np.count_nonzero(~np.isfinite(samples))
    if invalid:
        if invalid / samples.size > 0.01:
            raise ValueError("H3 audio contains too many invalid samples to save safely")
        logging.warning("H3 Studio: replaced %d invalid audio samples before FFmpeg save", invalid)
        samples = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
    pcm = (np.clip(samples, -1.0, 1.0).T * 32767).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(samples.shape[0])
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


def _save_with_ffmpeg(video, path):
    components = video.get_components()
    frames = components.images
    if frames.ndim != 4 or frames.shape[-1] != 3 or len(frames) == 0:
        raise ValueError("H3 produced an invalid video frame buffer")
    _, height, width, _ = frames.shape
    if width % 2 or height % 2:
        raise ValueError("H.264 MP4 requires even video dimensions")
    rate = Fraction(components.frame_rate)
    if rate <= 0:
        raise ValueError("H3 produced an invalid frame rate")
    with tempfile.TemporaryDirectory(prefix="h3-save-") as temp:
        audio_path = pathlib.Path(temp) / "audio.wav"
        _write_audio_wav(components.audio, audio_path)
        command = [
            _ffmpeg_executable(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{width}x{height}",
            "-framerate", str(float(rate)), "-i", "pipe:0", "-i", str(audio_path),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-af", "apad", "-shortest",
            "-movflags", "+faststart", "-f", "mp4", str(path),
        ]
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=errors)
            try:
                for frame in frames:
                    pixels = torch.nan_to_num(frame.detach().to(device="cpu", dtype=torch.float32))
                    pixels = (pixels.clamp(0, 1) * 255).to(torch.uint8).numpy()
                    process.stdin.write(pixels.tobytes())
                process.stdin.close()
                code = process.wait()
            except BaseException:
                process.kill()
                process.wait()
                raise
            if code:
                errors.seek(0)
                detail = errors.read()[-1200:].decode("utf-8", "replace").strip()
                raise RuntimeError("FFmpeg video save failed: " + detail)


class H3SaveVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video": ("VIDEO",),
                "filename_prefix": ("STRING", {"default": "video/h3_studio"}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ()
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "H3 Studio"

    def save(self, video, filename_prefix, prompt=None, extra_pnginfo=None):
        width, height = video.get_dimensions()
        folder, stem, counter, subfolder, _ = folder_paths.get_save_image_path(
            filename_prefix, folder_paths.get_output_directory(), width, height,
        )
        filename = f"{stem}_{counter:05}_.mp4"
        target = pathlib.Path(folder) / filename
        native_part = target.with_suffix(".native.part")
        ffmpeg_part = target.with_suffix(".ffmpeg.part")
        metadata = {"prompt": prompt} if prompt is not None else {}
        if isinstance(extra_pnginfo, dict):
            metadata.update(extra_pnginfo)
        try:
            try:
                video.save_to(str(native_part), format="mp4", codec="h264", metadata=metadata or None)
                if not _streams_ok(native_part):
                    raise ValueError("native save did not produce both video and audio streams")
                os.replace(native_part, target)
            except Exception as native_error:
                logging.warning("H3 Studio: native MP4 save failed; trying FFmpeg: %s", native_error)
                native_part.unlink(missing_ok=True)
                try:
                    _save_with_ffmpeg(video, ffmpeg_part)
                    if not _streams_ok(ffmpeg_part):
                        raise ValueError("FFmpeg did not produce both video and audio streams")
                    os.replace(ffmpeg_part, target)
                except Exception as fallback_error:
                    raise RuntimeError(
                        f"H3 video save failed in PyAV ({native_error}) and FFmpeg ({fallback_error})"
                    ) from fallback_error
        finally:
            native_part.unlink(missing_ok=True)
            ffmpeg_part.unlink(missing_ok=True)
        match = re.search(r"h3_studio_([a-f0-9]{12})$", filename_prefix)
        if match:
            checkpoint = pathlib.Path(folder_paths.get_output_directory()) / "latent" / f"h3_studio_{match.group(1)}.safetensors"
            checkpoint.unlink(missing_ok=True)
        file_info = {"filename": filename, "subfolder": subfolder, "type": "output"}
        return {"ui": {"videos": [file_info]}, "result": ()}
