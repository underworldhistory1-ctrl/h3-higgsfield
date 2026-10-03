"""Integration tests for H3 Studio Lab API routes."""

import pathlib
import asyncio
import tempfile
import unittest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from h3_lab.routes import register_lab_routes


class QueueClient:
    def __init__(self):
        self.submissions = []
    async def submit_prompt(self, spec, extra):
        self.submissions.append((spec, extra))
        return {"prompt_id": "prompt-1"}
    async def get_queue(self):
        return {"queue_running": [], "queue_pending": []}
    async def get_history(self, prompt_id=None):
        return {"prompt-1": {"prompt": [0, "prompt-1", {}, self.submissions[0][1]],
                "outputs": {"save": {"videos": [{"filename": "result.mp4"}]}},
                "status": {"status_str": "success"}}}


class TestH3LabRoutes(AioHTTPTestCase):
    async def get_application(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_root = pathlib.Path(self.temp_dir.name)
        app = web.Application()
        self.queue = QueueClient()
        self.services = register_lab_routes(app, str(self.storage_root),
            output_root=self.storage_root, input_root=self.storage_root, comfy_client=self.queue)
        return app

    def tearDown(self):
        super().tearDown()
        self.temp_dir.cleanup()

    @unittest_run_loop
    async def test_capabilities_endpoint(self):
        resp = await self.client.request("GET", "/h3_studio/lab/capabilities")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("ffmpeg", data)
        self.assertIn("native_nodes", data)
        self.assertIn("ready", data)

    @unittest_run_loop
    async def test_project_lifecycle_and_revision_conflict(self):
        # 1. Create project
        resp = await self.client.request(
            "POST", "/h3_studio/lab/projects",
            json={"name": "Api Project", "canvas": {"width": 1280, "height": 704}}
        )
        self.assertEqual(resp.status, 201)
        proj = await resp.json()
        pid = proj["project_id"]
        self.assertEqual(proj["revision"], 1)

        # 2. Get project
        resp = await self.client.request("GET", f"/h3_studio/lab/projects/{pid}")
        self.assertEqual(resp.status, 200)
        fetched = await resp.json()
        self.assertEqual(fetched["name"], "Api Project")

        # 3. Update project with valid revision
        fetched["name"] = "Updated Name"
        resp = await self.client.request(
            "POST", f"/h3_studio/lab/projects/{pid}",
            json={"project": fetched, "expected_revision": 1}
        )
        self.assertEqual(resp.status, 200)
        saved = await resp.json()
        self.assertEqual(saved["revision"], 2)

        # 4. Stale update returns 409 Conflict
        stale_update = dict(saved)
        stale_update["name"] = "Stale Conflicting Name"
        resp = await self.client.request(
            "POST", f"/h3_studio/lab/projects/{pid}",
            json={"project": stale_update, "expected_revision": 1}
        )
        self.assertEqual(resp.status, 409)

    @unittest_run_loop
    async def test_idempotent_job_submission_endpoint(self):
        spec = {"mode": "text", "prompt": "Desert horizon", "seed": 42}

        # 1. Submit initial job
        resp = await self.client.request(
            "POST", "/h3_studio/lab/jobs",
            json={"request_id": "api_req_1", "render_spec": spec}
        )
        self.assertEqual(resp.status, 201)
        data1 = await resp.json()
        self.assertFalse(data1["is_duplicate"])
        job_id = data1["job"]["job_id"]
        self.assertEqual(data1["job"]["state"], "queued")
        self.assertEqual(len(self.queue.submissions), 1)

        # 2. Submit exact duplicate request -> 200 OK and same job_id
        resp_dup = await self.client.request(
            "POST", "/h3_studio/lab/jobs",
            json={"request_id": "api_req_1", "render_spec": spec}
        )
        self.assertEqual(resp_dup.status, 200)
        data2 = await resp_dup.json()
        self.assertTrue(data2["is_duplicate"])
        self.assertEqual(data2["job"]["job_id"], job_id)
        self.assertEqual(len(self.queue.submissions), 1)

        # 3. Same request_id with different payload -> 409 Conflict
        spec_diff = {"mode": "text", "prompt": "Different prompt", "seed": 99}
        resp_conflict = await self.client.request(
            "POST", "/h3_studio/lab/jobs",
            json={"request_id": "api_req_1", "render_spec": spec_diff}
        )
        self.assertEqual(resp_conflict.status, 409)

    async def test_queue_history_is_exposed_and_cancel_is_awaited(self):
        response = await self.client.post("/h3_studio/lab/jobs", json={"request_id": "history", "render_spec": {}})
        job_id = (await response.json())["job"]["job_id"]
        response = await self.client.get(f"/h3_studio/lab/jobs/{job_id}")
        self.assertEqual((await response.json())["state"], "completed")
        response = await self.client.post(f"/h3_studio/lab/jobs/{job_id}/cancel")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["state"], "completed")

    async def test_qwen_absolute_path_cannot_be_exported(self):
        response = await self.client.post("/h3_studio/lab/projects", json={"name": "Safety"})
        project = await response.json()
        private = self.storage_root / "private.txt"
        private.write_text("secret")
        response = await self.client.post("/h3_studio/lab/import_qwen_image", json={
            "image_filename": str(private), "project_id": project["project_id"]})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.services["projects"].get_project(project["project_id"])["assets"], [])

    async def test_asset_upload_read_and_resolve(self):
        import aiohttp
        import io
        from PIL import Image
        stream = io.BytesIO()
        Image.new("RGB", (96, 64), "red").save(stream, format="PNG")
        image_bytes = stream.getvalue()
        form = aiohttp.FormData()
        form.add_field("file", image_bytes, filename="hero.png", content_type="image/png")
        response = await self.client.post("/h3_studio/lab/assets", data=form)
        self.assertEqual(response.status, 201)
        asset = (await response.json())["asset"]
        response = await self.client.get(f"/h3_studio/lab/assets/{asset['asset_id']}/file")
        self.assertEqual(await response.read(), image_bytes)
        response = await self.client.post(f"/h3_studio/lab/assets/{asset['asset_id']}/resolve")
        resolved = await response.json()
        self.assertTrue((self.storage_root / resolved["filename"]).is_file())
        self.assertEqual((resolved["width"], resolved["height"]), (96, 64))
        response = await self.client.post(f"/h3_studio/lab/assets/{asset['asset_id']}/resolve",
            json={"width": 128, "height": 128, "fit": "contain"})
        self.assertEqual(response.status, 200)
        fitted = await response.json()
        with Image.open(self.storage_root / fitted["filename"]) as image:
            self.assertEqual(image.size, (128, 128))
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 0))
        source = self.services["assets"].get_asset(asset["asset_id"])
        self.assertEqual((self.services["storage_root"] / source["server_path"]).read_bytes(), image_bytes)
        response = await self.client.post(f"/h3_studio/lab/assets/{asset['asset_id']}/resolve",
            json={"width": 127, "height": 128, "fit": "crop"})
        self.assertEqual(response.status, 400)

    async def test_resolve_rejects_disguised_unsupported_image(self):
        import io
        import aiohttp
        from PIL import Image
        stream = io.BytesIO()
        Image.new("RGB", (32, 32)).save(stream, format="GIF")
        form = aiohttp.FormData()
        form.add_field("file", stream.getvalue(), filename="unsafe.png", content_type="image/png")
        response = await self.client.post("/h3_studio/lab/assets", data=form)
        asset = (await response.json())["asset"]
        response = await self.client.post(f"/h3_studio/lab/assets/{asset['asset_id']}/resolve")
        self.assertEqual(response.status, 400)

    async def test_runtime_context_usage_and_accepted_lineage_purge_protection(self):
        token = "012345678abc"
        directory = self.storage_root / "h3_lab_contexts"
        directory.mkdir()
        payload = directory / (token + ".safetensors")
        payload.write_bytes(b"context")
        (directory / (token + ".json")).write_text('{"fps":24}')
        response = await self.client.get("/h3_studio/lab/contexts/usage")
        self.assertEqual((await response.json())["total_bytes"], 7)
        project = self.services["projects"].create_project()
        project["takes"] = [{"take_id": "take1", "output_file": "video/h3_studio_" + token + "_00001_.mp4"}]
        project["accepted_take_ids"] = ["take1"]
        project = self.services["projects"].save_project(project["project_id"], project, 1)
        response = await self.client.post(f"/h3_studio/lab/contexts/{token}/purge")
        self.assertEqual(response.status, 409)
        self.assertTrue(payload.is_file())
        project["accepted_take_ids"] = []
        self.services["projects"].save_project(project["project_id"], project, project["revision"])
        response = await self.client.post(f"/h3_studio/lab/contexts/{token}/purge")
        self.assertEqual(response.status, 200)
        self.assertFalse(payload.exists())

    async def test_definite_queue_rejection_releases_leases(self):
        async def reject(spec, extra):
            raise ValueError("Invalid workflow")
        self.queue.submit_prompt = reject
        response = await self.client.post("/h3_studio/lab/jobs", json={"request_id": "rejected",
            "render_spec": {}, "asset_leases": ["reference.png"]})
        self.assertEqual(response.status, 400)
        job = self.services["jobs"].get_job_by_request_id("rejected")
        self.assertEqual(job["state"], "failed")
        self.assertFalse(self.services["assets"].is_leased("reference.png"))

    async def test_cancel_reservation_blocks_late_submission(self):
        response = await self.client.post("/h3_studio/lab/jobs/by_request/cancel-first/cancel")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["state"], "cancelled")
        response = await self.client.post("/h3_studio/lab/jobs", json={"request_id": "cancel-first", "render_spec": {"workflow": {}}})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["job"]["state"], "cancelled")
        self.assertEqual(self.queue.submissions, [])
        from h3_lab.jobs import JobService
        restarted = JobService(str(self.services["storage_root"]))
        self.assertEqual(restarted.find_by_request("cancel-first")["state"], "cancelled")

    async def test_lost_submission_ack_is_recovered_by_request_identity(self):
        async def lost_ack(spec, extra):
            self.queue.submissions.append((spec, extra))
            raise ConnectionError("Response lost after queue acceptance")
        self.queue.submit_prompt = lost_ack
        response = await self.client.post("/h3_studio/lab/jobs", json={"request_id": "lost-ack", "render_spec": {"workflow": {}}})
        self.assertEqual(response.status, 503)
        response = await self.client.get("/h3_studio/lab/jobs/by_request/lost-ack")
        rec = await response.json()
        self.assertEqual(rec["prompt_id"], "prompt-1")
        self.assertEqual(rec["state"], "completed")
        response = await self.client.post("/h3_studio/lab/jobs", json={"request_id": "lost-ack", "render_spec": {"workflow": {}}})
        self.assertEqual(response.status, 200)
        self.assertEqual(len(self.queue.submissions), 1)

    async def test_cancel_unknown_submission_retains_leases_until_queue_visibility(self):
        job, _ = self.services["jobs"].submit_job("uncertain", {}, ["frame.png"])
        self.services["jobs"].update_job(job["job_id"], state="unknown")
        async def empty_history(prompt_id=None):
            return {}
        self.queue.get_history = empty_history
        response = await self.client.post("/h3_studio/lab/jobs/by_request/uncertain/cancel")
        self.assertEqual((await response.json())["state"], "cancel_requested")
        self.assertTrue(self.services["assets"].is_leased("frame.png"))

    async def test_import_video_owns_canonical_source_and_rejects_short_context(self):
        import shutil
        import subprocess
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg required")
        directory = self.storage_root / "video"
        directory.mkdir()
        for token, duration, expected in (("012345678abc", 2, 201), ("abcdef012345", 1, 400)):
            source = directory / f"h3_studio_{token}_00001_.mp4"
            await asyncio.to_thread(subprocess.run, [shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                f"color=red:size=64x64:rate=24:duration={duration}", "-f", "lavfi", "-i",
                f"sine=frequency=440:sample_rate=48000:duration={duration}", "-c:v", "libx264", "-c:a", "aac", str(source)],
                capture_output=True, check=True, timeout=30)
            response = await self.client.post("/h3_studio/lab/media/import_video", json={"filename": source.name})
            self.assertEqual(response.status, expected)
            if expected == 201:
                result = await response.json()
                self.assertEqual(result["metadata"]["frame_count"], 48)
                self.assertEqual((result["metadata"]["width"], result["metadata"]["height"], result["metadata"]["fps"]), (64, 64, 24))
                source.unlink()
                asset = self.services["assets"].get_asset(result["asset"]["asset_id"])
                self.assertIsNotNone(asset)
                resolved = await self.client.post(f"/h3_studio/lab/assets/{asset['asset_id']}/resolve")
                self.assertTrue((await resolved.json())["filename"].startswith("h3_studio_kf_lab_"))

    async def test_delayed_queue_ack_cannot_resurrect_confirmed_cancellation(self):
        accepted, release = asyncio.Event(), asyncio.Event()
        pending = []
        async def delayed_submit(spec, extra):
            pending.append([0, "delayed-owned", {}, extra, []])
            accepted.set()
            await release.wait()
            return {"prompt_id": "delayed-owned"}
        async def queue():
            return {"queue_running": [], "queue_pending": list(pending)}
        async def delete(prompt_ids):
            pending[:] = [item for item in pending if item[1] not in prompt_ids]
            return {"ok": True}
        async def history(prompt_id=None):
            return {}
        self.queue.submit_prompt = delayed_submit
        self.queue.get_queue = queue
        self.queue.delete_from_queue = delete
        self.queue.get_history = history
        submission = asyncio.create_task(self.client.post("/h3_studio/lab/jobs", json={
            "request_id": "delayed-cancel", "render_spec": {"workflow": {"node": {}}}}))
        try:
            await asyncio.wait_for(accepted.wait(), 5)
            response = await self.client.post("/h3_studio/lab/jobs/by_request/delayed-cancel/cancel")
            self.assertEqual((await response.json())["state"], "cancelled")
        finally:
            release.set()
        response = await submission
        self.assertEqual((await response.json())["job"]["state"], "cancelled")
        response = await self.client.get("/h3_studio/lab/jobs/by_request/delayed-cancel")
        self.assertEqual((await response.json())["state"], "cancelled")


class TestUnavailableQueueBridge(AioHTTPTestCase):
    async def get_application(self):
        self.directory = tempfile.TemporaryDirectory()
        app = web.Application()
        self.services = register_lab_routes(app, self.directory.name)
        return app

    async def test_submission_fails_without_creating_phantom_job(self):
        response = await self.client.post("/h3_studio/lab/jobs", json={"request_id": "unavailable", "render_spec": {}})
        self.assertEqual(response.status, 503)
        self.assertIsNone(self.services["jobs"].get_job_by_request_id("unavailable"))

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.directory.cleanup()


if __name__ == "__main__":
    unittest.main()
