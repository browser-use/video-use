"""Render disposable videos and inspect the actual encoded picture, captions and audio."""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


# Surface the failing media command and its diagnostics without an unbounded log.
def run(command):
    result = subprocess.run([str(part) for part in command], capture_output=True, timeout=90)
    if result.returncode:
        raise AssertionError(f"Command failed: {command}\n{result.stderr.decode(errors='replace')[-4000:]}")
    return result.stdout


# Probe encoded stream facts rather than trusting the requested export settings.
def probe(path):
    return json.loads(run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", path]))


# Decode an output frame for pixel checks at a specific point in the finished video.
def frame(path, time):
    data = run(["ffmpeg", "-v", "error", "-ss", str(time), "-i", path, "-frames:v", "1", "-f", "image2pipe", "-c:v", "ppm", "-"])
    with Image.open(io.BytesIO(data)) as image:
        return np.array(image.convert("RGB"))


# Test the public CLI with generated media and no provider credentials or downloads.
class VideoExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
        if not missing:
            filters = run(["ffmpeg", "-hide_banner", "-filters"]).decode()
            encoders = run(["ffmpeg", "-hide_banner", "-encoders"]).decode()
            if " subtitles " not in filters:
                missing.append("FFmpeg subtitles filter (libass)")
            if " libx264 " not in encoders:
                missing.append("FFmpeg libx264 encoder")
        if missing:
            message = "Real export tests require: " + ", ".join(missing)
            if os.environ.get("VIDEO_USE_REQUIRE_EXPORT_TESTS") == "1":
                raise AssertionError(message)
            raise unittest.SkipTest(message)
        cls.temporary = tempfile.TemporaryDirectory(prefix="video-use-exports-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.folder = Path(cls.temporary.name)
        cls.sources = {}
        for name, color, size, rate, frequency in (
            ("red", "0x880000", "160x90", 30, 440),
            ("blue", "0x000088", "160x90", 25, 880),
            ("portrait", "0x880000", "90x160", 30, 440),
        ):
            path = cls.folder / f"{name}.mp4"
            run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r={rate}:d=2",
                 "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=2",
                 "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-shortest", path])
            cls.sources[name] = path
        rotated = cls.folder / "rotated.mp4"
        run(["ffmpeg", "-v", "error", "-i", cls.sources["red"], "-c", "copy", "-metadata:s:v:0", "rotate=90", rotated])
        rotation = lambda: any(abs(float(row.get("rotation", 0))) == 90
                               for stream in probe(rotated)["streams"]
                               for row in stream.get("side_data_list", []))
        # New FFmpeg releases use an explicit display option instead of the old tag.
        if not rotation():
            run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", cls.sources["red"], "-c", "copy", rotated])
        if not rotation():
            raise AssertionError("Rotation fixture has no quarter-turn display matrix")
        cls.sources["rotated"] = rotated
        cls.overlay = cls.folder / "overlay.mp4"
        run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0x008800:s=1280x720:r=30:d=1",
             "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-pix_fmt", "yuv420p", cls.overlay])
        cls.originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in [*cls.sources.values(), cls.overlay]}

    def export(self, name, edl, *options, transcripts=None):
        edit = self.folder / name / "edit"
        edit.mkdir(parents=True)
        if transcripts:
            (edit / "transcripts").mkdir()
            for source, words in transcripts.items():
                (edit / "transcripts" / f"{source}.json").write_text(json.dumps({"words": words}), encoding="utf-8")
        manifest = edit / "edl.json"
        manifest.write_text(json.dumps(edl), encoding="utf-8")
        output = edit / "final.mp4"
        run([sys.executable, ROOT / "helpers/render.py", manifest, "-o", output, "--draft", "--no-loudnorm", *options])
        # A full decode catches damaged packets that metadata-only checks miss.
        run(["ffmpeg", "-v", "error", "-xerror", "-i", output, "-f", "null", "-"])
        for path, digest in self.originals.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, f"Source was modified: {path.name}")
        return output, probe(output)

    def test_cuts_overlay_captions_and_audio_in_the_finished_file(self):
        edl = {"version": 1, "sources": {k: str(self.sources[k]) for k in ("red", "blue")},
               "ranges": [{"source": "red", "start": 0.2, "end": 1.2}, {"source": "blue", "start": 0.3, "end": 1.3}],
               "grade": "none", "overlays": [{"file": str(self.overlay), "start_in_output": 0.2, "duration": 0.6}]}
        transcripts = {"red": [{"type": "word", "text": "FIRST", "start": 0.5, "end": 0.9}],
                       "blue": [{"type": "word", "text": "SECOND", "start": 0.6, "end": 1.0}]}
        output, info = self.export("combined", edl, "--build-subtitles", transcripts=transcripts)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
        self.assertEqual((video["width"], video["height"]), (1280, 720))
        self.assertEqual(video["r_frame_rate"], "30/1")
        self.assertEqual(video["pix_fmt"], "yuv420p")
        self.assertEqual(video["codec_name"], "h264")
        self.assertEqual(audio["sample_rate"], "48000")
        # The legacy copy-concat path can retain a few AAC padding frames.
        self.assertAlmostEqual(float(info["format"]["duration"]), 2.0, delta=0.1)
        self.assertAlmostEqual(int(video["nb_read_frames"]), 60, delta=2)
        self.assertLess(abs(float(video["duration"]) - float(audio["duration"])), 0.1)
        samples = {t: frame(output, t) for t in (0.1, 0.5, 0.9, 1.1, 1.5, 1.9)}
        for time, channel in ((0.1, 0), (0.5, 1), (0.9, 0), (1.5, 2)):
            means = samples[time][:300].mean(axis=(0, 1))
            self.assertEqual(int(means.argmax()), channel, f"Wrong clip or overlay at {time}s: {means}")
        white = lambda pixels: int(np.all(pixels[360:] > 210, axis=2).sum())
        self.assertGreater(white(samples[0.5]), 150, "First caption must remain visible on top of the overlay")
        self.assertGreater(white(samples[1.5]), 150, "Second source caption must move to output time")
        for time in (0.1, 0.9, 1.1, 1.9):
            self.assertLess(white(samples[time]), 20, f"Caption leaked outside its cue at {time}s")
        srt = (output.parent / "master.srt").read_text(encoding="utf-8")
        self.assertIn("00:00:00,300 --> 00:00:00,700", srt)
        self.assertIn("00:00:01,300 --> 00:00:01,700", srt)
        for time, expected in ((0.45, 440), (1.45, 880)):
            pcm = run(["ffmpeg", "-v", "error", "-ss", str(time), "-i", output, "-t", "0.2", "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"])
            values = np.frombuffer(pcm, dtype="<f4")
            self.assertGreater(len(values), 8000)
            self.assertGreater(float(np.sqrt(np.mean(values ** 2))), 0.01, "Expected audible source audio")
            peak = np.abs(np.fft.rfft(values * np.hanning(len(values)))).argmax()
            frequency = peak * 48000 / len(values)
            self.assertAlmostEqual(frequency, expected, delta=10, msg=f"Wrong audio source at {time}s")

    def test_forced_frame_rate_and_no_captions(self):
        edl = {"sources": {"red": str(self.sources["red"])}, "ranges": [{"source": "red", "start": 0.2, "end": 1.2}],
               "subtitles": "deliberately-missing.srt", "grade": "none"}
        output, info = self.export("forced", edl, "--fps", "24", "--no-subtitles")
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        self.assertEqual(video["r_frame_rate"], "24/1")
        self.assertEqual(int(video["nb_read_frames"]), 24)
        self.assertFalse((output.parent / "master.srt").exists())
        self.assertLess(int(np.all(frame(output, 0.5)[360:] > 210, axis=2).sum()), 20)

    def test_portrait_and_display_rotation_survive_export(self):
        for source in ("portrait", "rotated"):
            with self.subTest(source=source):
                edl = {"sources": {source: str(self.sources[source])}, "ranges": [{"source": source, "start": 0.2, "end": 1.2}], "grade": "none"}
                output, info = self.export(source, edl, "--no-subtitles")
                video = next(s for s in info["streams"] if s["codec_type"] == "video")
                self.assertEqual((video["width"], video["height"]), (720, 1280))
                self.assertEqual(frame(output, 0.5).shape[:2], (1280, 720))
                self.assertEqual(video["r_frame_rate"], "30/1")


if __name__ == "__main__":
    unittest.main()
