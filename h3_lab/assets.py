"""Durable Asset Management and Lease Registry for H3 Studio Lab.

Tracks project-owned assets and temporary uploaded media with reference counts
and durable leases. Prevents premature deletion during active/queued/unknown jobs.
"""

import hashlib
import json
import logging
import os
import pathlib
import re
import shutil
import threading
import time
import uuid

_LOG = logging.getLogger("h3_lab.assets")


class AssetService:
    """Manages owned media assets and temporary input leases."""

    def __init__(self, storage_root: str):
        self.storage_root = pathlib.Path(storage_root).resolve()
        self.assets_dir = self.storage_root / "assets"
        self.manifest_path = self.storage_root / "assets_manifest.json"
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._assets = {}  # asset_id -> dict
        self._leases = {}  # key (filename or asset_id) -> set of owner_ids
        self._load_manifest()

    def _load_manifest(self):
        with self._lock:
            if self.manifest_path.is_file():
                try:
                    with open(self.manifest_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        self._assets = data
                except (OSError, ValueError) as err:
                    _LOG.error("Failed to load asset manifest: %s", err)
                    self._assets = {}

    def _save_manifest_locked(self):
        tmp = self.manifest_path.with_suffix(f".tmp.{uuid.uuid4().hex}")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._assets, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.manifest_path)
        finally:
            if tmp.is_file():
                tmp.unlink(missing_ok=True)

    def hash_file(self, file_path: pathlib.Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def register_asset(self, source_path: str, kind: str, original_name: str,
                       project_id: str = None, metadata: dict = None) -> dict:
        """Register a permanent asset, copying it into assets_dir if not already there."""
        src = pathlib.Path(source_path).resolve()
        if not src.is_file():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        file_hash = self.hash_file(src)
        ext = src.suffix.lower()

        with self._lock:
            # Check for deduplication by content hash
            for aid, record in self._assets.items():
                if record.get("content_hash") == file_hash and record.get("kind") == kind:
                    target_path = self.storage_root / record["server_path"]
                    if target_path.is_file():
                        if project_id and project_id not in record.get("projects", []):
                            record.setdefault("projects", []).append(project_id)
                            self._save_manifest_locked()
                        return record

            asset_id = str(uuid.uuid4())
            rel_name = f"{asset_id}{ext}"
            rel_path = f"assets/{rel_name}"
            dest = self.assets_dir / rel_name

            shutil.copy2(src, dest)

            record = {
                "asset_id": asset_id,
                "kind": kind,
                "server_path": rel_path,
                "content_hash": file_hash,
                "original_name": original_name or src.name,
                "metadata": metadata or {},
                "projects": [project_id] if project_id else [],
                "created_at": time.time(),
            }
            self._assets[asset_id] = record
            self._save_manifest_locked()
            return record

    def get_asset(self, asset_id: str) -> dict:
        with self._lock:
            rec = self._assets.get(asset_id)
            if not rec:
                return None
            full_path = self.storage_root / rec["server_path"]
            if not full_path.is_file():
                return None
            return dict(rec)

    def attach_project(self, asset_id: str, project_id: str):
        with self._lock:
            record = self._assets.get(asset_id)
            if not record:
                raise ValueError("Unknown project asset")
            if project_id not in record.setdefault("projects", []):
                record["projects"].append(project_id)
                self._save_manifest_locked()

    def acquire_lease(self, identifier: str, owner_id: str):
        """Acquire a lease on an asset_id or a raw filename."""
        if not identifier or not owner_id:
            return
        with self._lock:
            self._leases.setdefault(identifier, set()).add(owner_id)

    def release_lease(self, identifier: str, owner_id: str):
        with self._lock:
            if identifier in self._leases:
                self._leases[identifier].discard(owner_id)
                if not self._leases[identifier]:
                    del self._leases[identifier]

    def release_all_leases_for_owner(self, owner_id: str):
        with self._lock:
            empty_keys = []
            for k, owners in self._leases.items():
                owners.discard(owner_id)
                if not owners:
                    empty_keys.append(k)
            for k in empty_keys:
                del self._leases[k]

    def is_leased(self, identifier: str) -> bool:
        with self._lock:
            return bool(self._leases.get(identifier))

    def get_lease_owners(self, identifier: str) -> list:
        with self._lock:
            return list(self._leases.get(identifier, []))

    def can_discard_input(self, filename: str) -> bool:
        """Consults lease registry before allowing discard."""
        with self._lock:
            if self.is_leased(filename):
                return False
            # Check if any asset references this filename
            for record in self._assets.values():
                if record.get("server_path", "").endswith(filename) or record.get("original_name") == filename:
                    if record.get("projects"):
                        return False
            return True
