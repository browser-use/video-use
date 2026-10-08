"""Verify actual media, audio tails, caption clocks and declared music presence."""

import argparse
from pathlib import Path
import numpy as np
from scipy import signal
from PIL import Image, ImageDraw
from edit_clock import frame_to_sample
from edit_io import run, probe, load_json, save_json, last_json, sha256
from mix_audio import rms_per_second


# decode the delivered stereo track for sample and alignment checks
def decode_audio(path):
    raw = run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            path,
            "-map",
            "0:a:0",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-f",
            "f32le",
            "-",
        ]
    ).stdout
    return np.frombuffer(raw, "<f4").reshape(-1, 2)


# compare declared caption entry frames with mapped source word timing
def caption_timing(manifest):
    rows = []
    for ident, word in manifest.get("words", {}).items():
        cards = [c for c in manifest.get("cards", []) if ident in c["word_ids"]]
        if not cards:
            continue
        first = min(cards, key=lambda c: c["start_frame"])
        linked = [manifest["words"][key] for key in first["word_ids"]]
        first_word_start = min(w["start_sample"] for w in linked)
        delta = frame_to_sample(first["start_frame"]) - first_word_start
        covered = "end_frame" not in first or word["start_sample"] < frame_to_sample(first["end_frame"])
        rows.append(
            {
                "word": word["text"],
                "word_id": ident,
                "first_frame": first["start_frame"],
                "offset_ms": delta / 48,
                "within_one_frame": abs(delta) <= 1600 and covered,
                "exception": first.get("timing_exception"),
            }
        )
    return rows


