"""Measure beat and frequency-band accent candidates without modifying recordings."""

import argparse
from pathlib import Path
import numpy as np
from scipy.signal import find_peaks, stft
from edit_io import save_json


# measure rising spectral energy without labeling instruments
def accents(samples, rate, hop=128):
    frequencies, times, complex_spectrum = stft(
        samples, fs=rate, nperseg=1024, noverlap=1024 - hop, boundary="zeros"
    )
    spectrum = np.abs(complex_spectrum)
    output = []
    for name, low, high in [
        ("low_attack", 30, 180),
        ("mid_attack", 180, 2500),
        ("high_attack", 2500, min(12000, rate / 2)),
    ]:
        band = spectrum[(frequencies >= low) & (frequencies < high)]
        if not len(band):
            continue
        flux = np.maximum(0, np.diff(band, axis=1, prepend=band[:, :1])).mean(0)
        positive = flux[flux > 1e-12]
        scale = np.percentile(positive, 95) if len(positive) else 0
        if scale <= 1e-9:
            continue
        score = flux / scale
        peaks, _ = find_peaks(
            score, height=0.4, prominence=0.2, distance=max(1, int(0.06 * rate / hop))
        )
        output.extend(
            {"time": float(times[i]), "kind": name, "strength": float(score[i])}
            for i in peaks
        )
    return sorted(output, key=lambda r: r["time"])


# summarize beat timing and loudness candidates from the recording
def scan(path, include_accents=False):
    import os, tempfile

    if not os.environ.get("NUMBA_CACHE_DIR"):
        os.environ["NUMBA_CACHE_DIR"] = tempfile.mkdtemp(prefix="video-use-song-scan-")
    import librosa

    samples, rate = librosa.load(path, sr=22050, mono=True)
    if not len(samples):
        raise ValueError("empty recording")
    tempo, beats = librosa.beat.beat_track(y=samples, sr=rate)
    bt = librosa.frames_to_time(beats, sr=rate)
    rms = [
        float(np.sqrt(np.mean(samples[i : i + rate].astype(float) ** 2)))
        for i in range(0, len(samples), rate)
    ]
    rises = [
        rms[i] - float(np.mean(rms[max(0, i - 4) : i])) if i else 0
        for i in range(len(rms))
    ]
    result = {
        "file": str(Path(path).resolve()),
        "duration": len(samples) / rate,
        "tempo": float(np.atleast_1d(tempo)[0]),
        "beats": bt.tolist(),
        "beat_interval": float(np.median(np.diff(bt))) if len(bt) > 1 else None,
        "rms_per_second": rms,
        "drop_candidates": [
            {"time": i, "rise": rises[i]}
            for i in sorted(range(len(rises)), key=lambda i: rises[i], reverse=True)[:6]
            if rises[i] > 0
        ],
        "interpretation": "Measured candidates require audition and editorial selection; frequency bands do not prove kick or snare identity",
    }
    if include_accents:
        result["accents"] = accents(samples, rate)
    return result


# write the analysis report without overwriting its source
def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("song")
    p.add_argument("--accents", action="store_true")
    p.add_argument("-o", "--out")
    a = p.parse_args()
    source = Path(a.song)
    out = Path(a.out) if a.out else source.with_name(source.stem + "_scan.json")
    if out.resolve() == source.resolve() or (out.exists() and out.samefile(source)):
        p.error("scan output cannot replace recording")
    save_json(out, scan(source, a.accents))


if __name__ == "__main__":
    main()
