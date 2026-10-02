"""Unit tests for H3 Lab Asset Service and Leases."""

import pathlib
import tempfile
import unittest

from h3_lab.assets import AssetService


class TestH3AssetLeases(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_root = pathlib.Path(self.temp_dir.name)
        self.service = AssetService(str(self.storage_root))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_register_asset_and_deduplication(self):
        src_file = self.storage_root / "test_input.png"
        src_file.write_bytes(b"\x89PNG\r\n\x1a\nfakeimagecontent")

        rec1 = self.service.register_asset(str(src_file), kind="image", original_name="test_input.png", project_id="proj_1")
        self.assertIn("asset_id", rec1)
        self.assertEqual(rec1["kind"], "image")
        self.assertTrue((self.storage_root / rec1["server_path"]).is_file())

        # Register same content again with another project
        rec2 = self.service.register_asset(str(src_file), kind="image", original_name="test_input.png", project_id="proj_2")
        self.assertEqual(rec1["asset_id"], rec2["asset_id"], "Identical content hash must deduplicate")
        self.assertIn("proj_1", rec2["projects"])
        self.assertIn("proj_2", rec2["projects"])

    def test_leases_prevent_discard(self):
        filename = "h3_studio_kf_sample.png"
        self.assertTrue(self.service.can_discard_input(filename))

        # Acquire lease for an active job
        self.service.acquire_lease(filename, owner_id="job_123")
        self.assertTrue(self.service.is_leased(filename))
        self.assertFalse(self.service.can_discard_input(filename), "Leased file must not be discarded")

        # Second job acquires lease on same file
        self.service.acquire_lease(filename, owner_id="job_456")
        self.assertEqual(set(self.service.get_lease_owners(filename)), {"job_123", "job_456"})

        # Releasing first owner still keeps file leased
        self.service.release_lease(filename, owner_id="job_123")
        self.assertTrue(self.service.is_leased(filename))
        self.assertFalse(self.service.can_discard_input(filename))

        # Releasing all owners frees the file
        self.service.release_all_leases_for_owner("job_456")
        self.assertFalse(self.service.is_leased(filename))
        self.assertTrue(self.service.can_discard_input(filename))

    def test_stale_input_with_queued_owner_survives_cleanup(self):
        """Mandatory fixture: stale input + queued owner -> survives cleanup"""
        stale_file = "h3_studio_kf_stale.png"
        self.service.acquire_lease(stale_file, owner_id="queued_job_99")
        self.assertFalse(self.service.can_discard_input(stale_file))


if __name__ == "__main__":
    unittest.main()
