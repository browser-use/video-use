import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "render.py"
SPEC = importlib.util.spec_from_file_location("video_use_render", MODULE_PATH)
assert SPEC and SPEC.loader
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)


class ConcatSegmentsTests(unittest.TestCase):
    def test_copies_video_and_reencodes_audio_as_one_continuous_stream(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            edit_dir = Path(temp_dir)
            segments = [edit_dir / "one.mp4", edit_dir / "two.mp4"]
            out_path = edit_dir / "base.mp4"

            with patch.object(render.subprocess, "run") as run:
                render.concat_segments(segments, out_path, edit_dir)

        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "copy")
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "aac")
        self.assertEqual(cmd[cmd.index("-b:a") + 1], "192k")
        self.assertEqual(cmd[cmd.index("-ar") + 1], "48000")
        self.assertNotIn("-c", cmd)


if __name__ == "__main__":
    unittest.main()
