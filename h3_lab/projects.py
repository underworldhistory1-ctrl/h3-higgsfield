"""Project and Take Manifest Service for H3 Studio Lab.

Provides:
- Versioned project manifests with atomic revision checks (HTTP 409 on stale revision).
- Immutable take records and historical lineage preservation.
- Secure export/import of project bundles with zip traversal protection.
- Qwen Image to H3 asset handoff with detached ownership.
"""

import hashlib
import json
import logging
import os
import pathlib
import shutil
import threading
import time
import uuid
import zipfile

_LOG = logging.getLogger("h3_lab.projects")

MAX_BUNDLE_UNCOMPRESSED_BYTES = 500 * 1024 * 1024  # 500 MB safety limit


class ProjectService:
    def __init__(self, storage_root: str, asset_service=None):
        self.storage_root = pathlib.Path(storage_root).resolve()
        self.projects_dir = self.storage_root / "projects"
        self.bundles_dir = self.storage_root / "bundles"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.bundles_dir.mkdir(parents=True, exist_ok=True)
        self.asset_service = asset_service
        self._lock = threading.RLock()

    def _project_dir(self, project_id: str) -> pathlib.Path:
        if not project_id or not re_id(project_id):
            raise ValueError(f"Invalid project_id: {project_id}")
        return self.projects_dir / project_id

    def create_project(self, name: str = "Untitled Project", canvas: dict = None) -> dict:
        project_id = str(uuid.uuid4())
        pdir = self._project_dir(project_id)
        pdir.mkdir(parents=True, exist_ok=True)

        now = time.time()
        record = {
            "schema_version": 1,
            "project_id": project_id,
            "name": name,
            "revision": 1,
            "fps": 24,
            "canvas": canvas or {"width": 1280, "height": 704, "aspect_ratio": "16:9"},
            "assets": [],
            "clips": [],
            "accepted_take_ids": [],
            "exports": [],
            "created_at": now,
            "updated_at": now,
        }

        self._write_manifest(project_id, record)
        return record

    def get_project(self, project_id: str) -> dict:
        pdir = self._project_dir(project_id)
        manifest_path = pdir / "project.json"
        if not manifest_path.is_file():
            return None
        with self._lock:
            with open(manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)

    def list_projects(self) -> list:
        results = []
        with self._lock:
            for item in self.projects_dir.iterdir():
                if item.is_dir():
                    mf = item / "project.json"
                    if mf.is_file():
                        try:
                            with open(mf, "r", encoding="utf-8") as f:
                                data = json.load(f)
                            results.append({
                                "project_id": data.get("project_id"),
                                "name": data.get("name"),
                                "revision": data.get("revision"),
                                "canvas": data.get("canvas"),
                                "updated_at": data.get("updated_at"),
                            })
                        except Exception:
                            pass
        results.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
        return results

    def _write_manifest(self, project_id: str, record: dict):
        pdir = self._project_dir(project_id)
        pdir.mkdir(parents=True, exist_ok=True)
        target = pdir / "project.json"
        tmp = target.with_suffix(f".tmp.{uuid.uuid4().hex}")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)

    def save_project(self, project_id: str, data: dict, expected_revision: int) -> dict:
        """
        Atomic update with optimistic concurrency check.
        Raises ValueError with status_code=409 on stale revision.
        """
        with self._lock:
            current = self.get_project(project_id)
            if not current:
                raise KeyError(f"Project not found: {project_id}")

            current_rev = current.get("revision", 1)
            if expected_revision is not None and expected_revision != current_rev:
                err = ValueError(f"Stale revision: expected {expected_revision}, but server has {current_rev}")
                err.status_code = 409
                raise err

            updated = dict(data)
            updated["project_id"] = project_id
            updated["revision"] = current_rev + 1
            updated["updated_at"] = time.time()

            self._write_manifest(project_id, updated)
            return updated

    def duplicate_project(self, project_id: str, new_name: str = None) -> dict:
        current = self.get_project(project_id)
        if not current:
            raise KeyError(f"Project not found: {project_id}")

        new_name = new_name or f"{current.get('name', 'Project')} (Copy)"
        new_proj = self.create_project(name=new_name, canvas=current.get("canvas"))

        # Deep copy clips and assets
        new_proj["assets"] = list(current.get("assets", []))
        new_proj["clips"] = list(current.get("clips", []))
        new_proj["accepted_take_ids"] = list(current.get("accepted_take_ids", []))
        return self.save_project(new_proj["project_id"], new_proj, expected_revision=1)

    def export_bundle(self, project_id: str, include_takes: bool = True) -> pathlib.Path:
        project = self.get_project(project_id)
        if not project:
            raise KeyError(f"Project not found: {project_id}")

        pdir = self._project_dir(project_id)
        bundle_path = self.bundles_dir / f"project_{project_id}_{int(time.time())}.zip"

        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # Write project.json
            zf.write(pdir / "project.json", arcname="project.json")

            # Write assets
            for asset_item in project.get("assets", []):
                aid = asset_item.get("asset_id")
                if self.asset_service and aid:
                    rec = self.asset_service.get_asset(aid)
                    if rec:
                        src_path = self.asset_service.storage_root / rec["server_path"]
                        if src_path.is_file():
                            zf.write(src_path, arcname=f"assets/{src_path.name}")

        return bundle_path

    def import_bundle(self, zip_path: str) -> dict:
        """
        Safely import a project bundle with zip path traversal protection.
        """
        zp = pathlib.Path(zip_path).resolve()
        if not zp.is_file():
            raise FileNotFoundError(f"Bundle file not found: {zip_path}")

        new_project_id = str(uuid.uuid4())
        dest_dir = self._project_dir(new_project_id)
        dest_dir.mkdir(parents=True, exist_ok=True)

        uncompressed_total = 0

        with zipfile.ZipFile(zp, "r") as zf:
            for info in zf.infolist():
                # Path traversal check
                norm_name = os.path.normpath(info.filename)
                if norm_name.startswith("..") or os.path.isabs(norm_name):
                    raise ValueError(f"Malicious archive entry rejected: {info.filename}")

                uncompressed_total += info.file_size
                if uncompressed_total > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                    raise ValueError(f"Archive exceeds maximum uncompressed limit ({MAX_BUNDLE_UNCOMPRESSED_BYTES} bytes)")

            # Extract manifest first
            try:
                manifest_bytes = zf.read("project.json")
                manifest_data = json.loads(manifest_bytes.decode("utf-8"))
            except Exception as e:
                raise ValueError(f"Invalid project bundle: missing or corrupt project.json: {e}")

            # Re-key project
            manifest_data["project_id"] = new_project_id
            manifest_data["revision"] = 1
            manifest_data["name"] = f"{manifest_data.get('name', 'Imported')} (Imported)"
            manifest_data["updated_at"] = time.time()

            # Extract assets and register them
            for info in zf.infolist():
                if info.filename.startswith("assets/") and not info.is_dir():
                    raw_filename = os.path.basename(info.filename)
                    out_path = dest_dir / raw_filename
                    with zf.open(info) as src, open(out_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)

                    if self.asset_service:
                        # Register asset
                        rec = self.asset_service.register_asset(
                            str(out_path),
                            kind="image" if raw_filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) else "video",
                            original_name=raw_filename,
                            project_id=new_project_id
                        )
                        # Clean temp extracted file
                        out_path.unlink(missing_ok=True)

            self._write_manifest(new_project_id, manifest_data)
            return manifest_data

    def import_qwen_image(self, qwen_image_path: str, project_id: str,
                          as_role: str = "reference", alias: str = None) -> dict:
        """
        Copy and register a saved Qwen image as a project asset.
        Decoupled from original Qwen output file.
        """
        src = pathlib.Path(qwen_image_path).resolve()
        if not src.is_file():
            raise FileNotFoundError(f"Qwen image not found: {qwen_image_path}")

        if not self.asset_service:
            raise RuntimeError("AssetService required for Qwen image import")

        asset_record = self.asset_service.register_asset(
            str(src),
            kind="image",
            original_name=src.name,
            project_id=project_id,
            metadata={"source": "qwen_studio", "role": as_role}
        )

        project = self.get_project(project_id)
        if project:
            alias_name = alias or f"qwen_{src.stem[-4:]}"
            asset_entry = {
                "asset_id": asset_record["asset_id"],
                "alias": alias_name,
                "kind": "image",
                "role": as_role,
                "server_path": asset_record["server_path"],
                "content_hash": asset_record["content_hash"],
            }
            project.setdefault("assets", []).append(asset_entry)
            self.save_project(project_id, project, expected_revision=project.get("revision", 1))

        return asset_record


def re_id(val: str) -> bool:
    import re
    return bool(re.fullmatch(r"[0-9a-fA-F-]{8,64}", val))