# extract picture and caption boundary frames into new review sheets
def review_sheets(manifest, video, dest):
    import cv2

    dest = Path(dest)
    if dest.is_symlink() or (dest.exists() and any(dest.iterdir())):
        raise FileExistsError("review directory must be new or empty")
    dest.mkdir(parents=True, exist_ok=True)
    items = []
    for shot in manifest["shots"]:
        a, b = shot["start_frame"], shot["end_frame"]
        items.extend(
            [
                (a, f'{shot["id"]} first'),
                ((a + b - 1) // 2, f'{shot["id"]} middle'),
                (b - 1, f'{shot["id"]} last'),
            ]
        )
    for card in manifest.get("cards", []):
        a, b = card["start_frame"], card["end_frame"]
        for n, label in [
            (max(0, a - 1), "before"),
            (a, "first"),
            ((a + b - 1) // 2, "middle"),
            (b - 1, "last"),
            (b, "after"),
        ]:
            if n < manifest["total_frames"]:
                items.append((n, card["id"] + " " + label))
    wanted = {n for n, _ in items}
    images = {}
    cap = cv2.VideoCapture(str(video))
    n = 0
    x, y, w, h = manifest["picture"]
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if n in wanted:
            images[n] = Image.fromarray(
                cv2.cvtColor(frame[y : y + h, x : x + w], cv2.COLOR_BGR2RGB)
            )
        n += 1
    cap.release()
    paths = []
    th = round(h * 540 / w)
    for offset in range(0, len(items), 10):
        batch = items[offset : offset + 10]
        sheet = Image.new("RGB", (1080, (th + 30) * ((len(batch) + 1) // 2)), "#111111")
        draw = ImageDraw.Draw(sheet)
        for i, (frame, label) in enumerate(batch):
            if frame not in images:
                raise ValueError(f"final video missing frame {frame}")
            xx = i % 2 * 540
            yy = i // 2 * (th + 30)
            sheet.paste(images[frame].resize((540, th)), (xx, yy + 30))
            draw.text((xx + 5, yy + 5), f"f{frame} {label}", fill="white")
        path = dest / f"review_{offset//10+1:03}.jpg"
        sheet.save(path, quality=92)
        paths.append(str(path))
    return paths


# measure the encoded picture clock audio delivery and declared caption schedule
def verify(manifest, root, video, work=None):
    video = Path(video)
    data = probe(video, True)
    streams = data["streams"]
    v = next(s for s in streams if s["codec_type"] == "video")
    audio_streams = [s for s in streams if s["codec_type"] == "audio"]
    expected = manifest["total_frames"]
    duration = expected / 30
    checks = {
        "frame_count": int(v["nb_read_frames"]) == expected,
        "dimensions": [v["width"], v["height"]] == manifest["canvas"],
        "fps": v["r_frame_rate"] == "30/1",
        "audio_exists": bool(audio_streams),
    }
    timestamp_data = load_json_from_run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "json",
            str(video),
        ]
    )
    timestamps = np.array(
        [float(f["best_effort_timestamp_time"]) for f in timestamp_data["frames"]]
    )
    pts_error = float(np.max(np.abs(timestamps - np.arange(len(timestamps)) / 30)))
    checks["frame_clock"] = pts_error <= 1e-6
    report = {
        "file": str(video),
        "sha256": sha256(video),
        "checks": checks,
        "max_pts_error_seconds": pts_error,
        "caption_timing": caption_timing(manifest),
        "probe": data,
    }
    checks["caption_schedule"] = all(
        row["within_one_frame"] or bool(row["exception"])
        for row in report["caption_timing"]
    )
    if work:
        checks["audio_alignment"] = False
    if manifest.get("music_required_intervals"):
        checks["declared_music_presence"] = False
    if audio_streams:
        a = audio_streams[0]
        checks["stereo_48k"] = (
            a.get("channels") == 2 and a.get("sample_rate") == "48000"
        )
        checks["audio_duration"] = abs(float(a.get("duration", 0)) - duration) <= 0.022
        decoded = decode_audio(video)
        wanted = frame_to_sample(expected)
        checks["decoded_audio_tail"] = wanted <= len(decoded) <= wanted + 1024
        report["decoded_audio_samples"] = len(decoded)
        report["expected_audio_samples"] = wanted
        report["final_mix_rms"] = rms_per_second(decoded[:wanted])
        loud = run(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                video,
                "-af",
                "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
                "-f",
                "null",
                "-",
            ]
        )
        report["loudness"] = last_json(loud.stderr.decode(errors="replace"))
        target = manifest.get("delivery", {})
        checks["loudness"] = abs(
            float(report["loudness"]["input_i"]) - target.get("lufs", -14)
        ) <= target.get("lufs_tolerance", 0.5)
        checks["true_peak"] = float(report["loudness"]["input_tp"]) <= target.get(
            "max_final_true_peak", -1.0
        )
        if work:
            work = Path(work)
            master_path = work / "audio/master.wav"
            if master_path.exists():
                master = decode_audio(master_path)
                align = []
                length = min(48000, max(256, wanted // 5))
                for st in sorted(
                    set(
                        [
                            min(4800, max(256, wanted // 10)),
                            max(256, wanted // 2 - length // 2),
                            max(256, wanted - length - 256),
                        ]
                    )
                ):
                    if st + length + 256 > min(len(master), len(decoded)):
                        continue
                    reference = master[st : st + length].mean(1)
                    sample = decoded[st - 256 : st + length + 256].mean(1)
                    if np.std(reference) < 1e-7:
                        continue
                    scores = signal.correlate(sample, reference, "valid", method="fft")
                    lag = int(np.argmax(scores)) - 256
                    corr = float(
                        np.corrcoef(
                            reference, decoded[st + lag : st + lag + length].mean(1)
                        )[0, 1]
                    )
                    align.append(
                        {"window_sample": st, "lag_samples": lag, "correlation": corr}
                    )
                report["audio_master_alignment"] = align
                checks["audio_alignment"] = bool(align) and all(
                    abs(r["lag_samples"]) <= 1 and r["correlation"] > 0.98
                    for r in align
                )
            music_path = work / "audio/music.wav"
            if music_path.exists():
                music = decode_audio(music_path)
                gaps = []
                for span in manifest.get("music_required_intervals", []):
                    start = frame_to_sample(span["start_frame"])
                    end = frame_to_sample(span["end_frame"])
                    for st in range(start, end, 4800):
                        sample = music[st : min(end, st + 4800)]
                        db = (
                            20
                            * np.log10(
                                max(
                                    1e-12,
                                    float(
                                        np.sqrt(np.mean(sample.astype("float64") ** 2))
                                    ),
                                )
                            )
                            if len(sample)
                            else -240
                        )
                        if db < span.get("minimum_rms_dbfs", -60):
                            gaps.append(
                                {
                                    "sample": st,
                                    "seconds": st / 48000,
                                    "rms_dbfs": float(db),
                                }
                            )
                report["music_gaps_in_declared_intervals"] = gaps
                checks["declared_music_presence"] = not gaps
    run(["ffmpeg", "-v", "error", "-i", video, "-f", "null", "-"])
    checks["full_decode"] = True
    report["technical_pass"] = all(checks.values())
    report["visual_review"] = "pending; inspect generated sheets and the moving output"
    report["listening_review"] = (
        "pending; RMS and phase checks do not prove intelligibility or aesthetic balance"
    )
    return report


# decode structured media probe output
def load_json_from_run(args):
    import json

    return json.loads(run(args).stdout)


# parse arguments and write new output artifacts without replacing existing files
def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest")
    p.add_argument("video")
    p.add_argument("--work-dir")
    p.add_argument("--out", required=True)
    p.add_argument("--sheets")
    a = p.parse_args()
    output = Path(a.out)
    if output.exists() or output.is_symlink():
        raise FileExistsError("verification output must be a new file")
    m = load_json(a.manifest)
    result = verify(m, Path(a.manifest).resolve().parent, a.video, a.work_dir)
    if a.sheets:
        result["review_sheets"] = review_sheets(m, a.video, a.sheets)
    save_json(a.out, result)
    if not result["technical_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
