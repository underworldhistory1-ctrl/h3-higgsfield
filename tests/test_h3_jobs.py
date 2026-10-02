"""Unit and integration tests for JobService and ComfyUI reconciliation."""

import asyncio
import pathlib
import tempfile
import unittest

from h3_lab.assets import AssetService
from h3_lab.jobs import JobService


class MockComfyClient:
    def __init__(self):
        self.queue_running = []
        self.queue_pending = []
        self.history = {}
        self.interrupt_called = False
        self.interrupt_fail = False
        self.delete_called = False

    async def get_queue(self):
        return {
            "queue_running": list(self.queue_running),
            "queue_pending": list(self.queue_pending),
        }

    async def get_history(self, prompt_id=None):
        if prompt_id:
            return {prompt_id: self.history.get(prompt_id)} if prompt_id in self.history else {}
        return dict(self.history)

    async def interrupt(self):
        self.interrupt_called = True
        if self.interrupt_fail:
            return {"ok": False, "status": 500, "error": "Internal Server Error"}
        return {"ok": True, "status": 200}

    async def delete_from_queue(self, prompt_ids):
        self.delete_called = True
        self.queue_pending = [item for item in self.queue_pending if item[1] not in prompt_ids]
        return {"ok": True}


class TestH3JobService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_root = pathlib.Path(self.temp_dir.name)
        self.asset_service = AssetService(str(self.storage_root))
        self.job_service = JobService(str(self.storage_root), asset_service=self.asset_service)
        self.mock_comfy = MockComfyClient()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_idempotent_submission(self):
        """Assertion: duplicate request -> same prompt ID; no second sampler"""
        spec = {"mode": "text", "prompt": "a cinematic desert scene", "seed": 42}
        job1, is_dup1 = self.job_service.submit_job("req_100", spec, asset_leases=["kf_1.png"])
        self.assertFalse(is_dup1)
        self.assertEqual(job1["state"], "validating")
        self.assertTrue(self.asset_service.is_leased("kf_1.png"))

        # Set prompt ID on first job
        self.job_service.update_job(job1["job_id"], prompt_id="prompt_abc1", state="queued")

        # Duplicate request with same payload
        job2, is_dup2 = self.job_service.submit_job("req_100", spec, asset_leases=["kf_1.png"])
        self.assertTrue(is_dup2)
        self.assertEqual(job2["job_id"], job1["job_id"])
        self.assertEqual(job2["prompt_id"], "prompt_abc1")

        # Different payload with same request_id -> 409 Conflict
        spec_diff = {"mode": "text", "prompt": "a totally different prompt", "seed": 99}
        with self.assertRaises(ValueError) as ctx:
            self.job_service.submit_job("req_100", spec_diff)
        self.assertEqual(getattr(ctx.exception, "status_code", None), 409)

    def test_cancel_http_500_retains_active_identity_and_leases(self):
        """Assertion: cancel(HTTP 500) -> active identity retained; cleanup not called"""
        spec = {"mode": "frames", "prompt": "test shot"}
        job, _ = self.job_service.submit_job("req_cancel_500", spec, asset_leases=["frame1.png"])
        self.job_service.update_job(job["job_id"], prompt_id="prompt_run_1", state="sampling")

        # Mock job currently running in ComfyUI
        self.mock_comfy.queue_running = [
            [1, "prompt_run_1", {}, {"extra_pnginfo": {"h3_lab_job_id": job["job_id"]}}, []]
        ]
        # Interrupt fails with HTTP 500
        self.mock_comfy.interrupt_fail = True

        updated_job = asyncio.run(self.job_service.request_cancel(job["job_id"], self.mock_comfy))
        self.assertEqual(updated_job["state"], "cancel_requested", "HTTP 500 must not mark job cancelled")
        self.assertTrue(self.asset_service.is_leased("frame1.png"), "Leases must be retained when cancel fails")

    def test_delayed_reply_submission_reconciliation(self):
        """Assertion: submit accepted + delayed reply + cancel -> exactly one server job tracked"""
        spec = {"mode": "text", "prompt": "delayed reply prompt"}
        job, _ = self.job_service.submit_job("req_delayed", spec, asset_leases=["audio_ref.wav"])

        # Comfy accepted prompt 'prompt_delayed_77' with our request_id in extra_data, but client never received reply
        self.mock_comfy.queue_pending = [
            [1, "prompt_delayed_77", {}, {"extra_pnginfo": {"h3_lab_request_id": "req_delayed"}}, []]
        ]

        # Reconcile submission gap
        reconciled = asyncio.run(self.job_service.reconcile_submission_gap(job["job_id"], self.mock_comfy))
        self.assertEqual(reconciled["prompt_id"], "prompt_delayed_77")
        self.assertEqual(reconciled["state"], "queued")

        # Now cancel the reconciled job
        cancelled = asyncio.run(self.job_service.request_cancel(job["job_id"], self.mock_comfy))
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertTrue(self.mock_comfy.delete_called)
        self.assertFalse(self.asset_service.is_leased("audio_ref.wav"))

    def test_foreign_running_job_no_global_interrupt(self):
        """Assertion: foreign running job -> no global interrupt"""
        spec = {"mode": "text", "prompt": "our prompt"}
        job, _ = self.job_service.submit_job("req_our_job", spec)
        self.job_service.update_job(job["job_id"], prompt_id="prompt_ours", state="queued")

        # Another user / task is running prompt_someone_else
        self.mock_comfy.queue_running = [
            [1, "prompt_someone_else", {}, {}, []]
        ]

        # Cancel our job
        updated = asyncio.run(self.job_service.request_cancel(job["job_id"], self.mock_comfy))
        self.assertFalse(self.mock_comfy.interrupt_called, "Must not interrupt a foreign running job!")
        self.assertEqual(updated["state"], "cancel_requested")

    def test_unknown_job_retains_leases(self):
        """Assertion: unknown job -> leases retained until reconciliation"""
        spec = {"mode": "text", "prompt": "lost in crash"}
        job, _ = self.job_service.submit_job("req_crash", spec, asset_leases=["precious_input.mov"])
        self.job_service.update_job(job["job_id"], state="unknown")

        self.assertTrue(self.asset_service.is_leased("precious_input.mov"))
        self.assertTrue(self.job_service.is_file_leased("precious_input.mov"))

    def test_missing_savevideo_output_not_completed(self):
        """Assertion: missing SaveVideo output -> not completed"""
        spec = {"mode": "text", "prompt": "evaluating finish"}
        job, _ = self.job_service.submit_job("req_finish", spec)
        self.job_service.update_job(job["job_id"], prompt_id="p_fin", state="saving")

        # History with success but no SaveVideo video in outputs
        self.mock_comfy.history["p_fin"] = {
            "status": {"status_str": "success", "completed": True},
            "outputs": {"8": {"latent": []}} # No videos node!
        }
        # Cancellation/reconciliation should not mark completed if no video output
        reconciled = asyncio.run(self.job_service.reconcile_submission_gap(job["job_id"], self.mock_comfy))
        self.assertNotEqual(reconciled["state"], "completed")


if __name__ == "__main__":
    unittest.main()
