import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "render.py"
SPEC = importlib.util.spec_from_file_location("video_use_render_concat", MODULE_PATH)
assert SPEC and SPEC.loader
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)


class ConcatModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.segs = [self.dir / "seg_00.mp4", self.dir / "seg_01.mp4"]
        for s in self.segs:
            s.write_bytes(b"")
        self.out = self.dir / "base.mp4"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, mode, skew=0.0):
        calls = []
        skews = skew if isinstance(skew, list) else [skew]
        with patch.object(render.subprocess, "run", side_effect=lambda cmd, **kw: calls.append(cmd)), \
             patch.object(render, "check_av_parity", side_effect=skews), \
             patch.object(render, "probe_source_fps", return_value="30000/1001"), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            render.concat_segments(self.segs, self.out, self.dir, mode=mode)
        return calls, out.getvalue()

    def test_copy_mode_is_lossless_demuxer(self):
        calls, _ = self._run("copy")
        cmd = calls[0]
        self.assertIn("concat", cmd)
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")
        self.assertNotIn("-filter_complex", cmd)

    def test_filter_mode_retimes_with_exact_source_rate(self):
        calls, _ = self._run("filter")
        cmd = calls[0]
        self.assertEqual(cmd[cmd.index("-filter_complex") + 1], "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]")
        self.assertEqual(cmd[cmd.index("-r") + 1], "30000/1001")
        self.assertEqual(cmd.count("-i"), 2)

    def test_copy_mode_warns_on_skew_without_failing(self):
        _, out = self._run("copy", skew=0.6)
        self.assertIn("WARNING", out)
        self.assertIn("--concat filter", out)

    def test_filter_mode_refuses_a_drifted_base(self):
        with self.assertRaises(RuntimeError):
            self._run("filter", skew=0.6)

    def test_auto_mode_stays_lossless_without_drift(self):
        calls, _ = self._run("auto", skew=0.01)
        self.assertEqual(len(calls), 1)
        self.assertIn("-c", calls[0])
        self.assertNotIn("-filter_complex", calls[0])

    def test_auto_mode_retimes_with_filter_on_drift(self):
        calls, out = self._run("auto", skew=[0.6, 0.01])
        self.assertEqual(len(calls), 2)
        self.assertNotIn("-filter_complex", calls[0])
        self.assertIn("-filter_complex", calls[1])
        self.assertIn("filter", out)

    def test_auto_mode_fails_if_filter_cannot_fix_drift(self):
        with self.assertRaises(RuntimeError):
            self._run("auto", skew=[0.6, 0.6])

    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            render.concat_segments(self.segs, self.out, self.dir, mode="bogus")


class AvParityTests(unittest.TestCase):
    def test_reports_absolute_skew(self):
        with patch.object(render, "_stream_duration", side_effect=[60.7, 61.4]), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertAlmostEqual(render.check_av_parity(Path("x.mp4")), 0.7, places=6)

    def test_unprobeable_returns_none(self):
        with patch.object(render, "_stream_duration", side_effect=[None, 61.4]):
            self.assertIsNone(render.check_av_parity(Path("x.mp4")))


if __name__ == "__main__":
    unittest.main()
