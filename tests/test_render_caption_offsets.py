"""Caption offsets must come from the rendered segments, not the EDL's floats.

An extract is quantised to whole frames, so a segment is a fraction of a frame
longer than `end - start`. Summing the EDL's floats accumulates that error and
puts every cue progressively early.
"""
import importlib.util
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


def _word(text, start, end):
    return {"type": "word", "text": text, "start": start, "end": end}


class CaptionOffsetTests(unittest.TestCase):
    """Three 2.0s segments whose extracts each come out 0.1s long."""

    EDL = {
        "sources": {"A": "/tmp/A.mov"},
        "ranges": [
            {"source": "A", "start": 0.0, "end": 2.0},
            {"source": "A", "start": 10.0, "end": 12.0},
            {"source": "A", "start": 20.0, "end": 22.0},
        ],
    }
    MEASURED = [2.1, 2.1, 2.1]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.edit = Path(self.tmp.name)
        (self.edit / "transcripts").mkdir()
        (self.edit / "transcripts" / "A.json").write_text(json.dumps({"words": [
            _word("one.", 0.5, 1.0),
            _word("two.", 10.5, 11.0),
            _word("three.", 20.5, 21.0),
        ]}))
        self.addCleanup(self.tmp.cleanup)

    def _cues(self, segment_paths=None):
        out = self.edit / "master.srt"
        with patch.object(render, "probe_duration", side_effect=self.MEASURED):
            render.build_master_srt(self.EDL, self.edit, out, segment_paths)
        starts = []
        for line in out.read_text().splitlines():
            if " --> " in line:
                h, m, rest = line.split(" --> ")[0].split(":")
                starts.append(int(h) * 3600 + int(m) * 60 + float(rest.replace(",", ".")))
        return starts

    def test_without_segment_paths_offsets_come_from_the_edl(self):
        # 0.5, then 2.0 + 0.5, then 4.0 + 0.5 — the pre-existing behaviour
        self.assertEqual(self._cues(), [0.5, 2.5, 4.5])

    def test_with_segment_paths_offsets_come_from_the_measured_clips(self):
        # each clip is 0.1s longer than the EDL claims, and the error accumulates
        paths = [Path(f"/tmp/seg{i}.mp4") for i in range(3)]
        self.assertEqual(self._cues(paths), [0.5, 2.6, 4.7])

    def test_a_wrong_number_of_clips_falls_back_to_the_edl(self):
        # a mismatch must not silently pair clips with the wrong ranges
        self.assertEqual(self._cues([Path("/tmp/seg0.mp4")]), [0.5, 2.5, 4.5])

    def test_a_failed_probe_falls_back_to_the_edl(self):
        out = self.edit / "master.srt"
        paths = [Path(f"/tmp/seg{i}.mp4") for i in range(3)]
        with patch.object(render, "probe_duration", side_effect=OSError("no ffprobe")):
            render.build_master_srt(self.EDL, self.edit, out, paths)
        starts = [
            int(l.split(" --> ")[0].split(":")[1]) * 60
            + float(l.split(" --> ")[0].split(":")[2].replace(",", "."))
            for l in out.read_text().splitlines() if " --> " in l
        ]
        self.assertEqual(starts, [0.5, 2.5, 4.5])


if __name__ == "__main__":
    unittest.main()
