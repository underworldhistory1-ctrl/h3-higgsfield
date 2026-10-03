"""Unit tests for AV latent context serialization, fingerprinting, and lifecycle."""

import pathlib
import tempfile
import unittest
import torch

from h3_lab.contexts import ContextService


class TestH3Contexts(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_root = pathlib.Path(self.temp_dir.name)
        self.service = ContextService(str(self.storage_root))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_context_roundtrip(self):
        # Create synthetic AV latent tensors:
        # Video: [1, 24, T, H/16, W/16] -> [1, 24, 2, 44, 80] for 1280x704, 5 frames
        video = torch.randn(1, 24, 2, 44, 80)
        # Audio: [1, 32, 2, T40] -> [1, 32, 2, 8]
        audio = torch.randn(1, 32, 2, 8)

        rec = self.service.save_context(
            video_tensor=video,
            audio_tensor=audio,
            project_id="proj_ctx_1",
            take_id="take_ctx_1",
            width=1280,
            height=704,
            frame_count=124,
            fps=24
        )

        self.assertIn("context_id", rec)
        self.assertEqual(rec["canvas"]["width"], 1280)
        self.assertEqual(rec["canvas"]["height"], 704)
        self.assertTrue((self.storage_root / rec["server_path"]).is_file())

        # Load back
        v_loaded, a_loaded, loaded_rec = self.service.load_context(
            rec["context_id"],
            target_width=1280,
            target_height=704
        )

        self.assertEqual(v_loaded.shape, video.shape)
        self.assertEqual(a_loaded.shape, audio.shape)
        self.assertTrue(torch.allclose(v_loaded, video, atol=1e-5))
        self.assertTrue(torch.allclose(a_loaded, audio, atol=1e-5))

    def test_mismatched_canvas_rejection(self):
        video = torch.zeros(1, 24, 2, 44, 80)
        audio = torch.zeros(1, 32, 2, 8)

        rec = self.service.save_context(
            video_tensor=video,
            audio_tensor=audio,
            project_id="p1",
            take_id="t1",
            width=1280,
            height=704,
            frame_count=124
        )

        # Loading into a 1024x576 target must reject with actionable reason
        with self.assertRaises(ValueError) as ctx:
            self.service.load_context(rec["context_id"], target_width=1024, target_height=576)
        self.assertIn("resolution mismatch", str(ctx.exception).lower())

    def test_disk_usage_and_purge(self):
        video = torch.zeros(1, 24, 2, 44, 80)
        audio = torch.zeros(1, 32, 2, 8)

        rec = self.service.save_context(video, audio, "p1", "t1", 1280, 704, 124)
        usage = self.service.get_disk_usage()
        self.assertEqual(usage["count"], 1)
        self.assertGreater(usage["total_bytes"], 0)

        # Purge
        self.service.purge_context(rec["context_id"])
        self.assertEqual(self.service.get_disk_usage()["count"], 0)
        self.assertFalse((self.storage_root / rec["server_path"]).is_file())

    def test_payload_tampering_is_rejected_before_tensor_loading(self):
        video = torch.zeros(1, 24, 2, 44, 80)
        audio = torch.zeros(1, 32, 2, 8)
        rec = self.service.save_context(video, audio, "p1", "t1", 1280, 704, 124)
        with (self.storage_root / rec["server_path"]).open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ValueError, "content hash mismatch"):
            self.service.load_context(rec["context_id"])


if __name__ == "__main__":
    unittest.main()
