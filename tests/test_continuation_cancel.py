"""Cancellation must not abandon workers or remove committed source media."""
import asyncio
import pathlib
import tempfile
import threading
import unittest
from unittest.mock import patch

from aiohttp import web
from h3_lab.routes import register_lab_routes


class UploadPart:
    name = "file"
    filename = "source.mp4"

    def __init__(self):
        self.sent = False

    async def read_chunk(self, size):
        if self.sent:
            return b""
        self.sent = True
        return b"uploaded fixture"


class UploadRequest:
    def __init__(self, project_id):
        self.query = {"project_id": project_id, "width": "256", "height": "256"}

    async def multipart(self):
        return self

    async def next(self):
        return UploadPart()


def prepare_fixture(source, destination, *args):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"normalized fixture")
    return {"frame_count": 48, "used_duration_seconds": 2, "width": 256, "height": 256}


class ContinuationCancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        app = web.Application()
        self.services = register_lab_routes(app, str(self.root), output_root=self.root)
        self.projects = self.services["projects"]
        self.project_id = self.projects.create_project()["project_id"]
        self.handler = next(route.handler for route in app.router.routes()
                            if route.resource.canonical == "/h3_studio/lab/media/upload_continuation")

    def tearDown(self):
        self.temp.cleanup()

    async def test_cancel_during_commit_keeps_completed_take_media(self):
        started, release = threading.Event(), threading.Event()
        original = self.projects.append_source_take

        def blocked_append(*args):
            started.set()
            if not release.wait(5):
                raise TimeoutError("Commit fixture was not released")
            return original(*args)

        with patch("h3_lab.video_source.prepare_video_source", prepare_fixture), \
                patch.object(self.projects, "append_source_take", blocked_append):
            task = asyncio.create_task(self.handler(UploadRequest(self.project_id)))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                task.cancel()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

        saved = self.projects.get_project(self.project_id)
        self.assertEqual(len(saved["takes"]), 1)
        self.assertTrue((self.root / saved["takes"][0]["output_file"]).is_file())
        self.assertFalse(list((self.root / "lab_storage").glob("upload_source_*")))

    async def test_cancel_during_conversion_holds_lock_until_cleanup(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        def blocked_prepare(*args):
            calls.append(args[1])
            if len(calls) == 1:
                started.set()
                if not release.wait(5):
                    raise TimeoutError("Conversion fixture was not released")
            return prepare_fixture(*args)

        with patch("h3_lab.video_source.prepare_video_source", blocked_prepare):
            first = asyncio.create_task(self.handler(UploadRequest(self.project_id)))
            second = None
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                first.cancel()
                second = asyncio.create_task(self.handler(UploadRequest(self.project_id)))
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.assertFalse(first.done())
                self.assertEqual(len(calls), 1)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(first, 5)
                self.assertEqual((await asyncio.wait_for(second, 5)).status, 201)
            finally:
                release.set()
                await asyncio.gather(*[task for task in (first, second) if task], return_exceptions=True)

        saved = self.projects.get_project(self.project_id)
        self.assertEqual(len(saved["takes"]), 1)
        self.assertFalse(calls[0].exists())
        self.assertTrue(calls[1].is_file())
        self.assertFalse(list((self.root / "lab_storage").glob("upload_source_*")))
