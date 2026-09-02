import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).parents[1]


class StdoutEncodingTests(unittest.TestCase):
    def test_pack_transcripts_handles_ascii_redirected_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            edit_dir = Path(tmp)
            transcripts_dir = edit_dir / "transcripts"
            transcripts_dir.mkdir()
            (transcripts_dir / "take.json").write_text(
                json.dumps(
                    {
                        "words": [
                            {
                                "type": "word",
                                "text": "hello",
                                "start": 0.0,
                                "end": 0.4,
                                "speaker_id": "speaker_0",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "ascii"
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "helpers" / "pack_transcripts.py"),
                    "--edit-dir",
                    str(edit_dir),
                ],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
            self.assertIn("→", result.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
