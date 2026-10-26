"""Check rendered video around EDL cut boundaries.

This is a conservative post-render QC pass. It reports transient audio jumps and
single-frame luminance flashes for human review; it does not repair the render
or claim that a clean report proves the whole video is artifact-free.

Usage:
    python helpers/verify_cuts.py edit/edl.json edit/final.mp4
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO


AUDIO_RATE = 48_000
AUDIO_WINDOW_SECONDS = 0.75
AUDIO_CHECK_SECONDS = 0.25
FRAME_WIDTH = 96
FRAME_HEIGHT = 54


class VerificationError(Exception):
    """Raised when media or EDL data cannot be inspected."""


def output_boundaries(edl: dict) -> list[float]:
    """Return output-timeline cut times from the EDL's source ranges."""
    if not isinstance(edl, dict):
        raise VerificationError("EDL must be a JSON object")
    ranges = edl.get("ranges")
    if not isinstance(ranges, list) or not ranges:
        raise VerificationError("EDL must contain a non-empty 'ranges' list")

    durations: list[float] = []
    for index, item in enumerate(ranges):
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise VerificationError(
                f"EDL range {index + 1} needs numeric start and end values"
            ) from exc
        duration = end - start
        if not math.isfinite(duration) or duration <= 0:
            raise VerificationError(f"EDL range {index + 1} has an invalid duration")
        durations.append(duration)

    boundaries: list[float] = []
    elapsed = 0.0
    for duration in durations[:-1]:
        elapsed += duration
        boundaries.append(elapsed)
    return boundaries


def probe_media(video: Path) -> dict:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(video)],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise VerificationError("ffprobe was not found; install FFmpeg first") from exc
    except subprocess.CalledProcessError as exc:
        raise VerificationError(exc.stderr.strip() or "ffprobe could not read the video") from exc
    except OSError as exc:
        raise VerificationError(f"could not run ffprobe: {exc}") from exc

    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError as exc:
        raise VerificationError("ffprobe returned invalid JSON") from exc
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not video_streams:
        raise VerificationError("the rendered file has no video stream")
    return {"video": video_streams[0], "audio": audio_streams[0] if audio_streams else None}


def _percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)]


def inspect_audio_boundary(
    pcm: BinaryIO,
    sample_count: int,
    boundary_seconds: float,
    *,
    sample_rate: int = AUDIO_RATE,
    ratio_threshold: float = 5.0,
    absolute_delta_floor: int = 1200,
) -> dict:
    """Compare the largest near-boundary sample jump with nearby audio levels."""
    start = max(0, int((boundary_seconds - AUDIO_WINDOW_SECONDS) * sample_rate))
    end = min(sample_count, math.ceil((boundary_seconds + AUDIO_WINDOW_SECONDS) * sample_rate))
    if end - start < 4:
        return {"status": "unavailable", "reason": "not enough audio around boundary"}

    pcm.seek(start * 2)
    raw = pcm.read((end - start) * 2)
    sample_count_in_window = len(raw) // 2
    if sample_count_in_window < 4:
        return {"status": "unavailable", "reason": "not enough decoded audio samples"}
    samples = struct.unpack(f"<{sample_count_in_window}h", raw[: sample_count_in_window * 2])

    near: list[tuple[int, int]] = []
    baseline: list[int] = []
    for offset, (left, right) in enumerate(zip(samples, samples[1:])):
        jump = abs(right - left)
        midpoint = (start + offset + 0.5) / sample_rate
        if abs(midpoint - boundary_seconds) <= AUDIO_CHECK_SECONDS:
            near.append((jump, start + offset))
        else:
            baseline.append(jump)

    if not near or not baseline:
        return {"status": "unavailable", "reason": "no usable local comparison window"}

    peak_delta, peak_index = max(near)
    local_p95 = _percentile(baseline, 0.95)
    ratio = peak_delta / max(local_p95, 1)
    flagged = peak_delta >= absolute_delta_floor and ratio >= ratio_threshold
    return {
        "status": "review" if flagged else "clear",
        "peak_delta_samples": peak_delta,
        "local_p95_delta_samples": local_p95,
        "peak_to_local_p95_ratio": round(ratio, 2),
        "peak_offset_ms": round(((peak_index + 0.5) / sample_rate - boundary_seconds) * 1000, 2),
        "thresholds": {
            "absolute_delta_floor_samples": absolute_delta_floor,
            "ratio": ratio_threshold,
        },
    }


