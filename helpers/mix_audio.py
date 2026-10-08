"""Independent sample-addressed voice/music buses; picture cuts never fade music."""

import argparse
from pathlib import Path
import numpy as np
import math
from scipy.io import wavfile
from edit_clock import SAMPLE_RATE, frame_to_sample, fraction
from edit_io import run, load_json, save_json, source_path, last_json


# only clock-preserving eq operations may be attached to a source clip
def filter_chain(filters):
    """Only clock-preserving EQ operations may be attached to a source clip."""
    output = []
    for row in filters:
        if not isinstance(row, dict) or set(row) - {
            "type",
            "frequency_hz",
            "q",
            "gain_db",
        }:
            raise ValueError("unsupported audio filter fields")
        kind = row.get("type")
        frequency = float(row.get("frequency_hz", 0))
        if not math.isfinite(frequency) or not 0 < frequency < 24000:
            raise ValueError("filter frequency must be between 0 and Nyquist")
        if kind in ("highpass", "lowpass"):
            if set(row) - {"type", "frequency_hz"}:
                raise ValueError("pass filters accept only type and frequency_hz")
            output.append(f"{kind}=f={frequency}")
        elif kind == "equalizer":
            q = float(row.get("q", 1))
            gain = float(row.get("gain_db", 0))
            if (
                not math.isfinite(q)
                or not q > 0
                or not math.isfinite(gain)
                or not -30 <= gain <= 30
            ):
                raise ValueError("invalid equalizer parameters")
            output.append(f"equalizer=f={frequency}:t=q:w={q}:g={gain}")
        else:
            raise ValueError(
                "only highpass, lowpass, and equalizer filters preserve this audio contract"
            )
    return ",".join(output)


# decode and resample before applying exact source sample boundaries
def decode_window(path, start, count, filters=None):
    if type(start) is not int or type(count) is not int or start < 0 or count <= 0:
        raise ValueError(
            "source window needs a nonnegative start and positive integer sample count"
        )
    # Decode/resample before sample trimming. No compressed input -ss or millisecond delay.
    af = f"aresample={SAMPLE_RATE},atrim=start_sample={start}:end_sample={start+count},asetpts=PTS-STARTPTS"
    chain = filter_chain(filters or [])
    if chain:
        af += "," + chain
    raw = run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            path,
            "-map",
            "0:a:0",
            "-af",
            af,
            "-ac",
            "2",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "f32le",
            "-",
        ]
    ).stdout
    audio = np.frombuffer(raw, "<f4").reshape(-1, 2).copy()
    if len(audio) != count:
        raise ValueError(
            f"{path}: needed {count} samples, decoded {len(audio)}; choose a valid source range"
        )
    if not np.isfinite(audio).all():
        raise ValueError("decoded audio contains nonfinite samples")
    return audio


# interpolate clip gain on the audio sample clock
def gain_envelope(count, points, base_db=0):
    if type(count) is not int or count <= 0:
        raise ValueError("gain envelope needs a positive integer sample count")
    if not isinstance(points, (list, tuple)) or any(not isinstance(p, (list, tuple)) or len(p) != 2 for p in points):
        raise ValueError("gain points must be sample and gain pairs")
    if not math.isfinite(float(base_db)) or any(
        not math.isfinite(float(p[1])) for p in points
    ):
        raise ValueError("gain must be finite")
    if not points:
        return np.full(count, 10 ** (base_db / 20), dtype=np.float32)
    positions = [p[0] for p in points]
    if (
        any(type(p) is not int for p in positions)
        or positions != sorted(set(positions))
        or positions[0] != 0
        or positions[-1] > count
    ):
        raise ValueError(
            "gain points need unique ascending integer sample offsets beginning at zero"
        )
    db = np.interp(np.arange(count), positions, [p[1] for p in points]) + base_db
    return np.power(10, db / 20).astype(np.float32)


# apply explicit gain and role specific fades without changing sample count
def apply_clip(audio, clip):
    count = len(audio)
    envelope = gain_envelope(count, clip.get("gain_points", []), clip.get("gain_db", 0))
    for key, reverse in [("fade_in_samples", False), ("fade_out_samples", True)]:
        n = clip.get(key, 1440 if clip["role"] == "voice" else 0)
        if type(n) is not int or not 0 <= n <= count:
            raise ValueError("invalid fade length")
        if n:
            if reverse:
                envelope[-n:] *= np.linspace(1, 0, n, dtype=np.float32)
            else:
                envelope[:n] *= np.linspace(0, 1, n, dtype=np.float32)
    return audio * envelope[:, None]


# report each bus energy including a final partial second
def rms_per_second(audio):
    return [
        {
            "second": i // SAMPLE_RATE,
            "samples": len(audio[i : i + SAMPLE_RATE]),
            "rms_dbfs": float(
                20
                * np.log10(
                    max(
                        1e-12,
                        float(
                            np.sqrt(
                                np.mean(
                                    audio[i : i + SAMPLE_RATE].astype("float64") ** 2
                                )
                            )
                        ),
                    )
                )
            ),
        }
        for i in range(0, len(audio), SAMPLE_RATE)
    ]


