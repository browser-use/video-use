"""Mixed-orientation canvas + music-bed montage support.

Gap this covers: render.py scaled portrait sources by height (-2:1920) and
landscape by width (1920:-2), so a montage mixing phone verticals with landscape
footage produced segments of DIFFERENT dimensions — which the `-c copy` concat
in Rule 2 cannot join. There was also no music-bed support at all, so a montage
whose spine is a song had no path through this renderer.
"""

import importlib.util
import subprocess
import io
import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "render.py"
SPEC = importlib.util.spec_from_file_location("video_use_render_montage", MODULE_PATH)
assert SPEC and SPEC.loader
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)


class BlurredFillChainTests(unittest.TestCase):
    def test_chain_is_single_in_single_out_so_it_drops_into_vf(self):
        chain = render.blurred_fill_chain(1920, 1080)
        # No leading or trailing pad labels — otherwise it cannot be used in -vf.
        self.assertFalse(chain.startswith("["))
        self.assertTrue(chain.endswith("(H-h)/2"))

    def test_background_covers_and_foreground_fits(self):
        chain = render.blurred_fill_chain(1920, 1080)
        self.assertIn("scale=1920:1080:force_original_aspect_ratio=increase", chain)
        self.assertIn("crop=1920:1080", chain)
        self.assertIn("scale=1920:1080:force_original_aspect_ratio=decrease", chain)
        self.assertIn("overlay=(W-w)/2:(H-h)/2", chain)

    def test_honours_canvas_dimensions(self):
        chain = render.blurred_fill_chain(1280, 720)
        self.assertIn("crop=1280:720", chain)
        self.assertNotIn("1920", chain)


class CanvasSegmentTests(unittest.TestCase):
    """The regression: portrait and landscape must render to the SAME size."""

    def _vf_for(self, portrait: bool, canvas: bool, draft: bool = False) -> str:
        captured = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = cmd
            return unittest.mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "seg.mp4"
            with patch.object(render, "is_portrait_source", return_value=portrait), \
                 patch.object(render, "is_hdr_source", return_value=False), \
                 patch.object(render.subprocess, "run", side_effect=fake_run):
                render.extract_segment(
                    Path("src.mp4"), 0.0, 2.0, "", out,
                    rate="30/1", canvas=canvas, draft=draft,
                )
        cmd = captured["cmd"]
        return cmd[cmd.index("-vf") + 1]

    def test_without_canvas_orientations_diverge(self):
        # Documents the pre-existing behaviour the canvas mode exists to escape.
        self.assertIn("scale=-2:1920", self._vf_for(portrait=True, canvas=False))
        self.assertIn("scale=1920:-2", self._vf_for(portrait=False, canvas=False))

    def test_with_canvas_orientations_produce_identical_filters(self):
        portrait_vf = self._vf_for(portrait=True, canvas=True)
        landscape_vf = self._vf_for(portrait=False, canvas=True)
        self.assertEqual(portrait_vf, landscape_vf)
        self.assertIn("crop=1920:1080", portrait_vf)

    def test_draft_canvas_uses_720p(self):
        vf = self._vf_for(portrait=True, canvas=True, draft=True)
        self.assertIn("crop=1280:720", vf)

    def test_grade_is_applied_after_the_canvas(self):
        captured = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = cmd
            return unittest.mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as td:
            with patch.object(render, "is_portrait_source", return_value=True), \
                 patch.object(render, "is_hdr_source", return_value=False), \
                 patch.object(render.subprocess, "run", side_effect=fake_run):
                render.extract_segment(
                    Path("src.mp4"), 0.0, 2.0, "eq=saturation=1.2",
                    Path(td) / "seg.mp4", rate="30/1", canvas=True,
                )
        cmd = captured["cmd"]
        vf = cmd[cmd.index("-vf") + 1]
        self.assertLess(vf.index("overlay="), vf.index("eq=saturation=1.2"))


class MuteSegmentTests(unittest.TestCase):
    def _af_for(self, mute: bool) -> str:
        captured = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = cmd
            return unittest.mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as td:
            with patch.object(render, "is_portrait_source", return_value=False), \
                 patch.object(render, "is_hdr_source", return_value=False), \
                 patch.object(render, "has_audio_stream", return_value=True), \
                 patch.object(render.subprocess, "run", side_effect=fake_run):
                render.extract_segment(
                    Path("src.mp4"), 0.0, 2.0, "", Path(td) / "seg.mp4",
                    rate="30/1", mute=mute,
                )
        cmd = captured["cmd"]
        return cmd[cmd.index("-af") + 1]

    def test_mute_silences_but_keeps_the_stream_and_the_fades(self):
        af = self._af_for(mute=True)
        self.assertTrue(af.startswith("volume=0,"))
        # Fades must survive — a dropped audio stream breaks the -c copy concat.
        self.assertIn("afade=t=in", af)
        self.assertIn("afade=t=out", af)

    def test_unmuted_segments_are_unchanged(self):
        self.assertNotIn("volume=0", self._af_for(mute=False))


