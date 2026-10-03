"""Unit tests for ProjectService, atomic revisions, and bundle safety."""

import io
import hashlib
import json
import pathlib
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from h3_lab.assets import AssetService
from h3_lab.projects import ProjectService


class TestH3Projects(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_root = pathlib.Path(self.temp_dir.name)
        self.asset_service = AssetService(str(self.storage_root))
        self.project_service = ProjectService(str(self.storage_root), asset_service=self.asset_service)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_project_crud_and_atomic_revision_check(self):
        # Create project
        proj = self.project_service.create_project(name="Action Sequence 1")
        pid = proj["project_id"]
        self.assertEqual(proj["revision"], 1)

        # Update project with valid expected revision
        proj["canvas"] = {"width": 1024, "height": 1024, "aspect_ratio": "1:1"}
        updated = self.project_service.save_project(pid, proj, expected_revision=1)
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["canvas"]["aspect_ratio"], "1:1")

        # Stale revision update (e.g. from an out-of-date browser tab) raises 409
        stale_data = dict(updated)
        stale_data["name"] = "Stale Edit"
        with self.assertRaises(ValueError) as ctx:
            self.project_service.save_project(pid, stale_data, expected_revision=1)
        self.assertEqual(getattr(ctx.exception, "status_code", None), 409)

        # Reload from disk simulates server restart
        loaded = self.project_service.get_project(pid)
        self.assertEqual(loaded["revision"], 2)
        self.assertEqual(loaded["name"], "Action Sequence 1")

    def test_bundle_export_and_safe_import(self):
        proj = self.project_service.create_project(name="Bundle Test")
        pid = proj["project_id"]

        # Register an asset
        dummy_img = self.storage_root / "hero_shot.png"
        dummy_img.write_bytes(b"\x89PNG\r\n\x1a\nherodata")
        asset_rec = self.asset_service.register_asset(str(dummy_img), kind="image", original_name="hero_shot.png", project_id=pid)

        proj["assets"].append({
            "asset_id": asset_rec["asset_id"],
            "alias": "hero",
            "kind": "image",
            "role": "character identity"
        })
        self.project_service.save_project(pid, proj, expected_revision=1)

        # Export bundle
        bundle_file = self.project_service.export_bundle(pid)
        self.assertTrue(bundle_file.is_file())

        # Import into new project
        imported = self.project_service.import_bundle(str(bundle_file))
        new_pid = imported["project_id"]
        self.assertNotEqual(new_pid, pid)
        self.assertTrue(imported["name"].startswith("Bundle Test"))
        self.assertEqual(len(imported["assets"]), 1)

    def test_bundle_traversal_rejection(self):
        # Create a malicious zip with ../evil.txt
        malicious_zip_path = self.storage_root / "evil.zip"
        with zipfile.ZipFile(malicious_zip_path, "w") as zf:
            zf.writestr("../../../../../evil.txt", "pwned")
            zf.writestr("project.json", '{"schema_version": 1, "name": "Evil"}')

        with self.assertRaises(ValueError) as ctx:
            self.project_service.import_bundle(str(malicious_zip_path))
        self.assertIn("rejected", str(ctx.exception).lower())

    def test_context_bundle_import_without_python311_file_digest(self):
        output_root = self.storage_root / "output"
        service = ProjectService(str(self.storage_root), self.asset_service, output_root)
        token = "a" * 12
        payload = b"portable-context-payload"
        bundle = self.storage_root / "context_bundle.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("project.json", json.dumps({"name": "Context", "assets": [], "takes": []}))
            archive.writestr(f"contexts/{token}.safetensors", payload)
            archive.writestr(f"contexts/{token}.json", json.dumps({
                "token": token, "sha256": hashlib.sha256(payload).hexdigest(),
            }))

        # Python 3.10 on the deployed server does not provide file_digest.
        with patch.object(hashlib, "file_digest", create=True):
            del hashlib.file_digest
            imported = service.import_bundle(str(bundle))

        contexts = list((output_root / "h3_lab_contexts").glob("*.json"))
        self.assertEqual(len(contexts), 1)
        metadata = json.loads(contexts[0].read_text(encoding="utf-8"))
        self.assertNotEqual(metadata["token"], token)
        self.assertEqual(contexts[0].with_suffix(".safetensors").read_bytes(), payload)
        self.assertIsNotNone(service.get_project(imported["project_id"]))

    def test_qwen_image_handoff_survives_deletion_of_source(self):
        proj = self.project_service.create_project(name="Qwen Integration")
        pid = proj["project_id"]

        # Simulate generated Qwen image in Qwen output directory
        qwen_dir = self.storage_root / "qwen_outputs"
        qwen_dir.mkdir(parents=True)
        qwen_img = qwen_dir / "qwen_studio_test_00001.png"
        qwen_img.write_bytes(b"\x89PNG\r\n\x1a\nqwenpixeldata")

        # Import into H3 project
        imported_asset = self.project_service.import_qwen_image(str(qwen_img), project_id=pid, as_role="reference")
        self.assertIn("asset_id", imported_asset)

        # Delete original Qwen image
        qwen_img.unlink()
        self.assertFalse(qwen_img.is_file())

        # Project asset must still be intact and valid!
        owned_asset = self.asset_service.get_asset(imported_asset["asset_id"])
        self.assertIsNotNone(owned_asset)
        asset_file = self.storage_root / owned_asset["server_path"]
        self.assertTrue(asset_file.is_file(), "Copied project asset must survive deletion of original Qwen result")
        self.assertEqual(asset_file.read_bytes(), b"\x89PNG\r\n\x1a\nqwenpixeldata")


if __name__ == "__main__":
    unittest.main()