def _decode_audio(video: Path) -> tuple[BinaryIO, int]:
    pcm = tempfile.TemporaryFile()
    try:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
                "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(AUDIO_RATE),
                "-f", "s16le", "pipe:1",
            ],
            stdout=pcm,
            stderr=subprocess.PIPE,
            check=True,
        )
    except FileNotFoundError as exc:
        pcm.close()
        raise VerificationError("ffmpeg was not found; install FFmpeg first") from exc
    except subprocess.CalledProcessError as exc:
        pcm.close()
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise VerificationError(detail or "ffmpeg could not decode the audio stream") from exc
    except OSError as exc:
        pcm.close()
        raise VerificationError(f"could not run ffmpeg: {exc}") from exc

    pcm.seek(0, 2)
    size = pcm.tell()
    pcm.seek(0)
    return pcm, size // 2


def _frame_rate(stream: dict) -> float:
    for field in ("avg_frame_rate", "r_frame_rate"):
        value = stream.get(field)
        if not value or value == "0/0":
            continue
        try:
            rate = float(Fraction(value))
        except (ValueError, ZeroDivisionError):
            continue
        if math.isfinite(rate) and rate > 0:
            return rate
    return 30.0


def _extract_boundary_frames(video: Path, boundaries: list[float], fps: float) -> list[tuple[float, int]]:
    if not boundaries:
        return []

    radius = 3.0 / fps
    intervals: list[tuple[float, float]] = []
    for boundary in sorted(boundaries):
        low, high = max(0.0, boundary - radius), boundary + radius
        if intervals and low <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], high))
        else:
            intervals.append((low, high))
    selection = "+".join(
        f"between(t\\,{low:.6f}\\,{high:.6f})" for low, high in intervals
    )
    filter_graph = (
        f"select={selection},scale={FRAME_WIDTH}:{FRAME_HEIGHT}:flags=area,"
        "format=gray,showinfo"
    )
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(video),
                "-vf", filter_graph, "-an", "-fps_mode", "passthrough",
                "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
            ],
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise VerificationError("ffmpeg was not found; install FFmpeg first") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise VerificationError(detail or "ffmpeg could not inspect video frames") from exc
    except OSError as exc:
        raise VerificationError(f"could not run ffmpeg: {exc}") from exc

    log = result.stderr.decode("utf-8", errors="replace")
    times = [float(value) for value in re.findall(r"pts_time:([+-]?(?:\d+(?:\.\d*)?|\.\d+))", log)]
    frame_size = FRAME_WIDTH * FRAME_HEIGHT
    if len(result.stdout) % frame_size:
        raise VerificationError("ffmpeg returned an incomplete grayscale frame")
    frame_count = len(result.stdout) // frame_size
    if frame_count != len(times):
        raise VerificationError("could not match inspected frames to their timestamps")

    frames: list[tuple[float, int]] = []
    for index, timestamp in enumerate(times):
        data = result.stdout[index * frame_size : (index + 1) * frame_size]
        frames.append((timestamp, sum(data) // len(data)))
    return frames


def inspect_video_boundary(
    frames: list[tuple[float, int]],
    boundary_seconds: float,
    *,
    fps: float,
    luma_threshold: int = 45,
) -> dict:
    """Flag a one-frame luminance excursion, not a sustained scene change."""
    radius = 3.1 / fps
    nearby = [(time, luma) for time, luma in frames if abs(time - boundary_seconds) <= radius]
    if len(nearby) < 3:
        return {"status": "unavailable", "reason": "not enough decoded frames around boundary"}
    frame_interval = 1.0 / fps
    inspected_triple = False
    for index in range(1, len(nearby) - 1):
        prev_time, prev_luma = nearby[index - 1]
        time, luma = nearby[index]
        next_time, next_luma = nearby[index + 1]
        if max(time - prev_time, next_time - time) > 2.2 * frame_interval:
            continue
        inspected_triple = True
        neighbor_delta = abs(prev_luma - next_luma)
        deviation = abs(luma - (prev_luma + next_luma) / 2)
        if neighbor_delta <= 20 and deviation >= luma_threshold:
            return {
                "status": "review",
                "frame_time_seconds": round(time, 4),
                "mean_luma": luma,
                "neighbor_mean_luma_delta": neighbor_delta,
                "single_frame_deviation": round(deviation, 2),
                "threshold": luma_threshold,
            }
    if not inspected_triple:
        return {"status": "unavailable", "reason": "no consecutive frames around boundary"}
    return {"status": "clear", "frames_checked": len(nearby)}


def analyze_render(
    edl_path: Path,
    video_path: Path,
    *,
    ratio_threshold: float = 5.0,
    absolute_delta_floor: int = 1200,
    luma_threshold: int = 45,
) -> dict:
    if not edl_path.is_file():
        raise VerificationError(f"EDL not found: {edl_path}")
    if not video_path.is_file():
        raise VerificationError(f"rendered video not found: {video_path}")
    try:
        edl = json.loads(edl_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"could not read EDL: {exc}") from exc

    boundaries = output_boundaries(edl)
    streams = probe_media(video_path)
    fps = _frame_rate(streams["video"])

    audio_frames: list[dict] = []
    pcm: BinaryIO | None = None
    sample_count = 0
    if boundaries and streams["audio"]:
        pcm, sample_count = _decode_audio(video_path)
        try:
            audio_frames = [
                inspect_audio_boundary(
                    pcm,
                    sample_count,
                    boundary,
                    ratio_threshold=ratio_threshold,
                    absolute_delta_floor=absolute_delta_floor,
                )
                for boundary in boundaries
            ]
        finally:
            pcm.close()
    elif not streams["audio"]:
        audio_frames = [
            {"status": "unavailable", "reason": "rendered file has no audio stream"}
            for _ in boundaries
        ]

    frame_samples = _extract_boundary_frames(video_path, boundaries, fps)
    video_frames = [
        inspect_video_boundary(
            frame_samples, boundary, fps=fps, luma_threshold=luma_threshold
        )
        for boundary in boundaries
    ]

    checks = []
    for index, boundary in enumerate(boundaries):
        audio = audio_frames[index]
        video = video_frames[index]
        checks.append({
            "boundary": index + 1,
            "time_seconds": round(boundary, 4),
            "status": (
                "review" if "review" in (audio["status"], video["status"])
                else "unavailable" if "unavailable" in (audio["status"], video["status"])
                else "clear"
            ),
            "audio": audio,
            "video": video,
        })
    review_count = sum(check["status"] == "review" for check in checks)
    unavailable_count = sum(check["status"] == "unavailable" for check in checks)
    return {
        "schema_version": 1,
        "edl": str(edl_path),
        "rendered_video": str(video_path),
        "status": "review" if review_count else "unavailable" if unavailable_count else "clear",
        "summary": {
            "boundaries_checked": len(checks),
            "boundaries_needing_review": review_count,
            "boundaries_unavailable": unavailable_count,
            "video_frame_rate_used_for_sampling": round(fps, 4),
            "audio_sample_rate": AUDIO_RATE if streams["audio"] else None,
        },
        "checks": checks,
    }


def _format_time(seconds: float) -> str:
    minutes, remainder = divmod(seconds, 60)
    return f"{int(minutes):02d}:{remainder:06.3f}"


def format_report(report: dict) -> str:
    summary = report["summary"]
    lines = [
        f"Cut-boundary QC: {report['status'].upper()} — "
        f"{summary['boundaries_needing_review']} of {summary['boundaries_checked']} boundaries need review; "
        f"{summary['boundaries_unavailable']} unavailable"
    ]
    for check in report["checks"]:
        details = []
        audio = check["audio"]
        video = check["video"]
        if audio["status"] == "review":
            details.append(
                "audio transient candidate "
                f"(Δ {audio['peak_delta_samples']} samples, "
                f"{audio['peak_to_local_p95_ratio']}× local p95, "
                f"offset {audio['peak_offset_ms']} ms)"
            )
        else:
            details.append(f"audio {audio['status']}")
        if video["status"] == "review":
            details.append(
                f"single-frame luma excursion at {video['frame_time_seconds']:.3f}s"
            )
        else:
            details.append(f"video {video['status']}")
        lines.append(
            f"  {check['boundary']:>3}  {_format_time(check['time_seconds'])}  "
            f"{check['status'].upper()}  —  " + "; ".join(details)
        )
    lines.append("Review flags are candidates for listening/inspection, not confirmed defects.")
    if summary["boundaries_unavailable"]:
        lines.append("Unavailable checks did not have enough media to assess and do not count as clear.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect rendered video at EDL cut boundaries")
    parser.add_argument("edl", type=Path, help="EDL JSON used to render the video")
    parser.add_argument("video", type=Path, help="Rendered video to inspect")
    parser.add_argument("--json-out", type=Path, help="Also write the machine-readable report to this path")
    parser.add_argument("--click-ratio", type=float, default=5.0, help="Audio jump / nearby p95 ratio (default: 5)")
    parser.add_argument("--click-floor", type=int, default=1200, help="Minimum 16-bit sample jump (default: 1200)")
    parser.add_argument("--flash-luma", type=int, default=45, help="Single-frame luma deviation threshold (default: 45)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.click_ratio) or args.click_ratio <= 0:
        parser.error("--click-ratio must be a positive finite number")
    if args.click_floor < 0:
        parser.error("--click-floor must be zero or greater")
    if not 0 <= args.flash_luma <= 255:
        parser.error("--flash-luma must be between 0 and 255")

    try:
        report = analyze_render(
            args.edl.resolve(),
            args.video.resolve(),
            ratio_threshold=args.click_ratio,
            absolute_delta_floor=args.click_floor,
            luma_threshold=args.flash_luma,
        )
    except VerificationError as exc:
        print(f"Cut-boundary QC could not run: {exc}", file=sys.stderr)
        return 2

    print(format_report(report))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 1 if report["status"] == "review" else 2 if report["status"] == "unavailable" else 0


if __name__ == "__main__":
    raise SystemExit(main())
