"""End-to-end A/V sync across cuts (needs ffmpeg; skipped without it).

A synthetic source has a 2 ms click at every whole second. After render.py cuts it
into five frame-exact segments, every click must land where the EDL puts it in the
output: sum of previous segment durations + (click - segment start). Per-segment AAC
used to push the audio ~25-35 ms later at every cut.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

HELPERS = Path(__file__).resolve().parent.parent / "helpers"
RANGES = [(0.5, 2.2), (3.3, 5.1), (6.2, 7.9), (8.4, 9.6), (10.3, 11.8)]  # all whole frames at 30 fps
SR = 48000


def click_times(media: Path) -> list[float]:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    x = np.abs(np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768)
    hits = np.flatnonzero(x > 0.3)
    starts = hits[np.concatenate([[True], np.diff(hits) > SR // 10])]
    return [i / SR for i in starts]


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not installed")
class AVSyncAcrossCuts(unittest.TestCase):
    def test_clicks_stay_on_the_edl_timeline(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            src = d / "src.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=12",
                "-f", "lavfi", "-i", f"aevalsrc=if(lt(mod(t\\,1)\\,0.002)\\,0.8\\,0):s={SR}:d=12",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-shortest", str(src.with_suffix(".mov")),
            ], check=True)
            src = src.with_suffix(".mov")
            edit = d / "edit"
            edit.mkdir()
            edl = {"version": 1, "sources": {"S": str(src)},
                   "ranges": [{"source": "S", "start": a, "end": b} for a, b in RANGES]}
            (edit / "edl.json").write_text(json.dumps(edl), encoding="utf-8")
            out = edit / "out.mp4"
            subprocess.run([sys.executable, str(HELPERS / "render.py"), str(edit / "edl.json"), "-o", str(out),
                            "--draft", "--no-loudnorm", "--fps", "30"], check=True, capture_output=True)

            expected, offset = [], 0.0
            for a, b in RANGES:
                expected += [offset + (c - a) for c in range(int(a) + 1, int(b) + 1) if a + 0.05 < c < b - 0.05]
                offset += b - a
            got = click_times(out)
            self.assertEqual(len(got), len(expected), (got, expected))
            for g, e in zip(got, expected):
                self.assertAlmostEqual(g, e, delta=0.010, msg=f"click expected at {e:.3f}s, found at {g:.3f}s")


class SegmentAudioIsPCM(unittest.TestCase):
    """Runs without ffmpeg: the per-segment extract must not encode AAC."""

    def test_extract_segment_writes_pcm(self):
        import importlib.util
        from unittest import mock

        spec = importlib.util.spec_from_file_location("render", HELPERS / "render.py")
        render = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(render)
        with mock.patch.object(render, "is_portrait_source", return_value=False), \
             mock.patch.object(render, "is_hdr_source", return_value=False), \
             mock.patch.object(render.subprocess, "run") as run:
            render.extract_segment(Path("src.mp4"), 1.0, 2.0, "", Path(tempfile.gettempdir()) / "seg.mov", rate="30")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "pcm_s16le")


if __name__ == "__main__":
    unittest.main()
