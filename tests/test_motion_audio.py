"""Signal-level checks for content-independent audio analysis."""

import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave

import numpy as np

from helpers.motion_audio import analyze_samples, decode_audio, parse_bands


# exercise measured audio controls with independent synthetic signals
class MotionAudioTests(unittest.TestCase):
    rate = 24000

    # generate a known frequency for independent spectrum checks
    def tone(self, frequency, seconds=1, amplitude=0.4):
        return amplitude * np.sin(
            2 * np.pi * frequency * np.arange(round(seconds * self.rate)) / self.rate
        )

    # verify silence is finite and stays zero
    def test_silence_is_finite_and_stays_zero(self):
        data = analyze_samples(np.zeros(self.rate), self.rate)
        self.assertEqual(len(data["frames"]), 60)
        for frame in data["frames"]:
            for key in ("rms", "peak", "envelope", "onset"):
                self.assertEqual(frame[key], 0)
            self.assertTrue(all(value == 0 for value in frame["bands"].values()))
        json.dumps(data, allow_nan=False)

    # verify known frequencies land in distinct bands
    def test_known_frequencies_land_in_distinct_bands(self):
        for frequency, expected in ((90, "bass"), (700, "mid"), (6000, "treble")):
            with self.subTest(frequency=frequency):
                data = analyze_samples(self.tone(frequency), self.rate)
                frame = data["frames"][30]
                self.assertAlmostEqual(
                    frame["raw"]["rms"], 0.4 / math.sqrt(2), delta=0.005
                )
                energies = frame["raw"]["bands"]
                self.assertEqual(max(energies, key=energies.get), expected)
                self.assertGreater(
                    energies[expected],
                    max(value for key, value in energies.items() if key != expected)
                    * 100,
                )

    # verify offsets and fractional frame rate do not drift
    def test_offsets_and_fractional_frame_rate_do_not_drift(self):
        data = analyze_samples(
            self.tone(300, 2.2), self.rate, frame_rate=29.97, offset=-0.75
        )
        self.assertAlmostEqual(data["frames"][0]["time"], -0.75)
        self.assertAlmostEqual(
            data["frames"][-1]["time"],
            (len(data["frames"]) - 1) / 29.97 - 0.75,
            places=7,
        )

    # verify pulses trigger onsets without bpm grid
    def test_pulses_trigger_onsets_without_bpm_grid(self):
        signal = np.zeros(self.rate * 2)
        moments = [0.23, 0.79, 1.37]
        for moment in moments:
            start = round(moment * self.rate)
            pulse = self.tone(3200, 0.025) * np.exp(-np.arange(600) / 180)
            signal[start : start + len(pulse)] += pulse
        data = analyze_samples(signal, self.rate)
        for moment in moments:
            nearby = [f for f in data["frames"] if abs(f["time"] - moment) < 0.06]
            self.assertGreater(max(f["onset"] for f in nearby), 0.5)
        quiet = [f for f in data["frames"] if 0.4 < f["time"] < 0.6]
        self.assertLess(max(f["onset"] for f in quiet), 0.001)

    # verify quiet passages are not normalized per frame
    def test_quiet_passages_are_not_normalized_per_frame(self):
        signal = np.concatenate((self.tone(200, 1, 0.8), self.tone(200, 1, 0.08)))
        frames = analyze_samples(signal, self.rate)["frames"]
        self.assertLess(frames[90]["rms"], frames[30]["rms"] * 0.12)

    # verify custom and no bands
    def test_custom_and_no_bands(self):
        bands = parse_bands("voice:250:1200,air:7000:11000")
        data = analyze_samples(self.tone(700), self.rate, bands=bands)
        self.assertEqual(list(data["frames"][20]["bands"]), ["voice", "air"])
        self.assertEqual(
            analyze_samples(self.tone(700), self.rate, bands=parse_bands("none"))[
                "frames"
            ][0]["bands"],
            {},
        )

    # verify invalid inputs
    def test_invalid_inputs(self):
        for samples in ([], [[1, 2]], [float("nan")], [float("inf")]):
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                analyze_samples(samples, self.rate)
        for kwargs in (
            {"frame_rate": 0},
            {"frame_rate": float("nan")},
            {"offset": float("inf")},
            {"window_size": 1000},
            {"attack": 0},
            {"percentile": 101},
            {"bands": [("bad", 100, 50)]},
            {"bands": [("x", 1, 300), ("x", 300, 800)]},
            {"bands": [("tiny", 101, 102)]},
            {"bands": [("high", 100, 13000)]},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                analyze_samples(self.tone(100), self.rate, **kwargs)

    # verify ffmpeg decode trim and empty input
    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for decoding")
    def test_ffmpeg_decode_trim_and_empty_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "different tones.wav"
            signal = np.concatenate((self.tone(90), self.tone(4000)))
            with wave.open(str(path), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(self.rate)
                stream.writeframes((signal * 32767).astype("<i2").tobytes())
            decoded = decode_audio(path, self.rate, start=1, duration=0.5)
            self.assertEqual(decoded.size, self.rate // 2)
            frame = analyze_samples(decoded, self.rate)["frames"][15]
            self.assertGreater(frame["raw"]["bands"]["treble"], 0.2)
            with self.assertRaises(ValueError):
                decode_audio(path, self.rate, start=3)
            with self.assertRaises(ValueError):
                decode_audio(Path(directory) / "missing.wav", self.rate)
            original = path.read_bytes()
            overwrite = subprocess.run(
                [
                    sys.executable,
                    str(
                        Path(__file__).resolve().parents[1] / "helpers/motion_audio.py"
                    ),
                    str(path),
                    "-o",
                    str(path),
                ],
                capture_output=True,
            )
            self.assertNotEqual(overwrite.returncode, 0)
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()


# output aliases must preserve the actual recording rather than replace it with JSON
def test_cli_preserves_hardlinked_recording(tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"source bytes must remain unchanged")
    output = tmp_path / "alias.json"
    output.hardlink_to(source)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "helpers/motion_audio.py"),
            str(source),
            "-o",
            str(output),
        ],
        capture_output=True,
    )
    assert result.returncode != 0
    assert b"must not overwrite" in result.stderr
    assert source.read_bytes() == b"source bytes must remain unchanged"
