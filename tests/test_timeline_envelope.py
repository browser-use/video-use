import importlib.util
import subprocess
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "timeline_view.py"
SPEC = importlib.util.spec_from_file_location("video_use_timeline_view", MODULE_PATH)
assert SPEC and SPEC.loader
timeline_view = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(timeline_view)

RATE = 16000
SOURCE_SECONDS = 10.0
TONE = (2.80, 3.00)  # the only non-silent audio in the fake source


def fake_ffmpeg(cmd, *args, **kwargs):
    """Write the 16 kHz mono WAV that `ffmpeg -ss S -i src -t D ... out.wav` would."""
    start = float(cmd[cmd.index("-ss") + 1])
    dur = float(cmd[cmd.index("-t") + 1])
    t = np.arange(int(round(start * RATE)),
                  int(round(min(start + dur, SOURCE_SECONDS) * RATE))) / RATE
    pcm = np.where((t >= TONE[0]) & (t < TONE[1]),
                   0.5 * np.sin(2 * np.pi * 1000 * t), 0.0)
    with wave.open(cmd[-1], "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((pcm * 32767).astype("<i2").tobytes())
    return subprocess.CompletedProcess(cmd, 0)


class EnvelopeAlignmentTests(unittest.TestCase):
    def loud_span(self, start, end, samples):
        """Times (s) where the drawn envelope is loud, using the plot's own x mapping."""
        with patch.object(timeline_view.subprocess, "run", fake_ffmpeg):
            env = timeline_view.compute_envelope(Path("src.mp4"), start, end, samples=samples)
        self.assertEqual(len(env), samples)
        loud = np.flatnonzero(env > 0.5)
        if loud.size == 0:
            return None
        # render_timeline spreads bin i over the strip as i / (len(env) - 1)
        to_t = lambda i: start + i / (samples - 1) * (end - start)
        return to_t(loud[0]), to_t(loud[-1])

    def test_tone_drawn_where_it_is(self):
        tol = 0.01  # 10 ms; a drawn bin is (end - start) / samples wide
        cases = [
            (0.0, 3.0, 3240),   # 10-frame 16:9 strip
            (1.5, 4.5, 3240),   # SKILL.md's +/-1.5 s cut-boundary check
            (0.0, 3.0, 1820),
            (0.0, 3.0, 200),
            (2.0, 3.0, 3240),
            (2.5, 2.9, 3240),   # range shorter than 2 * samples PCM samples
            (2.9, 2.95, 3240),  # range shorter than samples PCM samples
        ]
        for start, end, samples in cases:
            with self.subTest(start=start, end=end, samples=samples):
                span = self.loud_span(start, end, samples)
                self.assertIsNotNone(span, "tone not drawn at all")
                first, last = span
                self.assertAlmostEqual(first, max(start, TONE[0]), delta=tol)
                self.assertAlmostEqual(last, min(end, TONE[1]), delta=tol)

    def test_silent_range_stays_flat(self):
        with patch.object(timeline_view.subprocess, "run", fake_ffmpeg):
            env = timeline_view.compute_envelope(Path("src.mp4"), 5.0, 6.0, samples=500)
        self.assertEqual(len(env), 500)
        self.assertEqual(float(env.max()), 0.0)

    def test_audio_ending_inside_range_is_not_stretched(self):
        # The source ends at 10 s: in a 9-11 s view the 9.0-9.5 s tone must stay
        # at 9.0-9.5 s, not be stretched as if 1 s of audio filled 2 s.
        global TONE
        saved, TONE = TONE, (9.0, 9.5)
        try:
            span = self.loud_span(9.0, 11.0, 400)
        finally:
            TONE = saved
        self.assertIsNotNone(span)
        self.assertAlmostEqual(span[0], 9.0, delta=0.01)
        self.assertAlmostEqual(span[1], 9.5, delta=0.01)


if __name__ == "__main__":
    unittest.main()
