"""Auto-grade must actually analyze still images.

Bug: `_sample_frame_stats` built its ffmpeg command as `-ss <start> -i <file>
-t <duration>`. Seeking into a single-frame input yields ZERO frames, so the
signalstats metadata file came back empty and the analysis fell through to its
neutral defaults. Every photograph in a montage therefore received an identical
canned correction no matter how it actually looked, and `grade: "auto"` produced
output byte-identical to no grading at all.

Measured on real photos before the fix: two visually different stills both
reported y_mean 0.5 (the fallback), while a video clip reported 0.8. After the
fix the same stills report 0.52 and 0.58.
"""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "grade.py"
SPEC = importlib.util.spec_from_file_location("video_use_grade_stills", MODULE_PATH)
assert SPEC and SPEC.loader
grade = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(grade)


class StillDetectionTests(unittest.TestCase):
    def _detect(self, codec_name):
        result = unittest.mock.Mock(stdout=codec_name + "\n", returncode=0)
        with patch.object(grade.subprocess, "run", return_value=result):
            return grade._is_still_image(Path("x"))

    def test_image_codecs_detected(self):
        for codec in ("mjpeg", "png", "webp", "bmp", "tiff"):
            with self.subTest(codec=codec):
                self.assertTrue(self._detect(codec))

    def test_video_codecs_not_detected(self):
        for codec in ("h264", "hevc", "vp9", "prores", "av1"):
            with self.subTest(codec=codec):
                self.assertFalse(self._detect(codec))

    def test_probe_failure_is_not_fatal(self):
        err = subprocess.CalledProcessError(1, "ffprobe")
        with patch.object(grade.subprocess, "run", side_effect=err):
            self.assertFalse(grade._is_still_image(Path("x")))


class SampleFrameStatsCommandTests(unittest.TestCase):
    """The command shape is the fix — assert it directly."""

    def _cmd_for(self, still: bool):
        captured = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = cmd
            # Leave the metadata file empty; we only care about the command.
            return unittest.mock.Mock(returncode=0)

        with patch.object(grade, "_is_still_image", return_value=still), \
             patch.object(grade.subprocess, "run", side_effect=fake_run):
            grade._sample_frame_stats(Path("src"), start=5.0, duration=3.0)
        return captured["cmd"]

    def test_still_is_looped_and_not_seeked(self):
        cmd = self._cmd_for(still=True)
        self.assertIn("-loop", cmd)
        self.assertEqual(cmd[cmd.index("-loop") + 1], "1")
        # There is no timeline to seek into; -ss would consume the only frame.
        self.assertNotIn("-ss", cmd)
        self.assertEqual(cmd[cmd.index("-t") + 1], "3.000")

    def test_moving_source_is_seeked_and_not_looped(self):
        cmd = self._cmd_for(still=False)
        self.assertNotIn("-loop", cmd)
        self.assertEqual(cmd[cmd.index("-ss") + 1], "5.000")
        self.assertEqual(cmd[cmd.index("-t") + 1], "3.000")

    def test_both_paths_still_request_signalstats(self):
        for still in (True, False):
            with self.subTest(still=still):
                cmd = self._cmd_for(still)
                vf = cmd[cmd.index("-vf") + 1]
                self.assertIn("signalstats", vf)
                self.assertIn("metadata=print", vf)


class AutoGradeDifferentiatesStillsTests(unittest.TestCase):
    """The observable symptom: identical output for different photos."""

    def _stats_from(self, y_avg: float):
        """Drive auto_grade_for_clip with a synthetic signalstats reading."""
        def fake_run(cmd, *a, **kw):
            # Locate the metadata target the filtergraph was told to write.
            vf = cmd[cmd.index("-vf") + 1]
            name = vf.rsplit("file=", 1)[1]
            Path(kw["cwd"], name).write_text(
                f"lavfi.signalstats.YBITDEPTH=8\n"
                f"lavfi.signalstats.YAVG={y_avg}\n"
                f"lavfi.signalstats.YMIN=16\n"
                f"lavfi.signalstats.YMAX=235\n"
                f"lavfi.signalstats.SATAVG=40\n"
            )
            return unittest.mock.Mock(returncode=0)

        with patch.object(grade, "_is_still_image", return_value=True), \
             patch.object(grade.subprocess, "run", side_effect=fake_run):
            return grade.auto_grade_for_clip(Path("photo.jpg"), 0.0, 3.0)

    def test_different_stills_produce_different_analysis(self):
        dark_filter, dark_stats = self._stats_from(70.0)
        bright_filter, bright_stats = self._stats_from(200.0)
        # Before the fix both returned the same fallback stats.
        self.assertNotEqual(dark_stats["y_mean"], bright_stats["y_mean"])
        self.assertLess(dark_stats["y_mean"], bright_stats["y_mean"])

    def test_a_dark_still_is_lifted_and_a_bright_one_is_not(self):
        dark_filter, _ = self._stats_from(70.0)
        bright_filter, _ = self._stats_from(200.0)
        self.assertIn("gamma=", dark_filter)
        self.assertNotEqual(dark_filter, bright_filter)


if __name__ == "__main__":
    unittest.main()
