import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).parents[1]
HELPERS = (
    "pack_transcripts.py",
    "render.py",
    "grade.py",
    "transcribe_batch.py",
)


class StdoutEncodingTests(unittest.TestCase):
    def test_helpers_handle_ascii_redirected_stdout(self):
        probe = (
            "import atexit, runpy, sys; "
            "helper = sys.argv[1]; "
            "atexit.register(lambda: print('→')); "
            "sys.argv = [helper, '--help']; "
            "runpy.run_path(helper, run_name='__main__')"
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
                self.assertTrue(result.stdout.decode("utf-8").endswith("→\n"))


if __name__ == "__main__":
    unittest.main()
