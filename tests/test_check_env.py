import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import check_env


class CheckEnvTests(unittest.TestCase):
    def test_missing_media_tool_is_failure(self):
        with patch.object(check_env.shutil, "which", return_value=None):
            result = check_env.check_media_tool("ffmpeg")
        self.assertFalse(result.ok)
        self.assertIn("not found", result.detail)

    def test_old_media_tool_is_failure(self):
        with (
            patch.object(check_env.shutil, "which", return_value="/usr/bin/ffmpeg"),
            patch.object(
                check_env,
                "_version_text",
                return_value=("3.4.11", ""),
            ),
        ):
            result = check_env.check_media_tool("ffmpeg")
        self.assertFalse(result.ok)
        self.assertIn("older", result.detail)

    def test_writable_directory_check_creates_edit_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = check_env.check_writable_directory(
                "edit output",
                Path(temp_dir) / "edit",
                create=True,
            )
        self.assertTrue(result.ok)

    def test_read_only_directory_is_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            path.chmod(stat.S_IRUSR | stat.S_IXUSR)
            try:
                result = check_env.check_writable_directory("footage", path)
            finally:
                path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        self.assertFalse(result.ok)

    def test_optional_backend_is_not_checked_by_default(self):
        with patch.object(check_env, "check_backend") as check_backend:
            results = check_env.run_checks(Path.cwd(), [])
        check_backend.assert_not_called()
        self.assertEqual([result.name for result in results[-2:]], ["footage", "edit output"])

    def test_missing_footage_directory_does_not_create_edit_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            videos_dir = Path(temp_dir) / "missing"
            results = check_env.run_checks(videos_dir, [])
        self.assertFalse(results[2].ok)
        self.assertFalse((videos_dir / "edit").exists())

    def test_required_backend_is_checked(self):
        expected = check_env.CheckResult("manim", True, "0.20.1")
        with (
            patch.object(check_env, "check_media_tool", return_value=check_env.CheckResult("tool", True, "ok")),
            patch.object(check_env, "check_writable_directory", return_value=check_env.CheckResult("dir", True, "ok")),
            patch.object(check_env, "check_backend", return_value=expected) as check_backend,
        ):
            results = check_env.run_checks(Path.cwd(), ["manim"])
        check_backend.assert_called_once_with("manim")
        self.assertEqual(results[-1], expected)

    def test_backend_version_output_is_supported(self):
        with (
            patch.object(check_env.shutil, "which", return_value="/usr/local/bin/manim"),
            patch.object(check_env, "_version_text", return_value=("0.20.1", "")),
        ):
            result = check_env.check_backend("manim")
        self.assertTrue(result.ok)
        self.assertIn("0.20.1", result.detail)

    def test_version_text_accepts_v_prefixed_output(self):
        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": "Manim Community v0.20.1\n",
            "stderr": "",
        })()
        with patch.object(check_env.subprocess, "run", return_value=completed):
            version, error = check_env._version_text("manim", ("--version",))
        self.assertEqual(version, "0.20.1")
        self.assertEqual(error, "")


if __name__ == "__main__":
    unittest.main()
