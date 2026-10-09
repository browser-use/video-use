import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "render.py"
SPEC = importlib.util.spec_from_file_location("video_use_render", MODULE_PATH)
assert SPEC and SPEC.loader
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)


class CutFadeTests(unittest.TestCase):
    @staticmethod
    def _extract(ranges: list[dict]):
        edl = {"sources": {"a": "a.mp4"}, "ranges": ranges}
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(render, "probe_source_fps", return_value="24/1"),
                patch.object(render, "extract_segment") as extract,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                render.extract_all_segments(edl, Path(temp_dir), preview=False)
        return extract.call_args_list

    def test_defaults_to_30ms_fades_when_the_cut_says_nothing(self):
        calls = self._extract([{"source": "a", "start": 0, "end": 1}])
        self.assertEqual(calls[0].kwargs["fade_in"], 0.03)
        self.assertEqual(calls[0].kwargs["fade_out"], 0.03)

    def test_per_cut_fades_apply_only_to_the_cut_that_declares_them(self):
        calls = self._extract([
            {"source": "a", "start": 0, "end": 1, "fade_in": 0.5, "fade_out": 0.25},
            {"source": "a", "start": 2, "end": 3},
        ])
        self.assertEqual(
            [(c.kwargs["fade_in"], c.kwargs["fade_out"]) for c in calls],
            [(0.5, 0.25), (0.03, 0.03)],
        )


class SubtitleOptOutTests(unittest.TestCase):
    # One word per cut, each 0.5s into its own cut, so an SRT cue's timestamp
    # reveals the output offset the cut was placed at.
    TRANSCRIPT = {"words": [
        {"type": "word", "text": "first", "start": 0.5, "end": 1.0},
        {"type": "word", "text": "muted", "start": 5.5, "end": 6.0},
        {"type": "word", "text": "third", "start": 10.5, "end": 11.0},
    ]}

    @staticmethod
    def _srt(ranges: list[dict]) -> str:
        edl = {"sources": {"a": "a.mp4"}, "ranges": ranges}
        with tempfile.TemporaryDirectory() as temp_dir:
            edit_dir = Path(temp_dir)
            (edit_dir / "transcripts").mkdir()
            (edit_dir / "transcripts" / "a.json").write_text(
                json.dumps(SubtitleOptOutTests.TRANSCRIPT)
            )
            out_path = edit_dir / "master.srt"
            with contextlib.redirect_stdout(io.StringIO()):
                render.build_master_srt(edl, edit_dir, out_path)
            return out_path.read_text()

    def test_cut_marked_subtitles_false_contributes_no_cues(self):
        srt = self._srt([
            {"source": "a", "start": 0, "end": 2},
            {"source": "a", "start": 5, "end": 7, "subtitles": False},
        ])
        self.assertIn("FIRST", srt)
        self.assertNotIn("MUTED", srt)

    def test_skipped_cut_still_advances_the_output_offset(self):
        srt = self._srt([
            {"source": "a", "start": 0, "end": 2},
            {"source": "a", "start": 5, "end": 7, "subtitles": False},
            {"source": "a", "start": 10, "end": 12},
        ])
        # 2s + 2s of earlier cuts, then 0.5s into the third one.
        self.assertIn("00:00:04,500", srt)


if __name__ == "__main__":
    unittest.main()
