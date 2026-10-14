import importlib.util
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

HELPERS = Path(__file__).resolve().parent.parent / "helpers"
SPEC = importlib.util.spec_from_file_location("render", HELPERS / "render.py")
render = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(render)


class SubtitlesFilterPath(unittest.TestCase):
    def test_subtitles_path_has_no_backslashes(self):
        with tempfile.TemporaryDirectory() as d:
            subs = Path(d) / "master.srt"
            subs.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
            with mock.patch.object(render.subprocess, "run") as run:
                render.build_final_composite(Path(d) / "base.mp4", [], subs, Path(d) / "out.mp4", Path(d))
            cmd = run.call_args.args[0]
            graph = cmd[cmd.index("-filter_complex") + 1]
            subtitles_arg = graph[graph.index("subtitles="):]
            self.assertNotIn("\\U", subtitles_arg)  # no raw Windows separators survive
            self.assertIn(subs.resolve().as_posix().replace(":", r"\:"), subtitles_arg)

    def test_windows_drive_path_is_escaped_for_ffmpeg(self):
        # What the filter receives for a typical Windows path: forward slashes, escaped drive colon.
        p = PureWindowsPath(r"C:\Users\me\edit\master.srt")
        self.assertEqual(p.as_posix().replace(":", r"\:"), r"C\:/Users/me/edit/master.srt")


if __name__ == "__main__":
    unittest.main()
