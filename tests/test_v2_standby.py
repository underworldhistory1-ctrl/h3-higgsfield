"""Standby never submits to production or imports inference dependencies."""
import io
import pathlib
import subprocess
import sys
import tempfile
import unittest
import aiohttp
from aiohttp.test_utils import AioHTTPTestCase

from deploy.v2_standby import LAB_SERVICES, ProductionReader, create_app


class Reader:
    def __init__(self):
        self.calls = []
    async def get(self, path):
        self.calls.append(path)
        if path == "/object_info":
            return {"UNETLoader": {}}
        if path == "/h3_studio/readiness":
            return {"models": {"fl2va": True}, "nodes": {}, "ready": True}
        return {"devices": [{"name": "Production GPU"}]}


class StandbyTests(AioHTTPTestCase):
    async def get_application(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.directory.name)
        self.output, self.inputs = self.root / "v2-output", self.root / "v2-input"
        self.reader = Reader()
        return create_app(self.output, self.inputs, self.root / "models", reader=self.reader)

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.directory.cleanup()

    async def test_inference_mutations_are_forbidden_without_jobs(self):
        for path in ("/prompt", "/queue", "/interrupt", "/h3_studio/lab/jobs",
                     "/h3_studio/lab/jobs/by_request/client/cancel"):
            response = await self.client.post(path, json={"request_id": "client", "render_spec": {}})
            self.assertEqual(response.status, 403)
        self.assertEqual(self.reader.calls, [])
        self.assertIsNone(self.app[LAB_SERVICES]["jobs"].find_by_request("client"))

    async def test_project_assets_remain_available_in_isolated_storage(self):
        response = await self.client.post("/h3_studio/lab/projects", json={"name": "Review V2"})
        self.assertEqual(response.status, 201)
        from PIL import Image
        image = io.BytesIO()
        Image.new("RGB", (64, 32), "red").save(image, format="PNG")
        form = aiohttp.FormData()
        form.add_field("file", image.getvalue(), filename="review.png", content_type="image/png")
        response = await self.client.post("/h3_studio/lab/assets", data=form)
        self.assertEqual(response.status, 201)
        asset = (await response.json())["asset"]
        response = await self.client.post(f"/h3_studio/lab/assets/{asset['asset_id']}/resolve", json={"fit": "preserve"})
        resolved = await response.json()
        self.assertTrue((self.inputs / resolved["filename"]).is_file())
        response = await self.client.get("/view", params={"type": "input", "filename": resolved["filename"]})
        self.assertEqual(await response.read(), image.getvalue())
        self.assertEqual(self.reader.calls, [])

    async def test_empty_library_and_standby_status_are_truthful(self):
        response = await self.client.get("/h3_studio/library")
        self.assertEqual((await response.json())["items"], [])
        response = await self.client.get("/h3_studio/lab/capabilities")
        result = await response.json()
        self.assertTrue(result["standby"])
        self.assertFalse(result["inference_enabled"])
        self.assertFalse(result["ready"])
        response = await self.client.get("/h3_studio/readiness")
        result = await response.json()
        self.assertFalse(result["inference_enabled"])
        self.assertFalse(result["ready"])
        response = await self.client.get("/view", params={"filename": "../production.mp4"})
        self.assertEqual(response.status, 404)


class StandbyImportTests(unittest.TestCase):
    def test_import_does_not_load_torch_or_comfy(self):
        result = subprocess.run([sys.executable, "-c",
            "import deploy.v2_standby,sys; assert 'torch' not in sys.modules; assert 'folder_paths' not in sys.modules; print('CPU_ONLY')"],
            cwd=pathlib.Path(__file__).parents[1], capture_output=True, text=True, check=True, timeout=20)
        self.assertIn("CPU_ONLY", result.stdout)

    def test_production_reader_rejects_arbitrary_hosts(self):
        with self.assertRaises(ValueError):
            ProductionReader("http://example.com:8188")

    def test_nonempty_production_storage_is_never_adopted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            production = root / "production-output"
            production.mkdir()
            existing = production / "running-render.mp4"
            existing.write_bytes(b"protected")
            with self.assertRaisesRegex(ValueError, "unmarked storage"):
                create_app(production, root / "v2-input", root / "models")
            self.assertEqual(existing.read_bytes(), b"protected")
            self.assertFalse((production / ".h3-v2-standby-owned").exists())
