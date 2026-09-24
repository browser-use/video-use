import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "timeline_view.py"
SPEC = importlib.util.spec_from_file_location("video_use_timeline_view", MODULE_PATH)
assert SPEC and SPEC.loader
timeline_view = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(timeline_view)


def _ffmpeg_writing_frames(cmd, **kwargs):
    """Stand in for a working ffmpeg: create the output file it was asked for."""
    Path(cmd[-1]).write_bytes(b"jpeg")
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def _ffmpeg_writing_nothing(cmd, **kwargs):
    """Stand in for ffmpeg seeking past the last frame: exit 0, write no file."""
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def _seek_times(run_mock) -> list[float]:
    times = []
    for call in run_mock.call_args_list:
        cmd = call.args[0]
        if cmd[0] == "ffmpeg":
            times.append(float(cmd[cmd.index("-ss") + 1]))
    return times


class ExtractFramesRangeTests(unittest.TestCase):
    def _extract(self, start, end, n=10, duration=4.0, ffmpeg=_ffmpeg_writing_frames):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(timeline_view, "probe_duration", return_value=duration), \
                 patch.object(timeline_view.subprocess, "run", side_effect=ffmpeg) as run:
                paths = timeline_view.extract_frames(
                    Path("source.mp4"), start, end, n, Path(tmp)
                )
            return paths, _seek_times(run)

    def test_end_at_duration_stays_inside_the_decodable_range(self):
        """Seeking at the duration yields no frame, so the range must be clamped."""
        paths, times = self._extract(0, 4.0)
        self.assertEqual(len(paths), 10)
        self.assertLess(max(times), 4.0)

    def test_end_past_duration_is_clamped_too(self):
        _, times = self._extract(0, 6.0)
        self.assertLess(max(times), 4.0)

    def test_range_inside_the_clip_is_left_alone(self):
        _, times = self._extract(1.0, 3.0)
        self.assertAlmostEqual(min(times), 1.0)
        self.assertAlmostEqual(max(times), 3.0)

    def test_single_frame_request_is_clamped(self):
        _, times = self._extract(4.0, 4.0, n=1)
        self.assertLess(times[0], 4.0)

    def test_unprobeable_source_reports_the_missing_frame(self):
        """ffmpeg exits 0 when it writes nothing, so the file must be checked."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(timeline_view, "probe_duration", return_value=None), \
                 patch.object(timeline_view.subprocess, "run",
                              side_effect=_ffmpeg_writing_nothing):
                with self.assertRaises(RuntimeError) as caught:
                    timeline_view.extract_frames(
                        Path("source.mp4"), 0, 4.0, 10, Path(tmp)
                    )
        self.assertIn("no frame", str(caught.exception))


class ProbeDurationTests(unittest.TestCase):
    def test_parses_ffprobe_output(self):
        result = subprocess.CompletedProcess([], 0, stdout="12.500000\n", stderr="")
        with patch.object(timeline_view.subprocess, "run", return_value=result):
            self.assertEqual(timeline_view.probe_duration(Path("source.mp4")), 12.5)

    def test_returns_none_when_ffprobe_cannot_tell(self):
        for failure in (
            subprocess.CalledProcessError(1, "ffprobe"),
            None,
        ):
            with self.subTest(failure=type(failure).__name__):
                if failure is None:
                    result = subprocess.CompletedProcess([], 0, stdout="N/A\n", stderr="")
                    ctx = patch.object(timeline_view.subprocess, "run", return_value=result)
                else:
                    ctx = patch.object(timeline_view.subprocess, "run", side_effect=failure)
                with ctx:
                    self.assertIsNone(timeline_view.probe_duration(Path("source.mp4")))


if __name__ == "__main__":
    unittest.main()
