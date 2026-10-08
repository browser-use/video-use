import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "helpers"))
from transcribe_batch import find_videos  # noqa: E402


class FindVideosTests(unittest.TestCase):
    def test_rejects_same_stem_with_different_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("intro.mp4", "intro.mov", "outro.mp4"):
                (Path(tmp) / name).touch()
            with self.assertRaises(SystemExit) as ctx:
                find_videos(Path(tmp))
            self.assertIn("intro.mov, intro.mp4", str(ctx.exception))

    def test_rejects_stems_differing_only_by_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("Intro.mp4", "intro.mov"):
                (Path(tmp) / name).touch()
            with self.assertRaises(SystemExit):
                find_videos(Path(tmp))

    def test_distinct_stems_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a.mp4", "b.mov"):
                (Path(tmp) / name).touch()
            self.assertEqual([v.name for v in find_videos(Path(tmp))], ["a.mp4", "b.mov"])


if __name__ == "__main__":
    unittest.main()
