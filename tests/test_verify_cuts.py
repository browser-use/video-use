import importlib.util
import io
import math
import struct
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "verify_cuts.py"
SPEC = importlib.util.spec_from_file_location("video_use_verify_cuts", MODULE_PATH)
assert SPEC and SPEC.loader
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


class OutputBoundaryTests(unittest.TestCase):
    def test_builds_cumulative_output_times_from_edl_ranges(self):
        edl = {
            "ranges": [
                {"start": 2.0, "end": 4.25},
                {"start": 7.0, "end": 8.5},
                {"start": 0.5, "end": 2.5},
            ]
        }
        self.assertEqual(verify.output_boundaries(edl), [2.25, 3.75])

    def test_rejects_non_positive_or_missing_ranges(self):
        for edl in ({"ranges": []}, {"ranges": [{"start": 1, "end": 1}]}):
            with self.subTest(edl=edl), self.assertRaises(verify.VerificationError):
                verify.output_boundaries(edl)

    def test_rejects_non_object_edl(self):
        for edl in (None, [], "ranges"):
            with self.subTest(edl=edl), self.assertRaises(verify.VerificationError):
                verify.output_boundaries(edl)


class AudioBoundaryTests(unittest.TestCase):
    @staticmethod
    def _pcm(click: bool) -> tuple[io.BytesIO, int]:
        rate = verify.AUDIO_RATE
        count = rate * 2
        samples = [int(5000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(count)]
        if click:
            samples[int(1.1 * rate)] = 30_000
        return io.BytesIO(struct.pack(f"<{count}h", *samples)), count

    def test_flags_a_large_transient_near_a_cut(self):
        pcm, count = self._pcm(click=True)
        result = verify.inspect_audio_boundary(pcm, count, 1.0)
        self.assertEqual(result["status"], "review")
        self.assertGreaterEqual(result["peak_delta_samples"], 1200)

    def test_flags_the_reported_five_to_one_click_ratio(self):
        rate = verify.AUDIO_RATE
        count = rate * 2
        samples = [int(1550 * math.sin(2 * math.pi * 2000 * i / rate)) for i in range(count)]
        index = int(1.1 * rate)
        samples[index] = samples[index - 1] + 2038
        pcm = io.BytesIO(struct.pack(f"<{count}h", *samples))
        result = verify.inspect_audio_boundary(pcm, count, 1.0)
        self.assertGreaterEqual(result["peak_to_local_p95_ratio"], 5.0)
        self.assertEqual(result["status"], "review")

    def test_does_not_flag_a_continuous_tone(self):
        pcm, count = self._pcm(click=False)
        result = verify.inspect_audio_boundary(pcm, count, 1.0)
        self.assertEqual(result["status"], "clear")

    def test_marks_short_audio_window_unavailable(self):
        pcm = io.BytesIO(struct.pack("<2400h", *([0] * 2400)))
        result = verify.inspect_audio_boundary(pcm, 2400, 1.0)
        self.assertEqual(result["status"], "unavailable")


class VideoBoundaryTests(unittest.TestCase):
    def test_flags_one_frame_flash(self):
        frames = [(-0.0667, 40), (-0.0333, 42), (0.0, 245), (0.0333, 41), (0.0667, 40)]
        result = verify.inspect_video_boundary(frames, 0.0, fps=30)
        self.assertEqual(result["status"], "review")

    def test_does_not_flag_a_sustained_scene_change(self):
        frames = [(-0.0667, 40), (-0.0333, 42), (0.0, 90), (0.0333, 95), (0.0667, 99)]
        result = verify.inspect_video_boundary(frames, 0.0, fps=30)
        self.assertEqual(result["status"], "clear")

    def test_marks_insufficient_frames_unavailable(self):
        frames = [(-0.0333, 40), (0.0, 42)]
        result = verify.inspect_video_boundary(frames, 0.0, fps=30)
        self.assertEqual(result["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
