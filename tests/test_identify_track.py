"""Recording correspondence checks with known offsets and supplied candidates."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from identify_track import align, decode, rank


# mean subtraction and energy normalization should preserve a known excerpt offset
def test_alignment_recovers_offset_despite_gain():
    candidate = np.random.default_rng(12).normal(size=6000)
    query = candidate[137:1837] * 0.3 + 0.1
    result = align(query, candidate, 8000)
    assert result["offset_samples"] == 137
    assert result["offset_seconds"] == pytest.approx(137 / 8000)
    assert result["correlation"] > 0.999


# unusable query windows must not yield a confident recording match
@pytest.mark.parametrize(
    "query,candidate",
    [
        (np.zeros(100), np.ones(200)),
        (np.ones(10), np.ones(200)),
        (np.ones(200), np.ones(100)),
        (np.full(100, np.nan), np.ones(200)),
        (np.arange(100), np.full(200, np.inf)),
    ],
)
def test_alignment_rejects_invalid_input(query, candidate):
    with pytest.raises(ValueError):
        align(query, candidate)


# independent supplied recordings allow end to end matching without an online service
@pytest.fixture
def recordings(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required")
    samples = np.random.default_rng(42).normal(0, 0.08, 24000).astype(np.float32)
    unrelated = np.random.default_rng(8).normal(0, 0.08, 24000).astype(np.float32)
    wavfile.write(tmp_path / "match.wav", 8000, samples)
    wavfile.write(tmp_path / "other.wav", 8000, unrelated)
    wavfile.write(tmp_path / "query.wav", 8000, samples[3200:9600] * 0.4)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            [
                {"id": "other", "file": "other.wav"},
                {"id": "match", "file": "match.wav", "title": "Known test recording"},
            ]
        )
    )
    return tmp_path, catalog


# ranking should find the supplied recording and its actual sample offset
def test_rank_finds_matching_supplied_recording(recordings):
    root, catalog = recordings
    result = rank(root / "query.wav", catalog)
    best = result["candidates"][0]
    assert best["id"] == "match" and best["title"] == "Known test recording"
    assert best["offset_samples"] == 3200
    assert best["correlation"] > 0.999
    assert result["candidates"][1]["correlation"] < 0.2
    assert "not global song recognition" in result["limit"]
    assert len(decode(root / "match.wav", limit=0.1)) == 800


# output aliases must not destroy any file used as evidence for a match
@pytest.mark.parametrize("protected", ["query.wav", "catalog.json", "match.wav"])
@pytest.mark.parametrize("alias", [False, True])
def test_rank_cli_preserves_inputs(recordings, protected, alias):
    root, catalog = recordings
    original = root / protected
    before = original.read_bytes()
    output = root / "alias.json" if alias else original
    if alias:
        output.hardlink_to(original)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "helpers/identify_track.py"),
            str(root / "query.wav"),
            str(catalog),
            "--out",
            str(output),
        ],
        capture_output=True,
    )
    assert result.returncode != 0
    assert original.read_bytes() == before
