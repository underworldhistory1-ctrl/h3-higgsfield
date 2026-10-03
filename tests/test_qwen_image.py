import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class QwenImageContractTests(unittest.TestCase):
    def module(self):
        import qwen_image

        return qwen_image

    def test_model_profiles_have_exact_three_file_sets(self):
        qwen = self.module()
        self.assertEqual(set(qwen.MODEL_PROFILES), {"int8", "bf16"})
        self.assertEqual(qwen.MODEL_PROFILES["int8"]["bytes"], 17_283_091_112)
        self.assertEqual(qwen.MODEL_PROFILES["bf16"]["bytes"], 32_440_124_920)
        for profile in qwen.MODEL_PROFILES.values():
            self.assertEqual(len(profile["files"]), 3)
            self.assertTrue(all(len(item.sha256) == 64 for item in profile["files"]))

    def test_profiles_resolve_shared_model_paths_without_local_copies(self):
        from types import SimpleNamespace
        qwen = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = root / "private-models"
            shared.mkdir()
            model = shared / "qwen.safetensors"
            model.write_bytes(b"valid")
            profile = {"label": "Test", "bytes": 5, "files": (qwen.ModelFile("diffusion_models", model.name, 5, ""),)}
            resolver = SimpleNamespace(get_full_path=lambda folder, name: str(model))
            with patch.object(qwen, "MODEL_PROFILES", {"int8": profile}):
                self.assertFalse(qwen.profile_status(root / "isolated-comfy")["int8"]["ready"])
                self.assertTrue(qwen.profile_status(root / "isolated-comfy", resolver)["int8"]["ready"])
                model.write_bytes(b"bad")
                self.assertEqual(qwen.profile_status(root / "isolated-comfy", resolver)["int8"]["invalid"], [model.name])

    def test_create_graph_uses_selected_profile_and_empty_latent(self):
        qwen = self.module()
        graph = qwen.build_qwen_graph(
            {"mode": "create", "profile": "int8", "prompt": "A glass fox", "width": 1024,
             "height": 1024, "steps": 40, "seed": 42, "transparent": False},
            [], "abc123",
        )
        self.assertEqual(graph["unet"]["inputs"]["unet_name"], "qwen_image_2.1_int8_convrot.safetensors")
        self.assertEqual(graph["clip"]["inputs"]["clip_name"], "qwen3vl_8b_int8_convrot.safetensors")
        self.assertEqual(graph["latent"]["class_type"], "EmptyLatentImage")
        self.assertNotIn("cache", graph)
        self.assertEqual(graph["save"]["class_type"], "QwenStudioSaveImage")

    def test_edit_graph_binds_primary_and_references_in_order(self):
        qwen = self.module()
        graph = qwen.build_qwen_graph(
            {"mode": "edit", "profile": "bf16", "prompt": "Put <image2> in <image1>",
             "width": 1024, "height": 1024, "steps": 25, "seed": 7,
             "reference_resolution": 1024},
            ["a.png", "b.png"], "edit123",
        )
        self.assertEqual(graph["cache"]["class_type"], "QwenImage21Cache")
        self.assertEqual(graph["sampler"]["inputs"]["latent_image"], ["enc", 2])
        self.assertEqual(graph["enc"]["inputs"]["images.image_1"], ["load1", 0])
        self.assertEqual(graph["enc"]["inputs"]["images.image_2"], ["load2", 0])
        self.assertEqual(graph["unet"]["inputs"]["unet_name"], "qwen_image_2.1_bf16.safetensors")

    def test_edit_graph_can_resize_only_the_primary_canvas(self):
        qwen = self.module()
        graph = qwen.build_qwen_graph(
            {"mode": "edit", "profile": "int8", "prompt": "Relight <image1>",
             "width": 1696, "height": 960, "steps": 25, "seed": 9,
             "reference_resolution": 0, "custom_size": True},
            ["canvas.png", "look.png"], "edit456",
        )
        self.assertEqual(graph["scale_primary"]["class_type"], "ImageScale")
        self.assertEqual(graph["scale_primary"]["inputs"]["width"], 1696)
        self.assertEqual(graph["enc"]["inputs"]["images.image_1"], ["scale_primary", 0])
        self.assertEqual(graph["enc"]["inputs"]["images.image_2"], ["load2", 0])

    def test_validation_rejects_bad_or_unsafe_requests(self):
        qwen = self.module()
        base = {"mode": "create", "profile": "int8", "prompt": "x", "width": 1024,
                "height": 1024, "steps": 40, "seed": 1}
        with self.assertRaisesRegex(ValueError, "Edit mode requires"):
            qwen.build_qwen_graph({**base, "mode": "edit"}, [], "x")
        with self.assertRaisesRegex(ValueError, "up to 10"):
            qwen.build_qwen_graph({**base, "mode": "edit"}, [f"{i}.png" for i in range(11)], "x")
        with self.assertRaisesRegex(ValueError, "multiple of 32"):
            qwen.build_qwen_graph({**base, "width": 1000}, [], "x")
        with self.assertRaisesRegex(ValueError, "Unknown Qwen profile"):
            qwen.build_qwen_graph({**base, "profile": "fp8"}, [], "x")

    def test_transparent_create_wraps_prompt_but_edit_does_not(self):
        qwen = self.module()
        create = qwen.build_qwen_graph(
            {"mode": "create", "profile": "int8", "prompt": "A dragon sticker.", "width": 1024,
             "height": 1024, "steps": 40, "seed": 1, "transparent": True}, [], "x")
        self.assertIn("RGBA", create["enc"]["inputs"]["prompt"])
        self.assertIn("transparent background", create["enc"]["inputs"]["prompt"])

    def test_profile_status_requires_exact_file_sizes(self):
        qwen = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tiny = qwen.ModelFile("diffusion_models", "tiny.safetensors", 8, "0" * 64)
            profiles = {"int8": {"label": "test", "bytes": 8, "files": (tiny,)}}
            with patch.dict(qwen.MODEL_PROFILES, profiles, clear=True):
                status = qwen.profile_status(root)
                self.assertFalse(status["int8"]["ready"])
                path = root / "models" / tiny.folder / tiny.name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"12345678")
                self.assertTrue(qwen.profile_status(root)["int8"]["ready"])


if __name__ == "__main__":
    unittest.main()
