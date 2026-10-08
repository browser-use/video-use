"""test music bed support for video use"""

import wave
from pathlib import Path

import numpy as np

from helpers import music_bed


# test synthesize is loop length and normalized
def test_synthesize_is_loop_length_and_normalized() -> None:
    samples, loop_s = music_bed.synthesize(
        bpm=92,
        key=57,
        progression=[0, -4, -9, -2],
        bars_per_chord=2,
        seed=0,
        peak_dbfs=-12.0,
    )
    assert abs(loop_s - (60 / 92) * 4 * 2 * 4) <= 1 / music_bed.SR
    assert len(samples) == round(loop_s * music_bed.SR)
    peak = float(np.max(np.abs(samples)))
    assert abs(20 * np.log10(peak) + 12.0) < 0.5
    assert np.isfinite(samples).all()


# test write wav round trip
def test_write_wav_round_trip(tmp_path: Path) -> None:
    samples, loop_s = music_bed.synthesize(
        bpm=120,
        key=60,
        progression=[0, 5, -3, 7],
        bars_per_chord=1,
        seed=1,
        peak_dbfs=-18.0,
    )
    out = tmp_path / "bed.wav"
    music_bed.write_wav(samples, out)
    with wave.open(str(out)) as handle:
        assert handle.getnchannels() == 1
        assert handle.getframerate() == music_bed.SR
        assert abs(handle.getnframes() / music_bed.SR - loop_s) < 0.01


# seeded generation repeats exactly and ends at zero for the loop join
def test_seeded_loop_is_repeatable_and_has_clean_edges():
    options = dict(
        bpm=159.7,
        key=57,
        progression=[0, -4, -9, -2],
        bars_per_chord=1,
        seed=3,
        peak_dbfs=-12,
    )
    a, duration = music_bed.synthesize(**options)
    b, _ = music_bed.synthesize(**options)
    assert np.array_equal(a, b)
    assert duration == len(a) / music_bed.SR
    assert a[0] == a[-1] == 0
    assert abs(float(a[1])) < 0.001 and abs(float(a[-2])) < 0.001
    c, _ = music_bed.synthesize(**dict(options, seed=4))
    assert not np.array_equal(a, c)
