import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ImageUiContractTests(unittest.TestCase):
    def test_image_workspace_has_required_controls_and_accessible_dialog(self):
        html = (ROOT / "web" / "image.html").read_text(encoding="utf-8")
        for control in (
            "referenceFiles", "referenceList", "referenceCount", "fitReferences", "mentionMenu", "mentionList", "prompt",
            "profile", "aspect", "resolution", "steps", "seed", "transparent",
            "generate", "cancel", "stage", "progressBar", "results", "detailsDialog",
        ):
            self.assertIn(f'id="{control}"', html)
        self.assertNotIn('id="editMode"', html)
        self.assertIn('id="editPanel"', html)
        self.assertIn('href="index.html"', html)

    def test_video_workspace_links_to_image_workspace(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="image.html"', html)
        self.assertIn("Create Image", html)

    def test_image_script_contains_profile_gate_reference_cap_and_recovery(self):
        script = (ROOT / "web" / "image-studio.js").read_text(encoding="utf-8")
        self.assertIn("/h3_studio/image_readiness", script)
        self.assertIn("/h3_studio/image_library", script)
        self.assertIn("buildGraph", script)
        self.assertIn("state.refs.length>=10", script)
        self.assertIn("activePromptId", script)
        self.assertIn("qwen.activeJob", script)
        self.assertIn("new WebSocket", script)
        self.assertIn("QwenStudioSaveImage", script)
        self.assertIn("resolveMentions", script)
        self.assertIn("pollImageProgress", script)


if __name__ == "__main__":
    unittest.main()
