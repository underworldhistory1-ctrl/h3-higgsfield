import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image


class QwenImageLibraryTests(unittest.TestCase):
    def test_library_only_returns_valid_owned_images(self):
        import qwen_image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Image.new("RGBA", (8, 6), (255, 0, 0, 128)).save(root / "qwen_studio_good_00001.png")
            (root / "qwen_studio_good_00001.json").write_text(
                json.dumps({"prompt": "red", "profile": "int8", "token": "good"}), encoding="utf-8")
            (root / "qwen_studio_broken_00001.png").write_bytes(b"not an image")
            Image.new("RGB", (2, 2)).save(root / "foreign.png")
            items = qwen_image.scan_image_library(root)
            self.assertEqual([item["filename"] for item in items], ["qwen_studio_good_00001.png"])
            self.assertEqual(items[0]["width"], 8)
            self.assertEqual(items[0]["height"], 6)
            self.assertEqual(items[0]["settings"]["prompt"], "red")

    def test_safe_image_path_rejects_traversal_and_non_owned_names(self):
        import qwen_image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(qwen_image.safe_image_path(root, "../x.png"))
            self.assertIsNone(qwen_image.safe_image_path(root, "foreign.png"))
            self.assertEqual(
                qwen_image.safe_image_path(root, "qwen_studio_abc_00001.png"),
                root / "qwen_studio_abc_00001.png",
            )

    def test_delete_removes_image_and_sidecar_only(self):
        import qwen_image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "qwen_studio_abc_00001.png"
            sidecar = image.with_suffix(".json")
            image.write_bytes(b"png")
            sidecar.write_text("{}", encoding="utf-8")
            other = root / "keep.txt"
            other.write_text("keep", encoding="utf-8")
            self.assertTrue(qwen_image.delete_image_output(root, image.name))
            self.assertFalse(image.exists())
            self.assertFalse(sidecar.exists())
            self.assertTrue(other.exists())


if __name__ == "__main__":
    unittest.main()