# measure loudness before a second pass writes the normalized master
def normalize(source, target, lufs=-14, true_peak=-1.5):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or target.exists():
        raise FileExistsError("choose a new normalized output path")
    if not -70 <= lufs <= -5 or not -9 <= true_peak <= 0:
        raise ValueError("invalid loudness target")
    first = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            source,
            "-af",
            f"loudnorm=I={lufs}:TP={true_peak}:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    measured = last_json(first.stderr.decode(errors="replace"))
    if measured["input_i"] == "-inf":
        raise ValueError("cannot normalize silent audio")
    af = (
        f'loudnorm=I={lufs}:TP={true_peak}:LRA=11:measured_I={measured["input_i"]}:'
        f'measured_TP={measured["input_tp"]}:measured_LRA={measured["input_lra"]}:'
        f'measured_thresh={measured["input_thresh"]}:offset={measured["target_offset"]}:linear=true:print_format=json,aresample=48000'
    )
    second = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            source,
            "-af",
            af,
            "-ac",
            "2",
            "-c:a",
            "pcm_f32le",
            target,
        ]
    )
    return {
        "first_pass": measured,
        "second_pass": last_json(second.stderr.decode(errors="replace")),
    }


# place independent audio clips on a shared stereo sample timeline
def build(manifest, root, dest):
    dest = Path(dest)
    frames = manifest["total_frames"]
    fps = fraction(manifest.get("fps", 30))
    if type(frames) is not int or frames <= 0 or fps <= 0:
        raise ValueError(
            "timeline needs positive integer frames and a positive frame rate"
        )
    count = frame_to_sample(frames, fps)
    if count <= 0:
        raise ValueError("timeline contains no audio samples")
    outputs = [
        dest / name
        for name in (
            "mix.wav",
            "voice.wav",
            "music.wav",
            "effects.wav",
            "master.wav",
            "mix_report.json",
        )
    ]
    if any(path.exists() or path.is_symlink() for path in outputs):
        raise FileExistsError(
            "choose an output directory without existing mix artifacts"
        )
    seen = set()
    for clip in manifest.get("audio", []):
        if not clip.get("id") or clip["id"] in seen:
            raise ValueError("audio clips need unique nonempty ids")
        seen.add(clip["id"])
        if clip.get("role") not in ("voice", "music", "effects"):
            raise ValueError("audio role must be voice music or effects")
        for key in ("start_sample", "source_start_sample", "sample_count"):
            if type(clip.get(key)) is not int or clip[key] < (
                1 if key == "sample_count" else 0
            ):
                raise ValueError(
                    "audio placement needs nonnegative integer starts and a positive sample count"
                )
        if clip["start_sample"] + clip["sample_count"] > count:
            raise ValueError("audio exceeds timeline")
        source_path(manifest, root, clip["source"])
    if dest.is_symlink():
        raise ValueError("audio output directory cannot be a symlink")
    dest.mkdir(parents=True, exist_ok=True)
    buses = {r: np.zeros((count, 2), np.float32) for r in ("voice", "music", "effects")}
    clips = []
    for clip in manifest.get("audio", []):
        path = source_path(manifest, root, clip["source"])
        samples = decode_window(
            path, clip["source_start_sample"], clip["sample_count"], clip.get("filters")
        )
        processed = apply_clip(samples, clip)
        a = clip["start_sample"]
        b = a + len(processed)
        if not 0 <= a < b <= count:
            raise ValueError("audio exceeds timeline")
        buses[clip["role"]][a:b] += processed
        clips.append(
            {
                "id": clip["id"],
                "role": clip["role"],
                "start_sample": a,
                "end_sample": b,
                "source": str(path),
            }
        )
    master = sum(buses.values())
    if not np.isfinite(master).all():
        raise ValueError("mixed audio contains nonfinite samples")
    wavfile.write(dest / "mix.wav", SAMPLE_RATE, master)
    report = {"sample_rate": SAMPLE_RATE, "samples": count, "clips": clips, "buses": {}}
    for name, a in buses.items():
        wavfile.write(dest / f"{name}.wav", SAMPLE_RATE, a)
        report["buses"][name] = rms_per_second(a)
    targets = manifest.get("delivery", {})
    report["normalization"] = normalize(
        dest / "mix.wav",
        dest / "master.wav",
        targets.get("lufs", -14),
        targets.get("true_peak", -1.5),
    )
    save_json(dest / "mix_report.json", report)
    return dest / "master.wav"


# mix an explicit audio manifest without invoking the video renderer
def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest")
    p.add_argument("--out-dir", required=True)
    a = p.parse_args()
    print(build(load_json(a.manifest), Path(a.manifest).resolve().parent, a.out_dir))


if __name__ == "__main__":
    main()