class SilentSourceTests(unittest.TestCase):
    """A silent source must still yield a segment with an audio stream.

    Without this the -c copy concat produces holes in the audio timeline and the
    music bed has no [0:a] to duck under — a photo montage crashed the render.
    """

    def _cmd_for(self, source_has_audio: bool):
        captured = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = cmd
            return unittest.mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as td:
            with patch.object(render, "is_portrait_source", return_value=False), \
                 patch.object(render, "is_hdr_source", return_value=False), \
                 patch.object(render, "has_audio_stream", return_value=source_has_audio), \
                 patch.object(render.subprocess, "run", side_effect=fake_run):
                render.extract_segment(
                    Path("src.mp4"), 0.0, 2.0, "", Path(td) / "seg.mp4", rate="30/1",
                )
        return captured["cmd"]

    def test_silent_source_gets_synthesized_silence(self):
        cmd = self._cmd_for(source_has_audio=False)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000", cmd)
        self.assertIn("-map", cmd)
        self.assertIn("1:a", cmd)
        # Audio fades are meaningless on synthesized silence and -af would bind
        # to the wrong input once a second input exists.
        self.assertNotIn("-af", cmd)

    def test_source_with_audio_is_untouched(self):
        cmd = self._cmd_for(source_has_audio=True)
        self.assertNotIn("anullsrc", " ".join(cmd))
        self.assertIn("-af", cmd)

    def test_both_paths_still_encode_an_audio_stream(self):
        for has_audio in (True, False):
            with self.subTest(source_has_audio=has_audio):
                cmd = self._cmd_for(has_audio)
                self.assertEqual(cmd[cmd.index("-c:a") + 1], "aac")


class MusicBedTests(unittest.TestCase):
    def _cmd_for(self, cfg, duration=60.0, base_has_audio=True):
        captured = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = cmd
            return unittest.mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as td:
            edit_dir = Path(td)
            (edit_dir / "bed.mp3").write_bytes(b"x")
            with patch.object(render, "probe_duration", return_value=duration), \
                 patch.object(render, "has_audio_stream", return_value=base_has_audio), \
                 patch.object(render.subprocess, "run", side_effect=fake_run), \
                 contextlib.redirect_stdout(io.StringIO()):
                render.mix_music_bed(
                    edit_dir / "base.mp4", cfg, edit_dir / "out.mp4", edit_dir
                )
        return captured.get("cmd")

    def test_base_without_audio_falls_back_to_music_only_and_caps_length(self):
        # amix's duration=first is what stops the infinitely looped bed; without
        # amix the render needs -shortest or it never terminates.
        cmd = self._cmd_for({"source": "bed.mp3"}, base_has_audio=False)
        filt = cmd[cmd.index("-filter_complex") + 1]
        self.assertNotIn("amix", filt)
        self.assertNotIn("[0:a]", filt)
        self.assertIn("-shortest", cmd)

    def test_normal_path_does_not_pass_shortest(self):
        self.assertNotIn("-shortest", self._cmd_for({"source": "bed.mp3"}))

    def test_video_is_stream_copied_not_re_encoded(self):
        cmd = self._cmd_for({"source": "bed.mp3"})
        self.assertIn("copy", cmd[cmd.index("-c:v") + 1])

    def test_amix_disables_normalize_so_explicit_gains_survive(self):
        cmd = self._cmd_for({"source": "bed.mp3"})
        filt = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("normalize=0", filt)
        self.assertIn("duration=first", filt)

    def test_gains_default_to_music_over_ducked_natural(self):
        cmd = self._cmd_for({"source": "bed.mp3"})
        filt = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn(f"volume={render.MUSIC_GAIN}", filt)
        self.assertIn(f"volume={render.MUSIC_NATURAL_GAIN}", filt)

    def test_explicit_gains_override_defaults(self):
        cmd = self._cmd_for({"source": "bed.mp3", "gain": 0.6, "natural_gain": 0.2})
        filt = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("volume=0.6", filt)
        self.assertIn("volume=0.2", filt)

    def test_short_bed_is_looped(self):
        cmd = self._cmd_for({"source": "bed.mp3"})
        self.assertIn("-stream_loop", cmd)
        self.assertEqual(cmd[cmd.index("-stream_loop") + 1], "-1")

    def test_tail_fade_is_placed_from_the_video_duration(self):
        cmd = self._cmd_for({"source": "bed.mp3", "fade_out": 4.0}, duration=100.0)
        filt = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("afade=t=out:st=96.000:d=4.000", filt)

    def test_missing_music_file_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as td:
            edit_dir = Path(td)
            with patch.object(render.subprocess, "run") as run, \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                render.mix_music_bed(
                    edit_dir / "base.mp4", {"source": "nope.mp3"},
                    edit_dir / "out.mp4", edit_dir,
                )
            run.assert_not_called()
            self.assertIn("warning", out.getvalue().lower())


