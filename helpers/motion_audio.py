#!/usr/bin/env python3
"""Extract seekable sound controls from arbitrary audio, without tempo assumptions.

Requires FFmpeg and NumPy (already used by video-use). The analysis is descriptive:
it contains no visual layout, beat grid, genre, or scene-selection rules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

DEFAULT_BANDS = [
    ("bass", 30.0, 180.0),
    ("mid", 180.0, 2000.0),
    ("treble", 2000.0, 10000.0),
]


# validate a finite numeric option within its permitted range
def finite_number(value, name: str, minimum=None, maximum=None) -> float:
    number = float(value)
    if (
        not math.isfinite(number)
        or (minimum is not None and number < minimum)
        or (maximum is not None and number > maximum)
    ):
        raise ValueError(
            f"{name} must be finite"
            + (
                f" and between {minimum} and {maximum}"
                if maximum is not None
                else f" and at least {minimum}"
                if minimum is not None
                else ""
            )
        )
    return number


# parse unique named frequency bands from the command line
def parse_bands(value: str) -> list[tuple[str, float, float]]:
    if value.lower() == "none":
        return []
    result = []
    for item in value.split(","):
        parts = item.split(":")
        if len(parts) != 3:
            raise ValueError(
                "Bands must be comma-separated name:lowHz:highHz values, or none"
            )
        result.append(
            (
                parts[0],
                finite_number(parts[1], "band low frequency", 0),
                finite_number(parts[2], "band high frequency", 0),
            )
        )
    return result


# scale a whole signal against one percentile while preserving silence
def normalize(
    values: np.ndarray,
    percentile: float,
    floor: float = 1e-5,
    positive_only: bool = False,
) -> tuple[np.ndarray, float]:
    population = values[values > floor] if positive_only else values
    scale = max(
        float(np.percentile(population, percentile)) if population.size else 0.0, floor
    )
    return np.clip(values / scale, 0, 1), scale


# measure timed loudness spectral bands and onsets from decoded samples
def analyze_samples(
    samples: np.ndarray,
    sample_rate: int,
    *,
    frame_rate: float = 60,
    window_size: int = 2048,
    bands=None,
    offset: float = 0,
    attack: float = 0.025,
    release: float = 0.18,
    percentile: float = 95,
) -> dict:
    """Analyze mono full-scale PCM; frame timestamps are window centers in seconds.

    Offset positions this audio on an external timeline. Edge windows are zero
    padded. Normalization is per feature over this analysis, not per frame, so
    quiet passages remain quiet. Raw energy and scales preserve comparability.
    """
    finite_number(sample_rate, "sample rate", 8000, 96000)
    if int(sample_rate) != sample_rate:
        raise ValueError("Sample rate must be an integer")
    finite_number(frame_rate, "frame rate", 1, 240)
    finite_number(offset, "offset")
    finite_number(attack, "attack", 0.001, 10)
    finite_number(release, "release", 0.001, 10)
    finite_number(percentile, "normalization percentile", 50, 100)
    if (
        not isinstance(window_size, int)
        or window_size < 16
        or window_size > 65536
        or window_size & (window_size - 1)
    ):
        raise ValueError("Window size must be a power of two from 16 through 65536")
    if sample_rate / frame_rate < 1:
        raise ValueError("Frame rate exceeds sample rate")
    chosen_bands = list(DEFAULT_BANDS if bands is None else bands)
    names = set()
    for name, low, high in chosen_bands:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name) or name in names:
            raise ValueError(
                "Band names must be unique identifiers beginning with a letter"
            )
        names.add(name)
        finite_number(low, "band low frequency", 0, sample_rate / 2)
        finite_number(high, "band high frequency", 0, sample_rate / 2)
        if low >= high:
            raise ValueError("Band low frequency must be below its high frequency")
    signal = np.asarray(samples, dtype=np.float64)
    if signal.ndim != 1 or not signal.size or not np.isfinite(signal).all():
        raise ValueError(
            "Audio must contain a nonempty one-dimensional array of finite mono samples"
        )
    duration = signal.size / sample_rate
    count = math.ceil(duration * frame_rate)
    times = np.arange(count, dtype=np.float64) / frame_rate
    rms = np.zeros(count)
    peak = np.zeros(count)
    envelope = np.zeros(count)
    flux = np.zeros(count)
    energies = {name: np.zeros(count) for name, _, _ in chosen_bands}
    window = np.hanning(window_size)
    frequencies = np.fft.rfftfreq(window_size, 1 / sample_rate)
    masks = {
        name: (frequencies >= low) & (frequencies < high)
        for name, low, high in chosen_bands
    }
    for name, mask in masks.items():
        if not mask.any():
            raise ValueError(
                f"Band {name} contains no FFT bins; increase window size or widen the band"
            )
    spectral_scale = window_size * float(np.sum(window * window))
    previous_spectrum = None
    current_envelope = 0.0
    for index, time in enumerate(times):
        center = round(time * sample_rate)
        first = center - window_size // 2
        last = first + window_size
        chunk = np.zeros(window_size)
        lo, hi = max(0, first), min(signal.size, last)
        chunk[lo - first : hi - first] = signal[lo:hi]
        rms[index] = math.sqrt(float(np.mean(chunk * chunk)))
        peak[index] = float(np.max(np.abs(chunk)))
        coefficient = math.exp(
            -1 / (frame_rate * (attack if rms[index] > current_envelope else release))
        )
        current_envelope = (
            coefficient * current_envelope + (1 - coefficient) * rms[index]
        )
        envelope[index] = current_envelope
        spectrum = np.abs(np.fft.rfft(chunk * window))
        power = spectrum * spectrum / spectral_scale
        power[1:-1] *= 2
        for name, mask in masks.items():
            energies[name][index] = math.sqrt(float(np.sum(power[mask])))
        if previous_spectrum is not None:
            flux[index] = math.sqrt(
                float(np.sum(np.maximum(spectrum - previous_spectrum, 0) ** 2))
                / spectral_scale
            )
        previous_spectrum = spectrum
    raw = {
        "rms": rms,
        "peak": peak,
        "envelope": envelope,
        "onset": flux,
        **{f"band:{name}": values for name, values in energies.items()},
    }
    normalized, scales = {}, {}
    for name, values in raw.items():
        normalized[name], scales[name] = normalize(
            values, percentile, positive_only=name == "onset"
        )
    frames = []
    for i, time in enumerate(times):
        frames.append(
            {
                "time": round(float(time + offset), 8),
                **{
                    key: round(float(normalized[key][i]), 7)
                    for key in ("rms", "peak", "envelope", "onset")
                },
                "bands": {
                    name: round(float(normalized[f"band:{name}"][i]), 7)
                    for name, _, _ in chosen_bands
                },
                "raw": {
                    "rms": round(float(rms[i]), 9),
                    "peak": round(float(peak[i]), 9),
                    "bands": {
                        name: round(float(energies[name][i]), 9)
                        for name, _, _ in chosen_bands
                    },
                },
            }
        )
    return {
        "schemaVersion": 1,
        "duration": duration,
        "offset": offset,
        "sampleRate": sample_rate,
        "frameRate": frame_rate,
        "windowSize": window_size,
        "window": "hann",
        "timeReference": "center of zero-padded analysis window",
        "bands": [
            {"name": name, "lowHz": low, "highHz": high}
            for name, low, high in chosen_bands
        ],
        "smoothing": {"attackSeconds": attack, "releaseSeconds": release},
        "normalization": {
            "method": "global percentile per feature clipped to 0..1",
            "percentile": percentile,
            "floor": 1e-5,
            "onsetUsesPositiveValues": True,
            "scales": scales,
        },
        "frames": frames,
    }


# decode a bounded source interval into mono floating point audio
def decode_audio(
    path: Path, sample_rate: int, start: float = 0, duration: float | None = None
) -> np.ndarray:
    """Decode the first audio stream with FFmpeg; start trims input, not timestamps."""
    if not path.is_file():
        raise ValueError(f"Audio input does not exist: {path}")
    finite_number(sample_rate, "sample rate", 8000, 96000)
    finite_number(start, "start", 0)
    if duration is not None:
        finite_number(duration, "duration", 0.000001)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-v",
        "error",
        "-i",
        str(path),
        "-ss",
        str(start),
    ]
    if duration is not None:
        command += ["-t", str(duration)]
    command += [
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True)
    if result.returncode:
        raise ValueError(
            f"FFmpeg could not decode audio: {result.stderr.decode(errors='replace').strip()}"
        )
    if not result.stdout:
        raise ValueError(
            "Decoded audio is empty; check the requested start and duration"
        )
    return np.frombuffer(result.stdout, dtype="<f4")


# analyze the selected audio and write its versioned motion controls
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--sample-rate", type=int, default=24000)
    parser.add_argument("--frame-rate", type=float, default=60)
    parser.add_argument("--window-size", type=int, default=2048)
    parser.add_argument("--bands", default="bass:30:180,mid:180:2000,treble:2000:10000")
    parser.add_argument(
        "--start", type=float, default=0, help="Trim this many seconds from the input"
    )
    parser.add_argument("--duration", type=float, help="Analyze only this many seconds")
    parser.add_argument(
        "--offset",
        type=float,
        default=0,
        help="Place frame timestamps at this external timeline offset",
    )
    parser.add_argument("--attack", type=float, default=0.025)
    parser.add_argument("--release", type=float, default=0.18)
    parser.add_argument("--percentile", type=float, default=95)
    args = parser.parse_args()
    try:
        if args.input.resolve() == args.output.resolve() or (
            args.output.exists()
            and args.input.exists()
            and args.output.samefile(args.input)
        ):
            raise ValueError("Analysis output must not overwrite its source audio")
        samples = decode_audio(args.input, args.sample_rate, args.start, args.duration)
        data = analyze_samples(
            samples,
            args.sample_rate,
            frame_rate=args.frame_rate,
            window_size=args.window_size,
            bands=parse_bands(args.bands),
            offset=args.offset,
            attack=args.attack,
            release=args.release,
            percentile=args.percentile,
        )
        digest = hashlib.sha256()
        with args.input.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        data["source"] = {
            "file": args.input.name,
            "sha256": digest.hexdigest(),
            "inputStartSeconds": args.start,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(data, separators=(",", ":"), allow_nan=False) + "\n"
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(
        f"Analyzed {data['duration']:.3f}s into {len(data['frames'])} frames: {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
