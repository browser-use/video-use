import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.render import concat_segments  # noqa: E402


class ConcatSegmentsTest(unittest.TestCase):
    def test_concat_list_is_unique_and_cleaned_up(self):
        with TemporaryDirectory() as tmp:
            edit_dir = Path(tmp)
            segment_paths = [edit_dir / "seg_01.mp4", edit_dir / "seg_02.mp4"]
            for segment_path in segment_paths:
                segment_path.write_bytes(b"")

            concat_lists: list[Path] = []

            def fake_run(cmd, check, stdout, stderr):
                concat_list = Path(cmd[cmd.index("-i") + 1])
                self.assertTrue(concat_list.exists())
                self.assertEqual(concat_list.parent, edit_dir)
                self.assertTrue(concat_list.name.startswith("_concat_"))
                self.assertNotEqual(concat_list.name, "_concat.txt")
                self.assertEqual(
                    concat_list.read_text(encoding="utf-8"),
                    "".join(f"file '{p.resolve()}'\n" for p in segment_paths),
                )
                concat_lists.append(concat_list)
                return subprocess.CompletedProcess(cmd, 0)

            with patch("helpers.render.subprocess.run", fake_run):
                with redirect_stdout(StringIO()):
                    concat_segments(segment_paths, edit_dir / "out_1.mp4", edit_dir)
                    concat_segments(segment_paths, edit_dir / "out_2.mp4", edit_dir)

            self.assertEqual(len(concat_lists), 2)
            self.assertEqual(len(set(concat_lists)), 2)
            self.assertFalse((edit_dir / "_concat.txt").exists())
            for concat_list in concat_lists:
                self.assertFalse(concat_list.exists())


if __name__ == "__main__":
    unittest.main()
