"""Continuation Adapter and Timing Mathematics for H3 Studio Lab.

Implements MiniMax H3 temporal and AV latent continuation invariants:
- Video grid: 24 fps, lengths follow 17*k + 5 (124, 175, 226, 294, 362...)
- Audio latent grid: 40 Hz
- Protected context grid: 51*k + 39 (39, 90, 141, 192...)
- Default context: 39 frames (1.625 s, 65 audio latent ticks)
- Net duration accounting: target_frames - context_length
- Fixture invariant: 175 + 136 + 136 = 447 unique frames (18.625 s)
"""

import math

FPS = 24.0
AUDIO_LATENT_HZ = 40.0
VALID_CONTEXT_LENGTHS = [39, 90, 141, 192, 243, 294]


def is_valid_target_frames(n: int) -> bool:
    return n >= 5 and (n % 17 == 5)


def snap_target_frames(n: int) -> int:
    n = max(5, int(n))
    while n % 17 != 5:
        n += 1
    return n


def is_valid_context_length(n: int) -> bool:
    return n >= 39 and ((n - 39) % 51 == 0)


def snap_context_length(requested: int, max_source_frames: int, target_frames: int) -> int:
    """
    Snaps requested context length to valid 51k+39 grid such that:
    context < max_source_frames and context < target_frames.
    """
    candidates = [c for c in VALID_CONTEXT_LENGTHS if c < max_source_frames and c < target_frames]
    if not candidates:
        raise ValueError(
            f"Source frames ({max_source_frames}) or target ({target_frames}) too short for minimum 39-frame context"
        )
    # Pick closest candidate <= requested
    valid = [c for c in candidates if c <= requested]
    return valid[-1] if valid else candidates[0]


def calculate_extension_timing(target_frames: int = 175, context_length: int = 39,
                               source_frames: int = 175) -> dict:
    target_frames = snap_target_frames(target_frames)
    context_length = snap_context_length(context_length, source_frames, target_frames)

    net_new_frames = target_frames - context_length
    audio_context_ticks = int(round(context_length / FPS * AUDIO_LATENT_HZ))
    net_new_audio_ticks = int(round(net_new_frames / FPS * AUDIO_LATENT_HZ))

    return {
        "fps": FPS,
        "audio_latent_hz": AUDIO_LATENT_HZ,
        "target_frames": target_frames,
        "target_seconds": round(target_frames / FPS, 4),
        "context_frames": context_length,
        "context_seconds": round(context_length / FPS, 4),
        "net_new_frames": net_new_frames,
        "net_new_seconds": round(net_new_frames / FPS, 4),
        "audio_context_ticks": audio_context_ticks,
        "net_new_audio_ticks": net_new_audio_ticks,
    }


def calculate_sequence_frames(first_take_frames: int, extension_takes_count: int,
                              context_length: int = 39, target_frames: int = 175) -> int:
    """
    Computes exact total unique frames for a chained sequence.
    Example: 175 + 2 extensions of (175 - 39 = 136) = 447 frames.
    """
    net_per_ext = target_frames - context_length
    return first_take_frames + extension_takes_count * net_per_ext
