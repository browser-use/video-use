import importlib.util
import io
from contextlib import redirect_stdout
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "render.py"
SPEC = importlib.util.spec_from_file_location("video_use_render", MODULE_PATH)
assert SPEC and SPEC.loader
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class SilentAudioLoudnormTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work = Path(self.temp_dir.name)
        self.source = self.work / "silent.mp4"
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i", "color=size=320x180:rate=24:duration=0.5",
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=0.5",
                "-c:v", "mpeg4", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(self.source),
            ],
            check=True,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def assert_silent_audio_is_preserved(self, preview: bool):
        output = self.work / ("preview.mp4" if preview else "final.mp4")

        self.assertTrue(render.apply_loudnorm_two_pass(self.source, output, preview=preview))

        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_type", "-of", "json", str(output),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(probe.stdout)["streams"][0]["codec_type"], "audio")
        decoded = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", str(output),
                "-map", "0:a:0", "-f", "s16le", "-acodec", "pcm_s16le", "-",
            ],
            capture_output=True,
            check=True,
        ).stdout
        self.assertTrue(decoded, "output must contain decoded audio samples")
        self.assertEqual(decoded, bytes(len(decoded)), "output audio must remain silent")
        self.assertEqual(output.read_bytes(), self.source.read_bytes())

    def test_final_render_preserves_digital_silence(self):
        self.assert_silent_audio_is_preserved(preview=False)

    def test_draft_render_preserves_digital_silence(self):
        self.assert_silent_audio_is_preserved(preview=True)


class LoudnormFallbackTests(unittest.TestCase):
    def test_failed_measurement_encodes_once_without_remeasuring(self):
        for measurement, preview in ((None, False), ({"input_i": "invalid"}, False), (None, True)):
            with self.subTest(measurement=measurement, preview=preview):
                log = io.StringIO()
                with mock.patch.object(render, "measure_loudness", return_value=measurement) as measure:
                    with mock.patch.object(render.subprocess, "run") as encode, redirect_stdout(log):
                        source, output = Path("input.mp4"), Path("output.mp4")
                        self.assertTrue(render.apply_loudnorm_two_pass(source, output, preview=preview))
                self.assertIn("measurement failed", log.getvalue())
                if preview:
                    self.assertIn("1-pass preview", log.getvalue())
                else:
                    self.assertNotIn("preview", log.getvalue())
                measure.assert_called_once_with(source)
                encode.assert_called_once()
                command = encode.call_args.args[0]
                self.assertEqual(
                    command[command.index("-af") + 1],
                    f"loudnorm=I={render.LOUDNORM_I}:TP={render.LOUDNORM_TP}:LRA={render.LOUDNORM_LRA}",
                )
                self.assertEqual(command[-1], str(output))
                self.assertTrue(encode.call_args.kwargs["check"])


if __name__ == "__main__":
    unittest.main()
