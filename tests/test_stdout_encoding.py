import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).parents[1]
HELPERS = ("pack_transcripts", "render", "grade", "transcribe_batch")


class StdoutEncodingTests(unittest.TestCase):
    def test_helpers_handle_ascii_redirected_stdout(self):
        probe = (
            "import importlib, sys; "
            "module = importlib.import_module(sys.argv[1]); "
            "module.configure_stdout(); "
            "print('→')"
        )
        for helper in HELPERS:
            with self.subTest(helper=helper):
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "ascii"
                result = subprocess.run(
                    [sys.executable, "-c", probe, helper],
                    cwd=REPO_ROOT / "helpers",
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )

                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
                self.assertEqual(result.stdout.decode("utf-8"), "→\n")


if __name__ == "__main__":
    unittest.main()