class CanvasEdlWiringTests(unittest.TestCase):
    def _canvas_flags(self, edl_extra):
        seen = []

        def fake_extract(src, start, duration, filt, out, **kw):
            seen.append(kw)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"x")

        with tempfile.TemporaryDirectory() as td:
            edit_dir = Path(td)
            (edit_dir / "a.mp4").write_bytes(b"x")
            edl = {
                "sources": {"a": "a.mp4"},
                "ranges": [
                    {"source": "a", "start": 0, "end": 2},
                    {"source": "a", "start": 2, "end": 4, "mute": True},
                ],
            }
            edl.update(edl_extra)
            with patch.object(render, "extract_segment", side_effect=fake_extract), \
                 patch.object(render, "probe_source_fps", return_value="30/1"), \
                 contextlib.redirect_stdout(io.StringIO()):
                render.extract_all_segments(edl, edit_dir, preview=False)
        return seen

    def test_canvas_fill_enables_canvas_on_every_segment(self):
        for value in ("fill", "blur", "blurred_fill", "FILL"):
            with self.subTest(value=value):
                flags = self._canvas_flags({"canvas": value})
                self.assertTrue(all(f["canvas"] for f in flags))

    def test_canvas_defaults_off_for_existing_edls(self):
        flags = self._canvas_flags({})
        self.assertTrue(all(not f["canvas"] for f in flags))

    def test_per_range_mute_is_passed_through(self):
        flags = self._canvas_flags({"canvas": "fill"})
        self.assertEqual([f["mute"] for f in flags], [False, True])


if __name__ == "__main__":
    unittest.main()


class RealFootageRegressionTests(unittest.TestCase):
    """Three defects found only by feeding real footage through the renderer.

    Synthetic clips cut from one source share codec parameters and are always
    moving video, so none of these surfaced until a real LiveBarn export (mono
    audio) and real iPhone stills (single-frame JPEGs) went in.
    """

    def _cmd(self, *, still=False, has_audio=True):
        captured = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = cmd
            return unittest.mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as td:
            with patch.object(render, "is_portrait_source", return_value=False), \
                 patch.object(render, "is_hdr_source", return_value=False), \
                 patch.object(render, "is_still_image", return_value=still), \
                 patch.object(render, "has_audio_stream", return_value=has_audio), \
                 patch.object(render.subprocess, "run", side_effect=fake_run):
                render.extract_segment(
                    Path("src"), 5.0, 3.0, "", Path(td) / "seg.mp4", rate="30/1",
                )
        return captured["cmd"]

    # --- 1. mono source collapsed the whole reel to mono -------------------
    def test_channel_count_is_forced_uniform(self):
        # The concat demuxer takes parameters from the FIRST segment, so one
        # mono source (a LiveBarn export) silently made the entire montage mono
        # — including the stereo music bed mixed on afterwards.
        cmd = self._cmd()
        self.assertEqual(cmd[cmd.index("-ac") + 1], "2")

    def test_channel_count_forced_for_silent_sources_too(self):
        cmd = self._cmd(has_audio=False)
        self.assertEqual(cmd[cmd.index("-ac") + 1], "2")

    # --- 2. stills produced a segment with no video frames ----------------
    def test_still_image_is_looped(self):
        # Without -loop 1 ffmpeg decodes exactly one frame; the segment then
        # reports a duration borrowed from its audio but has no real video, and
        # the concatenated timeline comes out the wrong length.
        cmd = self._cmd(still=True)
        self.assertIn("-loop", cmd)
        self.assertEqual(cmd[cmd.index("-loop") + 1], "1")
        self.assertEqual(cmd[cmd.index("-t") + 1], "3.000")

    def test_still_image_is_not_seeked(self):
        # -ss into a single frame seeks past the end and yields nothing.
        self.assertNotIn("-ss", self._cmd(still=True))

    def test_moving_source_is_still_seeked(self):
        cmd = self._cmd(still=False)
        self.assertEqual(cmd[cmd.index("-ss") + 1], "5.000")
        self.assertNotIn("-loop", cmd)

    # --- 3. stills carry no audio, so they need synthesized silence -------
    def test_still_image_gets_synthesized_audio(self):
        cmd = self._cmd(still=True, has_audio=True)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000", cmd)


class StillImageDetectionTests(unittest.TestCase):
    def _detect(self, codec_name):
        result = unittest.mock.Mock(stdout=codec_name + "\n", returncode=0)
        with patch.object(render.subprocess, "run", return_value=result):
            return render.is_still_image(Path("x"))

    def test_image_codecs_detected(self):
        for codec in ("mjpeg", "png", "webp", "bmp"):
            with self.subTest(codec=codec):
                self.assertTrue(self._detect(codec))

    def test_video_codecs_not_detected(self):
        for codec in ("h264", "hevc", "vp9", "prores"):
            with self.subTest(codec=codec):
                self.assertFalse(self._detect(codec))

    def test_probe_failure_is_not_fatal(self):
        err = subprocess.CalledProcessError(1, "ffprobe")
        with patch.object(render.subprocess, "run", side_effect=err):
            self.assertFalse(render.is_still_image(Path("x")))
