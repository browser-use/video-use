"""Independent signals exercise song measurements and source preservation."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from song_scan import accents, scan


# broadband attacks should be measured near the pulse without inventing an instrument
def test_transient_candidates_have_time_and_frequency_band():
    rate = 22050
    samples = np.zeros(rate * 2)
    pulse = np.random.default_rng(31).normal(size=300) * np.linspace(1, 0, 300)
    samples[rate : rate + len(pulse)] = pulse
    events = accents(samples, rate)
    assert any(abs(event["time"] - 1) < 0.05 for event in events)
    assert all(
        event["kind"] in {"low_attack", "mid_attack", "high_attack"} for event in events
    )
    assert all(np.isfinite(event["strength"]) for event in events)
    assert events == sorted(events, key=lambda event: event["time"])


# silence has no detected accents or spurious normalized energy
def test_silent_recording_has_no_events(tmp_path):
    path = tmp_path / "silence.wav"
    wavfile.write(path, 22050, np.zeros(22050 * 3, dtype=np.float32))
    result = scan(path, include_accents=True)
    assert result["duration"] == 3
    assert result["tempo"] == 0
    assert result["beats"] == [] and result["accents"] == []
    assert result["rms_per_second"] == [0, 0, 0]
    assert result["drop_candidates"] == []
    json.dumps(result, allow_nan=False)


# a known pulse train yields plausible beat spacing while preserving input bytes
def test_regular_pulses_yield_beat_candidates(tmp_path):
    rate = 22050
    signal = np.zeros(rate * 8, dtype=np.float32)
    pulse = np.random.default_rng(9).normal(0, 0.2, 600) * np.linspace(1, 0, 600)
    for start in np.arange(0.5, 7.6, 0.5):
        index = round(start * rate)
        signal[index : index + 600] += pulse
    path = tmp_path / "pulses.wav"
    wavfile.write(path, rate, signal)
    before = path.read_bytes()
    result = scan(path, include_accents=True)
    assert 110 <= result["tempo"] <= 130
    assert 0.45 <= result["beat_interval"] <= 0.55
    assert len(result["beats"]) >= 8
    assert path.read_bytes() == before
    assert "audition" in result["interpretation"]
    json.dumps(result, allow_nan=False)


# explicit output aliases must be rejected before loading the recording
@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_cli_cannot_replace_recording(tmp_path, alias):
    source = tmp_path / "recording.mp3"
    source.write_bytes(b"original source bytes")
    output = source if alias == "same" else tmp_path / "alias.mp3"
    if alias == "symlink":
        output.symlink_to(source)
    elif alias == "hardlink":
        output.hardlink_to(source)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "helpers/song_scan.py"),
            str(source),
            "--out",
            str(output),
        ],
        capture_output=True,
    )
    assert result.returncode != 0 and b"cannot replace" in result.stderr
    assert source.read_bytes() == b"original source bytes"
