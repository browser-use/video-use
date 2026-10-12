r"""Regression test for cross-platform font selection in timeline_view.py.

Bug: FONT_CANDIDATES listed only macOS and Linux font paths. On Windows every
candidate failed Path.exists(), so load_font fell through to PIL's default
bitmap font (which ignores the requested size), rendering the filmstrip labels
nearly illegibly. Fix: add Windows candidates derived from %WINDIR%\Fonts
(Consolas + Arial). These tests pin the candidate list and the env-derived
font dir, and confirm load_font always returns a usable font — on any host.
"""
import os
import sys
import unittest
from pathlib import Path

HELPERS_DIR = Path(__file__).resolve().parent.parent / "helpers"
sys.path.insert(0, str(HELPERS_DIR))
import timeline_view  # noqa: E402


class FontCandidatesTest(unittest.TestCase):
    def test_windows_fonts_present(self):
        # Both Windows fallbacks must be in the candidate list, under a Fonts dir.
        names = [Path(c).name.lower() for c in timeline_view.FONT_CANDIDATES]
        self.assertIn("consola.ttf", names)
        self.assertIn("arial.ttf", names)
        for c in timeline_view.FONT_CANDIDATES:
            if Path(c).name.lower() in ("consola.ttf", "arial.ttf"):
                self.assertEqual(Path(c).parent.name, "Fonts")

    def test_macos_and_linux_fonts_still_present(self):
        # The fix must not drop the original POSIX candidates.
        joined = " ".join(timeline_view.FONT_CANDIDATES)
        self.assertIn("/System/Library/Fonts/Menlo.ttc", joined)
        self.assertIn("DejaVuSansMono.ttf", joined)

    def test_win_fonts_dir_respects_windir(self):
        # The Windows font dir is derived from %WINDIR%, not hardcoded to C:.
        windir = os.environ.get("WINDIR", r"C:\Windows")
        self.assertEqual(timeline_view._WIN_FONTS, Path(windir) / "Fonts")

    def test_load_font_never_raises(self):
        # load_font must return a usable font object on any host, falling back
        # to PIL's default only when no candidate file exists.
        font = timeline_view.load_font(14)
        self.assertIsNotNone(font)


if __name__ == "__main__":
    unittest.main()
