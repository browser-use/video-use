"""Check invalid settings encoded sample properties and output preservation."""

import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from helpers import music_bed


# invalid settings fail before synthesis allocates an audio buffer
@pytest.mark.parametrize(
    "option,value",
    [
        ("bpm", 0),
        ("bpm", float("nan")),
        ("bpm", float("inf")),
        ("bars_per_chord", 0),
        ("bars_per_chord", 9),
        ("bars_per_chord", True),
        ("seed", -1),
        ("seed", 2**64),
        ("key", 127),
        ("key", -1),
        ("progression", [0, 1]),
        ("progression", [0, 0, 0, 200]),
        ("peak_dbfs", 1),
        ("peak_dbfs", float("nan")),
        ("peak_dbfs", -61),
    ],
)
def test_invalid_settings(option, value):
    options = dict(
        bpm=120,
        key=57,
        progression=[0, -4, -9, -2],
        bars_per_chord=1,
        seed=0,
        peak_dbfs=-12,
    )
    options[option] = value
    with pytest.raises(ValueError):
        music_bed.synthesize(**options)


# wav samples use signed little endian pcm without silent clipping
def test_pcm_encoding(tmp_path):
    out = tmp_path / "bed.wav"
    samples = np.array([-1, -0.5, 0, 0.5, 1], dtype=np.float32)
    music_bed.write_wav(samples, out)
    with wave.open(str(out)) as handle:
        assert (
            handle.getnchannels(),
            handle.getsampwidth(),
            handle.getframerate(),
        ) == (1, 2, 48000)
        encoded = np.frombuffer(handle.readframes(5), dtype="<i2")
    assert encoded.tolist() == [-32767, -16383, 0, 16383, 32767]


# existing files and dangling symbolic links are never replaced
@pytest.mark.parametrize("symlink", [False, True])
def test_existing_output_protected(tmp_path, symlink):
    out = tmp_path / "bed.wav"
    if symlink:
        out.symlink_to(tmp_path / "missing")
    else:
        out.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        music_bed.write_wav(np.zeros(10), out)
    assert out.is_symlink() if symlink else out.read_bytes() == b"keep"
    assert not (tmp_path / "missing").exists()


# invalid audio cannot create a partial output
@pytest.mark.parametrize(
    "samples",
    [np.array([]), np.array([float("nan")]), np.array([1.1]), np.zeros((2, 2))],
)
def test_invalid_samples_rejected(tmp_path, samples):
    with pytest.raises(ValueError):
        music_bed.write_wav(samples, tmp_path / "bad.wav")
    assert list(tmp_path.iterdir()) == []


# a writer racing publication keeps its file and temporary output is cleaned up
def test_publication_race(monkeypatch, tmp_path):
    out = tmp_path / "bed.wav"

    # simulate an exclusive publication collision
    def collision(source, target):
        target.write_bytes(b"other writer")
        raise FileExistsError("destination appeared")

    monkeypatch.setattr(music_bed.os, "link", collision)
    with pytest.raises(FileExistsError):
        music_bed.write_wav(np.zeros(10), out)
    assert out.read_bytes() == b"other writer"
    assert list(tmp_path.iterdir()) == [out]


# the public command produces the declared number of frames and rejects reused names
def test_cli_delivery(tmp_path):
    out = tmp_path / "bed.wav"
    helper = Path(music_bed.__file__)
    command = [
        sys.executable,
        str(helper),
        "-o",
        str(out),
        "--bpm",
        "160",
        "--bars-per-chord",
        "1",
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    with wave.open(str(out)) as handle:
        assert handle.getnframes() == 6 * 48000
        samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    peak = float(np.max(np.abs(samples.astype(np.int32)))) / 32767
    assert 20 * np.log10(peak) == pytest.approx(-12, abs=0.01)
    assert samples[0] == samples[-1] == 0
    before = out.read_bytes()
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0 and "already exists" in result.stderr
    assert out.read_bytes() == before


# complex samples must not silently lose their imaginary component
def test_review_complex_samples(tmp_path):
    with pytest.raises(ValueError):
        music_bed.write_wav(np.array([0.1 + 0.2j]), tmp_path / 'out.wav')
    assert not (tmp_path / 'out.wav').exists()


# a progression cannot compensate for an invalid base midi key
def test_review_invalid_midi_key():
    with pytest.raises(ValueError):
        music_bed.validate_settings(90, -1, [2, 3, 4, 5], 1, 0, -12)
