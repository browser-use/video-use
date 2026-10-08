"""Sample accuracy and gain behavior for independently placed audio clips."""

import shutil

import numpy as np
import pytest
from scipy.io import wavfile

from mix_audio import apply_clip, decode_window, filter_chain, gain_envelope


# known stereo samples provide an exact target for decoder trimming
@pytest.fixture
def stereo(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required")
    audio = np.random.default_rng(12).normal(0, 0.05, (48000, 2)).astype(np.float32)
    path = tmp_path / "stereo.wav"
    wavfile.write(path, 48000, audio)
    return path, audio


# trimmed float PCM preserves every selected sample on both channels
def test_decode_selects_exact_samples(stereo):
    path, audio = stereo
    np.testing.assert_array_equal(decode_window(path, 177, 12000), audio[177:12177])
    with pytest.raises(ValueError, match="needed"):
        decode_window(path, 47000, 2000)


# invalid sample boundaries fail before starting the media decoder
@pytest.mark.parametrize("start,count", [(-1, 100), (0, 0), (1.5, 10), (True, 10)])
def test_invalid_decode_window(stereo, start, count):
    with pytest.raises(ValueError, match="window"):
        decode_window(stereo[0], start, count)


# EQ can alter spectrum but may not stretch or shrink the decoded interval
def test_filter_preserves_count_and_reduces_low_frequency(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required")
    signal = 0.2 * np.sin(2 * np.pi * 80 * np.arange(48000) / 48000)
    path = tmp_path / "bass.wav"
    wavfile.write(path, 48000, np.column_stack([signal, signal]).astype(np.float32))
    raw = decode_window(path, 0, 48000)
    filtered = decode_window(
        path, 0, 48000, [{"type": "highpass", "frequency_hz": 1000}]
    )
    assert filtered.shape == raw.shape == (48000, 2)
    assert (
        np.sqrt(np.mean(filtered[5000:] ** 2))
        < np.sqrt(np.mean(raw[5000:] ** 2)) * 0.02
    )


# rate changing filters and invalid EQ values cannot enter a sample addressed mix
@pytest.mark.parametrize(
    "row",
    [
        {"type": "atempo", "frequency_hz": 100},
        {"type": "highpass", "frequency_hz": 100, "gain_db": 5},
        {"type": "highpass", "frequency_hz": 24000},
        {"type": "highpass", "frequency_hz": float("nan")},
        {"type": "equalizer", "frequency_hz": 100, "q": 0},
        {"type": "equalizer", "frequency_hz": 100, "gain_db": 40},
        {"type": "highpass", "frequency_hz": 100, "unknown": 1},
    ],
)
def test_unsupported_filters_fail(row):
    with pytest.raises(ValueError):
        filter_chain([row])


# logarithmic gain interpolation produces the declared amplitude at each sample
def test_gain_envelope_interpolates_decibels():
    gain = gain_envelope(5, [[0, -20], [4, 0]])
    np.testing.assert_allclose(
        gain, 10 ** (np.array([-20, -15, -10, -5, 0]) / 20), rtol=1e-6
    )
    assert gain_envelope(2, [], -20).tolist() == pytest.approx([0.1, 0.1])


# malformed gain points cannot silently reorder the requested automation
@pytest.mark.parametrize(
    "points", [[[1, 0]], [[0, 0], [0, 1]], [[0, 0], [6, 1]], [[0, float("nan")]]]
)
def test_invalid_gain_points(points):
    with pytest.raises(ValueError):
        gain_envelope(5, points)


# speech gets short edge fades while continuous music keeps its full edge amplitude
def test_voice_fades_do_not_apply_to_music():
    audio = np.ones((4800, 2), dtype=np.float32)
    voice = apply_clip(audio, {"role": "voice"})
    music = apply_clip(audio, {"role": "music"})
    assert voice[0, 0] == voice[-1, 0] == 0
    assert voice[1440, 0] == 1
    np.testing.assert_array_equal(music, audio)
    assert voice.shape == music.shape == audio.shape


# malformed envelope rows fail with actionable errors instead of indexing crashes
@pytest.mark.parametrize('points', [[[]], [[0]], [None], [1]])
def test_review_malformed_gain_points(points):
    from mix_audio import gain_envelope
    with pytest.raises(ValueError, match='pairs'):
        gain_envelope(100, points)
