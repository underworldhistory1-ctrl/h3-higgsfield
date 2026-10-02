"""Integration tests for H3 Studio Lab API routes."""

import pathlib
import tempfile
import unittest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from h3_lab.routes import register_lab_routes


class TestH3LabRoutes(AioHTTPTestCase):
    async def get_application(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_root = pathlib.Path(self.temp_dir.name)
        app = web.Application()
        self.services = register_lab_routes(app, str(self.storage_root))
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

        # 2. Submit exact duplicate request -> 200 OK and same job_id
        resp_dup = await self.client.request(
            "POST", "/h3_studio/lab/jobs",
            json={"request_id": "api_req_1", "render_spec": spec}
        )
        self.assertEqual(resp_dup.status, 200)
        data2 = await resp_dup.json()
        self.assertTrue(data2["is_duplicate"])
        self.assertEqual(data2["job"]["job_id"], job_id)

        # 3. Same request_id with different payload -> 409 Conflict
        spec_diff = {"mode": "text", "prompt": "Different prompt", "seed": 99}
        resp_conflict = await self.client.request(
            "POST", "/h3_studio/lab/jobs",
            json={"request_id": "api_req_1", "render_spec": spec_diff}
        )
        self.assertEqual(resp_conflict.status, 409)


if __name__ == "__main__":
    unittest.main()
