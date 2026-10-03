"""Fail-closed activation checks use CPU-only, read-only status mocks."""
import pathlib
import types
import tempfile
import unittest
from deploy.activate_v2 import ActivationBlocked, launch_command, preflight, verify_standby_command


class ActivationTests(unittest.TestCase):
    def checks(self, *, running=False, gpu_busy=False, unavailable=False, gpu="0, 1000", memory=50):
        calls = []
        def read(url):
            calls.append(url)
            if unavailable:
                raise ConnectionError("unavailable")
            if url.endswith("/queue"):
                return {"queue_running": [[1, "production"]] if running else [], "queue_pending": []}
            return {"gpu_available": True, "busy": gpu_busy, "h3_busy": False}
        def run(command, **kwargs):
            self.assertEqual(command[0], "nvidia-smi")
            return types.SimpleNamespace(stdout=gpu)
        def text(path):
            return str(memory if path.endswith("current") else 100)
        return lambda: preflight(read, run, text), calls

    def test_only_empty_queues_and_idle_resources_pass(self):
        check, calls = self.checks()
        check()
        self.assertEqual(calls, ["http://127.0.0.1:8188/queue", "http://127.0.0.1:8189/queue", "http://127.0.0.1:7860/api/gpu_status"])

    def test_busy_or_unavailable_sources_block(self):
        for options in ({"running": True}, {"gpu_busy": True}, {"unavailable": True},
                        {"gpu": "6, 1000"}, {"gpu": "0, 4097"}, {"gpu": "N/A, 10"}, {"memory": 81}):
            with self.subTest(options=options), self.assertRaises(ActivationBlocked):
                self.checks(**options)[0]()

    def test_only_owned_standby_pid_command_is_eligible(self):
        root = pathlib.Path(tempfile.gettempdir()).resolve() / "h3-studio-v2"
        script = (root / "source/deploy/v2_standby.py").as_posix()
        verify_standby_command(["python", script, "--port", "8190"], root)
        for arguments in (["python", "/production/main.py", "--port", "8188"],
                          ["python", "/other/deploy/v2_standby.py", "--port", "8190"],
                          ["python", script, "--port", "8188"],
                          ["python", "/production/main.py", "--asset", script, "--port", "8190"],
                          ["python", "-c", script, "--port", "8190"],
                          ["python", "-m", script, "--port", "8190"],
                          ["python", "-u", script, "--port", "8190"]):
            with self.assertRaises(ActivationBlocked):
                verify_standby_command(arguments, root)

    def test_launch_is_manual_isolated_and_uses_requested_memory_flags(self):
        command = launch_command()
        self.assertIn("--cache-none", command)
        self.assertIn("--fp16-intermediates", command)
        self.assertEqual(command[command.index("--port") + 1], "8190")
        self.assertEqual(pathlib.Path(command[command.index("--output-directory") + 1]).as_posix(), "/output/h3-studio-v2/data/output")
        self.assertNotIn("--disable-api-nodes", command)
