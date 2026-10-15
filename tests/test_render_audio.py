import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "render.py"
SPEC = importlib.util.spec_from_file_location("video_use_render", MODULE_PATH)
assert SPEC and SPEC.loader
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)


class RenderAudioTests(unittest.TestCase):
    def test_silent_loudness_measurement_is_rejected(self):
        measurement = {
            "input_i": "-inf",
            "input_tp": "-inf",
            "input_lra": "0.0",
            "input_thresh": "-inf",
            "target_offset": "0.0",
        }
        result = subprocess.CompletedProcess(
            [], 0, stdout="", stderr=f"loudnorm output: {json.dumps(measurement)}"
        )
        with patch.object(render.subprocess, "run", return_value=result):
            self.assertIsNone(render.measure_loudness(Path("silent.mp4")))

    def test_unmeasurable_audio_is_preserved_in_final_and_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "input.mp4"
            source.write_bytes(b"silent media")
            for preview in (False, True):
                output = Path(temp) / f"output-{preview}.mp4"
                with patch.object(render, "measure_loudness", return_value=None), patch.object(render.subprocess, "run") as run:
                    self.assertTrue(render.apply_loudnorm_two_pass(source, output, preview=preview))
                self.assertEqual(output.read_bytes(), source.read_bytes())
                run.assert_not_called()

    def _concat_command(self, segment_count):
        with tempfile.TemporaryDirectory() as temp:
            edit_dir = Path(temp)
            segments = [edit_dir / f"segment-{i}.mp4" for i in range(segment_count)]
            with patch.object(render.subprocess, "run") as run:
                render.concat_segments(segments, edit_dir / "base.mp4", edit_dir)
            return run.call_args.args[0]

    def test_single_segment_preserves_encoded_audio(self):
        cmd = self._concat_command(1)
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "copy")

    def test_multiple_segments_reencode_audio_for_continuity(self):
        cmd = self._concat_command(2)
        self.assertEqual(cmd[cmd.index("-c:a") + 1], "aac")
        self.assertEqual(cmd[cmd.index("-b:a") + 1], render.AAC_BITRATE)
        self.assertEqual(cmd[cmd.index("-ar") + 1], render.AAC_SAMPLE_RATE)


if __name__ == "__main__":
    unittest.main()
