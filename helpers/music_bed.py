#!/usr/bin/env python3
"""Synthesize a quiet, loopable ambient music bed without external audio samples.

This helper produces a detuned pad over a four-chord loop, a soft bass pulse
and a faint noise tick. It returns a mono 48 kHz WAV for a separate mixer or
editor to place and level. No API calls or external audio samples are used.

Usage:
    python helpers/music_bed.py -o edit/assets/bed.wav
    python helpers/music_bed.py -o edit/assets/bed.wav --bpm 84 --key 55 --seed 3
    python helpers/music_bed.py -o edit/assets/bed.wav --progression "0,-4,-9,-2"

``--key`` is the MIDI note of the loop's root (57 = A3). ``--progression`` lists
root offsets in semitones for the four chords; chord shapes alternate minor/major
so the loop stays unresolved and unobtrusive.
"""

from __future__ import annotations

import argparse
import math
import os
import tempfile
import wave
from pathlib import Path

import numpy as np

SR = 48000


# convert a midi note number to a frequency in hertz
def midi_hz(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


# render a warm detuned pad chord for the given notes over the time axis
def pad(t: np.ndarray, notes: list[int], rng: np.random.Generator) -> np.ndarray:
    out = np.zeros_like(t)
    for n in notes:
        f = midi_hz(n)
        phase = rng.uniform(0, 2 * math.pi)
        for detune, gain in ((-0.35, 0.5), (0.0, 0.7), (0.35, 0.5)):
            fd = f * 2 ** (detune / 1200)
            out += gain * np.sin(
                2 * np.pi * fd * t + phase + 0.3 * np.sin(2 * np.pi * 0.11 * t)
            )
        out += 0.25 * np.sin(2 * np.pi * f * 0.5 * t)
    return out / (len(notes) * 2.5)


# validate synthesis settings before allocating audio buffers
def validate_settings(bpm, key, progression, bars_per_chord, seed, peak_dbfs):
    if not math.isfinite(bpm) or not 50 <= bpm <= 160:
        raise ValueError("bpm must be between 50 and 160")
    if (
        isinstance(bars_per_chord, bool)
        or not isinstance(bars_per_chord, int)
        or not 1 <= bars_per_chord <= 8
    ):
        raise ValueError("bars per chord must be an integer between 1 and 8")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**64:
        raise ValueError(
            "seed must be an integer between zero and 2 to the power 64 minus one"
        )
    if not math.isfinite(peak_dbfs) or not -60 <= peak_dbfs <= 0:
        raise ValueError("peak dbfs must be between minus 60 and zero")
    if isinstance(key, bool) or not isinstance(key, int) or not 0 <= key <= 127:
        raise ValueError("key must be an integer MIDI note from 0 through 127")
    if len(progression) != 4 or any(
        isinstance(v, bool) or not isinstance(v, int) for v in progression
    ):
        raise ValueError("progression needs exactly four integer semitone offsets")
    if any(not 0 <= key + offset <= 115 for offset in progression):
        raise ValueError("all chord roots must fit MIDI notes 0 through 115")


# synthesize a seeded chord loop and return samples with its encoded duration
def synthesize(
    *,
    bpm: float,
    key: int,
    progression: list[int],
    bars_per_chord: int,
    seed: int,
    peak_dbfs: float,
) -> tuple[np.ndarray, float]:
    validate_settings(bpm, key, progression, bars_per_chord, seed, peak_dbfs)
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    bar = 4 * beat
    loop_s = bar * bars_per_chord * len(progression)
    n = round(loop_s * SR)
    t = np.arange(n) / SR
    mix = np.zeros(n, dtype=np.float64)
    xf = int(0.35 * SR)
    for i, offset in enumerate(progression):
        root = key + offset
        third = 3 if i % 2 == 0 else 4  # minor, major, minor, major
        chord = [root, root + third, root + 7, root + 12]
        start = round(i * bar * bars_per_chord * SR)
        end = round((i + 1) * bar * bars_per_chord * SR)
        local = np.arange(end - start) / SR
        voice = pad(local, chord, rng)
        env = np.ones_like(local)
        env[:xf] = np.linspace(0, 1, xf)
        env[-xf:] = np.linspace(1, 0, xf)
        mix[start:end] += voice * env
    mix *= 0.85 + 0.15 * np.sin(2 * np.pi * (1 / (2 * bar)) * t)
    beats = 4 * bars_per_chord * len(progression)
    for b in range(beats):
        s = round(b * beat * SR)
        if b % 2 == 0:
            length = min(round(0.35 * SR), n - s)
            k = np.arange(length) / SR
            mix[s : s + length] += (
                0.35
                * np.sin(2 * np.pi * (55 * np.exp(-k * 6) + 40) * k)
                * np.exp(-k * 9)
            )
        else:
            length = min(round(0.03 * SR), n - s)
            tick = rng.standard_normal(length) * np.exp(-np.arange(length) / SR * 220)
            mix[s : s + length] += 0.03 * tick
    mix = np.convolve(mix, np.ones(24) / 24, mode="same")
    # remove the wrap discontinuity with short edge fades before peak normalization
    fade = round(0.005 * SR)
    mix[:fade] *= np.linspace(0, 1, fade)
    mix[-fade:] *= np.linspace(1, 0, fade)
    mix /= float(np.max(np.abs(mix))) or 1.0
    mix *= 10 ** (peak_dbfs / 20)
    return mix.astype(np.float32), n / SR


# write float samples to a sixteen bit wav file
def write_wav(samples: np.ndarray, out: Path) -> None:
    if out.exists() or out.is_symlink():
        raise FileExistsError("output already exists use a new WAV filename")
    if out.suffix.lower() != ".wav":
        raise ValueError("output must have a wav extension")
    samples = np.asarray(samples)
    if np.iscomplexobj(samples) or samples.ndim != 1 or not samples.size or not np.isfinite(samples).all():
        raise ValueError("samples must be a nonempty finite mono array")
    if np.max(np.abs(samples)) > 1:
        raise ValueError("samples exceed the PCM amplitude range")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=out.parent, suffix=".wav", delete=False
    ) as staged:
        temporary = Path(staged.name)
    try:
        with wave.open(str(temporary), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SR)
            handle.writeframes((samples * 32767).astype("<i2").tobytes())
        # link only to a new name so another writer cannot be overwritten
        os.link(temporary, out)
    finally:
        temporary.unlink(missing_ok=True)


# command line entry point that renders the bed to the requested path
def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--bpm", type=float, default=92)
    parser.add_argument("--key", type=int, default=57, help="MIDI root note (57 = A3)")
    parser.add_argument(
        "--progression", default="0,-4,-9,-2", help="four root offsets in semitones"
    )
    parser.add_argument("--bars-per-chord", type=int, default=2)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--peak-dbfs", type=float, default=-12.0)
    args = parser.parse_args()
    try:
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError("output already exists use a new WAV filename")
        if args.output.suffix.lower() != ".wav":
            raise ValueError("output must have a wav extension")
        progression = [int(v) for v in args.progression.split(",")]
        samples, loop_s = synthesize(
            bpm=args.bpm,
            key=args.key,
            progression=progression,
            bars_per_chord=args.bars_per_chord,
            seed=args.seed,
            peak_dbfs=args.peak_dbfs,
        )
        write_wav(samples, args.output)
    except (ValueError, FileExistsError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"bed → {args.output} ({loop_s:.1f}s loop, {args.bpm:g} bpm, peak {args.peak_dbfs:g} dBFS)"
    )


if __name__ == "__main__":
    main()
