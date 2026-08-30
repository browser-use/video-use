r"""Regression test for cross-platform subtitles-path escaping in render.py.

Bug: render.py built the ffmpeg `subtitles=` filter from an absolute path that,
on Windows, looks like C:\Users\me\master.srt. The filter escaped ':' and "'"
but left '\' separators raw, so ffmpeg's filtergraph parser mangled the path and
subtitle burning failed on Windows. Fix: _escape_filter_value forward-slashes the
separators and escapes the drive colon; escape_subtitles_path applies it to the
resolved path. These tests pin the pure transform for Windows and POSIX inputs
and pass on any host.
"""
import sys
import unittest
from pathlib import Path

HELPERS_DIR = Path(__file__).resolve().parent.parent / "helpers"
sys.path.insert(0, str(HELPERS_DIR))
import render  # noqa: E402


class EscapeFilterValueTest(unittest.TestCase):
    def test_windows_path_forward_slashed_and_colon_escaped(self):
        out = render._escape_filter_value(r"C:\Users\me\AppData\Local\Temp\master.srt")
        # Drive colon escaped, all path separators forward-slashed. The only
        # backslash that survives is the one escaping the colon.
        self.assertEqual(out, r"C\:/Users/me/AppData/Local/Temp/master.srt")
        for i, ch in enumerate(out):  # every ':' is escaped (preceded by a backslash)
            if ch == ":":
                self.assertTrue(i > 0 and out[i - 1] == "\\")

    def test_posix_path_unchanged(self):
        p = "/home/me/edit/master.srt"
        self.assertEqual(render._escape_filter_value(p), p)

    def test_single_quote_escaped(self):
        out = render._escape_filter_value("/tmp/it's/master.srt")
        self.assertIn(r"\'", out)
        self.assertNotIn("'", out.replace(r"\'", ""))  # no unescaped quote remains


if __name__ == "__main__":
    unittest.main()
