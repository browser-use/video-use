import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "render.py"
SPEC = importlib.util.spec_from_file_location("video_use_render", MODULE_PATH)
assert SPEC and SPEC.loader
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class SilentLoudnormTests(unittest.TestCase):
    def test_silent_audio_is_copied_instead_of_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "silent.mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y",
                 "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=1",
                 "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=1",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                 "-shortest", str(src)],
                check=True,
            )
            for preview in (True, False):
                with self.subTest(preview=preview):
                    out = Path(tmp) / f"out_{preview}.mp4"
                    self.assertFalse(render.apply_loudnorm_two_pass(src, out, preview=preview))
                    self.assertEqual(out.read_bytes(), src.read_bytes())


if __name__ == "__main__":
    unittest.main()
