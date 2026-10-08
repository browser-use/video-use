"""Real audio mixes protect sources and preserve the declared timeline."""

import copy
import json
import shutil
import subprocess

import numpy as np
import pytest
from scipy.io import wavfile

from edit_io import last_json, sha256
from mix_audio import build, normalize


# three independently timed source tracks make alignment and mixing measurable
@pytest.fixture
def project(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required")
    t = np.arange(48000 * 4) / 48000
    music = np.column_stack(
        [0.12 * np.cos(2 * np.pi * 220 * t), 0.08 * np.cos(2 * np.pi * 330 * t)]
    ).astype(np.float32)
    voice = np.full((19200, 2), 0.07, np.float32)
    effect = np.full((2400, 2), 0.05, np.float32)
    for name, signal in [("music", music), ("voice", voice), ("effect", effect)]:
        wavfile.write(tmp_path / f"{name}.wav", 48000, signal)
    manifest = {
        "fps": 30,
        "total_frames": 120,
        "sources": {
            name: {"file": f"{name}.wav", "provenance": "generated test signal"}
            for name in ["music", "voice", "effect"]
        },
        "audio": [
            {
                "id": "music",
                "source": "music",
                "role": "music",
                "source_start_sample": 0,
                "start_sample": 0,
                "sample_count": 192000,
            },
            {
                "id": "voice",
                "source": "voice",
                "role": "voice",
                "source_start_sample": 0,
                "start_sample": 48000,
                "sample_count": 19200,
            },
            {
                "id": "effect",
                "source": "effect",
                "role": "effects",
                "source_start_sample": 0,
                "start_sample": 60000,
                "sample_count": 2400,
            },
        ],
        "delivery": {"lufs": -14, "true_peak": -1.5},
    }
    return tmp_path, manifest, music


# complete mixing preserves independent stems and output duration through normalization
def test_complete_mix_preserves_alignment_and_normalizes(project):
    root, manifest, music = project
    before = {
        name: sha256(root / f"{name}.wav") for name in ("music", "voice", "effect")
    }
    output = build(manifest, root, root / "mix")
    stems = {}
    for name in ["music", "voice", "effects", "mix", "master"]:
        rate, signal = wavfile.read(root / "mix" / f"{name}.wav")
        assert rate == 48000 and signal.shape == (192000, 2)
        assert np.isfinite(signal).all()
        stems[name] = signal
    np.testing.assert_array_equal(stems["music"], music)
    assert not stems["voice"][:48000].any() and not stems["voice"][67200:].any()
    assert stems["voice"][48000, 0] == 0 and stems["voice"][49440, 0] == pytest.approx(
        0.07
    )
    assert np.flatnonzero(stems["effects"][:, 0])[[0, -1]].tolist() == [60000, 62399]
    np.testing.assert_allclose(
        stems["mix"], stems["music"] + stems["voice"] + stems["effects"], atol=1e-7
    )
    report = json.loads((root / "mix/mix_report.json").read_text())
    assert report["samples"] == 192000 and report["sample_rate"] == 48000
    assert all(
        sha256(root / f"{name}.wav") == digest for name, digest in before.items()
    )
    measured = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(output),
            "-af",
            "loudnorm=print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    loudness = last_json(measured.stderr.decode())
    assert float(loudness["input_i"]) == pytest.approx(-14, abs=0.3)
    assert float(loudness["input_tp"]) <= -1.3


# explicit picture frame rates determine duration rather than an implicit thirty fps clock
def test_declared_frame_rate_controls_mix_duration(project):
    root, manifest, _ = project
    manifest["fps"] = 24
    manifest["total_frames"] = 96
    build(manifest, root, root / "at24")
    _, signal = wavfile.read(root / "at24/master.wav")
    assert signal.shape == (192000, 2)


# invalid placements fail before decoding or creating the output directory
@pytest.mark.parametrize(
    "change",
    [
        {"start_sample": -1},
        {"start_sample": 0.5},
        {"sample_count": 0},
        {"start_sample": 190000},
        {"source_start_sample": -1},
        {"role": "unknown"},
    ],
)
def test_invalid_placement_rejected_before_outputs(project, change):
    root, manifest, _ = project
    manifest["audio"][1].update(change)
    with pytest.raises(ValueError):
        build(manifest, root, root / "invalid")
    assert not (root / "invalid").exists()


# existing files and source aliases are never overwritten by named mix outputs
@pytest.mark.parametrize("alias", [False, True])
def test_existing_outputs_preserved(project, alias):
    root, manifest, _ = project
    dest = root / "protected"
    dest.mkdir()
    path = dest / "mix.wav"
    if alias:
        path.hardlink_to(root / "music.wav")
    else:
        path.write_bytes(b"existing output")
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        build(manifest, root, dest)
    assert path.read_bytes() == before
    assert not (dest / "master.wav").exists()


# a rerun needs a fresh destination so a reviewed mix cannot be silently replaced
def test_second_build_requires_fresh_output(project):
    root, manifest, _ = project
    build(manifest, root, root / "mix")
    digest = sha256(root / "mix/master.wav")
    with pytest.raises(FileExistsError):
        build(manifest, root, root / "mix")
    assert sha256(root / "mix/master.wav") == digest


# silent material cannot produce a meaningful loudness normalization result
def test_silent_normalization_fails(project):
    root, _, _ = project
    source = root / "silence.wav"
    wavfile.write(source, 48000, np.zeros((192000, 2), np.float32))
    with pytest.raises(ValueError, match="silent"):
        normalize(source, root / "normalized.wav")
    assert not (root / "normalized.wav").exists()
