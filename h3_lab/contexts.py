"""Durable AV Latent Context Serialization and Lifecycle for H3 Studio Lab.

Safely serializes and fingerprints sampled audio/video latent pairs using
safetensors and JSON manifests. Rejects unvalidated or mismatched contexts
without resorting to unsafe serialization (pickle).
"""

import hashlib
import json
import logging
import os
import pathlib
import threading
import time
import uuid

from .paths import owned_path

_LOG = logging.getLogger("h3_lab.contexts")


class ContextService:
    def __init__(self, storage_root: str):
        self.storage_root = pathlib.Path(storage_root).resolve()
        self.contexts_dir = self.storage_root / "contexts"
        self.manifest_path = self.storage_root / "contexts_manifest.json"
        self.contexts_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._contexts = {}
        self._load_manifest()

    def _load_manifest(self):
        with self._lock:
            if self.manifest_path.is_file():
                try:
                    with open(self.manifest_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        self._contexts = data
                except Exception as e:
                    _LOG.error("Failed to load context manifest: %s", e)
                    self._contexts = {}

    def _save_manifest_locked(self):
        tmp = self.manifest_path.with_suffix(f".tmp.{uuid.uuid4().hex}")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._contexts, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.manifest_path)

    def calculate_fingerprint(self, width: int, height: int, fps: int = 24,
                              model_id: str = "minimax_h3", adapter_version: str = "v1") -> str:
        payload = f"{width}x{height}@{fps}:{model_id}:{adapter_version}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def save_context(self, video_tensor, audio_tensor,
                     project_id: str, take_id: str, width: int, height: int,
                     frame_count: int, fps: int = 24, model_identities: dict = None) -> dict:
        """
        Durable save of AV latent pair to safetensors.
        video_tensor: [1, 24, T, H/16, W/16]
        audio_tensor: [1, 32, 2, T40]
        """
        import torch
        from safetensors.torch import save_file
        context_id = str(uuid.uuid4())
        filename = f"ctx_{context_id}.safetensors"
        target_path = self.contexts_dir / filename
        part_path = target_path.with_suffix(".part")

        # Validate shapes and dtypes
        if video_tensor.ndim != 5 or video_tensor.shape[:2] != (1, 24) or list(video_tensor.shape[-2:]) != [height // 16, width // 16]:
            raise ValueError(f"Invalid video latent tensor shape: {video_tensor.shape}")
        if audio_tensor.ndim != 4 or audio_tensor.shape[:3] != (1, 32, 2):
            raise ValueError(f"Invalid audio latent tensor shape: {audio_tensor.shape}")

        tensors = {
            "video_latent": video_tensor.detach().to("cpu", dtype=torch.float32).contiguous(),
            "audio_latent": audio_tensor.detach().to("cpu", dtype=torch.float32).contiguous(),
        }

        try:
            save_file(tensors, str(part_path))
            os.replace(part_path, target_path)
        finally:
            if part_path.is_file():
                part_path.unlink(missing_ok=True)

        # Hash payload
        h = hashlib.sha256()
        with open(target_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        payload_hash = h.hexdigest()

        model_key = json.dumps(model_identities or {}, sort_keys=True)
        fingerprint = self.calculate_fingerprint(width, height, fps, model_key, "MultiRef-v1")

        record = {
            "schema_version": 1,
            "context_id": context_id,
            "project_id": project_id,
            "take_id": take_id,
            "fingerprint": fingerprint,
            "canvas": {"width": width, "height": height},
            "fps": fps,
            "audio_fps": 40,
            "frame_count": frame_count,
            "video_latent_shape": list(video_tensor.shape),
            "audio_latent_shape": list(audio_tensor.shape),
            "model_identities": model_identities or {},
            "adapter_version": "MultiRef-v1",
            "server_path": f"contexts/{filename}",
            "content_hash": payload_hash,
            "file_size": target_path.stat().st_size,
            "created_at": time.time(),
        }

        with self._lock:
            self._contexts[context_id] = record
            self._save_manifest_locked()

        return record

    def load_context(self, context_id: str, target_width: int = None, target_height: int = None,
                     model_identities=None, fps=24) -> tuple:
        """
        Loads AV latent tensors and validates compatibility against target dimensions.
        Returns (video_tensor, audio_tensor, record).
        """
        with self._lock:
            rec = self._contexts.get(context_id)
            if not rec:
                raise KeyError(f"Context not found: {context_id}")

        canvas = rec.get("canvas", {})
        if target_width and target_height:
            if canvas.get("width") != target_width or canvas.get("height") != target_height:
                raise ValueError(
                    f"Context resolution mismatch: context is {canvas.get('width')}x{canvas.get('height')}, "
                    f"but target is {target_width}x{target_height}"
                )

        full_path = owned_path(self.storage_root, rec["server_path"])
        if not full_path.is_file():
            raise FileNotFoundError(f"Context safetensors file missing: {full_path}")

        if rec.get("fps") != fps:
            raise ValueError("Context FPS mismatch")
        if model_identities is not None and rec.get("model_identities") != model_identities:
            raise ValueError("Context model identities mismatch")
        fingerprint = self.calculate_fingerprint(canvas.get("width"), canvas.get("height"), fps,
            json.dumps(rec.get("model_identities", {}), sort_keys=True), rec.get("adapter_version"))
        if rec.get("fingerprint") != fingerprint:
            raise ValueError("Context compatibility fingerprint mismatch")
        digest = hashlib.sha256()
        with full_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
        if digest.hexdigest() != rec.get("content_hash"):
            raise ValueError("Context content hash mismatch")
        from safetensors.torch import load_file
        tensors = load_file(str(full_path), device="cpu")
        if "video_latent" not in tensors or "audio_latent" not in tensors:
            raise ValueError(f"Corrupt context file: missing video_latent or audio_latent streams")

        for key, shape_key in (("video_latent", "video_latent_shape"), ("audio_latent", "audio_latent_shape")):
            if list(tensors[key].shape) != rec.get(shape_key):
                raise ValueError("Context tensor shape mismatch")
        return tensors["video_latent"], tensors["audio_latent"], dict(rec)

    def get_disk_usage(self) -> dict:
        with self._lock:
            total_bytes = sum(c.get("file_size", 0) for c in self._contexts.values())
            return {
                "count": len(self._contexts),
                "total_bytes": total_bytes,
                "total_mb": round(total_bytes / (1024 * 1024), 2),
            }

    def purge_context(self, context_id: str):
        with self._lock:
            rec = self._contexts.pop(context_id, None)
            if rec:
                self._save_manifest_locked()
                path = owned_path(self.storage_root, rec.get("server_path", ""), require_file=False)
                if path.is_file():
                    path.unlink(missing_ok=True)
