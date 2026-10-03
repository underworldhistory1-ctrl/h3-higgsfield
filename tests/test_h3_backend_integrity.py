"""CPU integration coverage for portable ownership and exact AV assembly."""
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from h3_lab.assets import AssetService
from h3_lab.assembly import assemble_sequence
from h3_lab.projects import ProjectService
from h3_lab.paths import owned_path


class BackendIntegrityTests(unittest.TestCase):
    def test_image_resolve_creates_missing_input_directory(self):
        from PIL import Image
        from h3_lab.images import resolve_image
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "source.png"
            Image.new("RGB", (64, 32), "blue").save(source)
            input_root = root / "fresh" / "input"
            result = resolve_image(source, input_root, "fixture", {"fit": "crop", "width": 64, "height": 64})
            self.assertTrue((input_root / result["filename"]).is_file())

    def test_duplicate_preserves_draft_and_take_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            projects = ProjectService(temporary)
            project = projects.create_project()
            project.update(draft={"prompt": "locked scene"}, takes=[{"take_id": "t1"}], accepted_take_ids=["t1"])
            projects.save_project(project["project_id"], project, 1)
            copy = projects.duplicate_project(project["project_id"])
            self.assertEqual(copy["draft"], project["draft"])
            self.assertEqual(copy["takes"], project["takes"])
            self.assertEqual(copy["accepted_take_ids"], ["t1"])

    def test_bundle_import_resolves_assets_on_fresh_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary)
            source_assets = AssetService(str(base / "source"))
            source_projects = ProjectService(str(base / "source"), source_assets)
            project = source_projects.create_project()
            image = base / "hero.png"
            image.write_bytes(b"fixture")
            record = source_assets.register_asset(str(image), "image", "hero.png", project["project_id"])
            project["assets"] = [{"asset_id": record["asset_id"], "server_path": record["server_path"], "alias": "hero"}]
            source_projects.save_project(project["project_id"], project, 1)
            destination_assets = AssetService(str(base / "destination"))
            destination_projects = ProjectService(str(base / "destination"), destination_assets)
            imported = destination_projects.import_bundle(str(source_projects.export_bundle(project["project_id"])))
            restored = destination_assets.get_asset(imported["assets"][0]["asset_id"])
            self.assertIsNotNone(restored)
            self.assertNotEqual(restored["asset_id"], record["asset_id"])
            self.assertIn(imported["project_id"], restored["projects"])

    def test_bundle_export_rejects_missing_referenced_media(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            assets = AssetService(str(root))
            projects = ProjectService(str(root), assets)
            project = projects.create_project()
            source = root / "hero.png"
            source.write_bytes(b"fixture")
            rec = assets.register_asset(str(source), "image", "hero.png", project["project_id"])
            project["assets"] = [{"asset_id": rec["asset_id"], "server_path": rec["server_path"]}]
            project["draft"] = {"reference": {"assetId": rec["asset_id"]}}
            projects.save_project(project["project_id"], project, 1)
            (root / rec["server_path"]).unlink()
            with self.assertRaisesRegex(ValueError, "media is missing"):
                projects.export_bundle(project["project_id"])

    def test_containment_rejects_relative_and_absolute_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name in ("../private.txt", "/etc/passwd", "C:/private.txt", "folder\\private.txt"):
                with self.assertRaises(ValueError):
                    owned_path(temporary, name, require_file=False)

    def test_project_export_cannot_package_nonmedia_server_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            record = root / "private.json"
            record.write_text('{"prompt":"private"}')
            projects = ProjectService(str(root / "lab_storage"), output_root=root)
            project = projects.create_project()
            project["takes"] = [{"take_id": "malicious", "output_file": "private.json"}]
            projects.save_project(project["project_id"], project, 1)
            with self.assertRaisesRegex(ValueError, "managed H3"):
                projects.export_bundle(project["project_id"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_assembly_removes_declared_overlap_with_single_av_encode(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary)
            clips = []
            for number in range(2):
                path = base / f"clip '{number}.mp4"
                subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "color=blue:size=64x64:rate=24:duration=1", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000:duration=1", "-c:v", "libx264", "-c:a", "aac", str(path)],
                    check=True, capture_output=True, timeout=30)
                clips.append(str(path))
            result = assemble_sequence(clips, str(base / "final.mp4"), overlap_frames=[0, 6])
            self.assertEqual(result["frames"], 42)
            self.assertLess(abs(result["duration"] - 42 / 24), 0.06)
            self.assertEqual(result["streams"], ["video", "audio"])
