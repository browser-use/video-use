"""The degraded resolver each consumer keeps when transcribe.py cannot be imported.

render.py and timeline_view.py both guard their import of the shared resolver so that a
missing sibling module costs them the resolver rather than the whole script. That fallback
has to name files the same way the real one does, or a project that loses transcribe.py
silently reads a different transcript than the one that was written.
"""

import builtins
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


HELPERS = Path(__file__).parents[1] / "helpers"
# Make `import transcribe` work when this file is run alone (python tests/… or
# unittest on this module). test_transcript_paths.py does the same; without it,
# collection order decides whether helpers/ is already on sys.path.
if str(HELPERS) not in sys.path:
    sys.path.insert(0, str(HELPERS))


def load_with_transcribe_hidden(name: str, filename: str):
    """Import helpers/<filename> with `import transcribe` failing the way a missing file would."""
    real_import = builtins.__import__

    def blocked(name_, globals_=None, locals_=None, fromlist=(), level=0):
        if name_ == "transcribe" and level == 0:
            raise ImportError("transcribe is hidden for this test")
        return real_import(name_, globals_, locals_, fromlist, level)

    saved = {k: v for k, v in sys.modules.items() if k in ("transcribe", name)}
    for k in saved:
        del sys.modules[k]
    sys.modules["transcribe"] = None  # makes `from transcribe import x` raise ImportError

    spec = importlib.util.spec_from_file_location(name, HELPERS / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    builtins.__import__ = blocked
    try:
        spec.loader.exec_module(mod)
    finally:
        builtins.__import__ = real_import
        sys.modules.pop("transcribe", None)
        sys.modules.update(saved)
    return mod


class RenderFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.render = load_with_transcribe_hidden("video_use_render_fallback", "render.py")

    def setUp(self):
        self.edit = Path(tempfile.mkdtemp()) / "edit"
        (self.edit / "transcripts").mkdir(parents=True)

    def _write(self, name):
        path = self.edit / "transcripts" / name
        path.write_text("{}")
        return path

    def test_prefers_the_current_name_over_a_stale_stem_only_file(self):
        """The old order checked the stem first, so intro.json shadowed intro.mp4.json."""
        self._write("intro.json")
        current = self._write("intro.mp4.json")
        got = self.render.resolve_transcript_for_source(self.edit, "takes/intro.mp4")
        self.assertEqual(got, current)

    def test_still_falls_back_to_the_stem_only_name(self):
        legacy = self._write("intro.json")
        got = self.render.resolve_transcript_for_source(self.edit, "takes/intro.mp4")
        self.assertEqual(got, legacy)

    def test_carries_the_track_suffix(self):
        current = self._write("intro.mp4.track1.json")
        got = self.render.resolve_transcript_for_source(self.edit, "takes/intro.mp4", 1)
        self.assertEqual(got, current)

    def test_returns_the_write_target_when_nothing_exists(self):
        got = self.render.resolve_transcript_for_source(self.edit, "takes/intro.mp4")
        self.assertEqual(got.name, "intro.mp4.json")
        self.assertFalse(got.exists())


class TimelineViewFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tv = load_with_transcribe_hidden("video_use_timeline_view_fallback", "timeline_view.py")

    def setUp(self):
        self.edit = Path(tempfile.mkdtemp()) / "edit"
        (self.edit / "transcripts").mkdir(parents=True)

    def _write(self, name):
        path = self.edit / "transcripts" / name
        path.write_text("{}")
        return path

    def test_prefers_the_current_name(self):
        current = self._write("intro.mov.json")
        self._write("intro.json")
        self.assertEqual(self.tv.resolve_transcript(self.edit, Path("takes/intro.mov")), current)

    def test_still_falls_back_to_the_stem_only_name(self):
        legacy = self._write("intro.json")
        got = self.tv.resolve_transcript(self.edit, Path("takes/intro.mov"))
        self.assertEqual(got, legacy)

    def test_carries_the_track_suffix(self):
        """The old fallback dropped .trackN, so a track rerun resolved the wrong file."""
        track = self._write("intro.mov.track2.json")
        self._write("intro.mov.track1.json")
        got = self.tv.resolve_transcript(self.edit, Path("takes/intro.mov"), 2)
        self.assertEqual(got, track)

    def test_matches_the_real_resolver_when_transcribe_is_importable(self):
        """The two implementations are only useful if they agree; check that they do."""
        import transcribe

        for audio_track in (0, 1, 3):
            with tempfile.TemporaryDirectory() as d:
                edit = Path(d) / "edit"
                (edit / "transcripts").mkdir(parents=True)
                # no files on disk, so both must land on the same write target
                self.assertEqual(
                    self.tv.resolve_transcript(edit, Path("takes/intro.mov"), audio_track),
                    transcribe.resolve_transcript(edit, Path("takes/intro.mov"), audio_track),
                )


if __name__ == "__main__":
    unittest.main()