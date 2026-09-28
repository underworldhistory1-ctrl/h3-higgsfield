import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTests(unittest.TestCase):
    def test_salad_image_pins_combined_h3_and_qwen_comfy_revision(self):
        dockerfile = (ROOT / "Dockerfile.salad").read_text(encoding="utf-8")
        self.assertIn("3b4c0b0e457cf0a51cf3038e0a6750d8f96ce251", dockerfile)
        self.assertIn("TextEncodeQwenImage21", dockerfile)
        self.assertIn("QwenImage21Cache", dockerfile)
        self.assertIn("strip[..., :, x_idx[j]:x_idx[j] + x_len[j]]", dockerfile)

    def test_salad_defaults_to_int8_and_verifies_selected_profile(self):
        entrypoint = (ROOT / "deploy/salad/entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn('QWEN_IMAGE_PROFILES="${QWEN_IMAGE_PROFILES:-int8}"', entrypoint)
        self.assertIn("download_qwen_image_models.py", entrypoint)
        self.assertIn("--offline-check", entrypoint)
        self.assertIn('output/images', entrypoint)

    def test_all_installers_share_verified_comfy_revision(self):
        expected = "3b4c0b0e457cf0a51cf3038e0a6750d8f96ce251"
        for filename in ("install.sh", "deploy/install_windows.py", "Dockerfile.salad"):
            self.assertIn(expected, (ROOT / filename).read_text(encoding="utf-8"), filename)


if __name__ == "__main__":
    unittest.main()
