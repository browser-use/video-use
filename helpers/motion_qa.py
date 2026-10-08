#!/usr/bin/env python3
"""Decode a motion export, verify its delivery contract, and create review images.

Usage:
    python helpers/motion_qa.py final.mp4
    python helpers/motion_qa.py final.mp4 --manifest final.render/render.json

Mechanical validation cannot judge design quality. Flat frames, holds, and large
frame changes are review cues rather than automatic aesthetic failures.
Dependencies: ffmpeg, ffprobe, numpy, Pillow (existing video-use dependencies).
"""
from __future__ import annotations

import argparse
import io
import json
import math
import struct
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


# capture command output and report the actual process failure
def run(args: list[str]) -> bytes:
    completed = subprocess.run(args, capture_output=True)
    if completed.returncode:
        raise RuntimeError(f"{args[0]} failed: {completed.stderr.decode(errors='replace')}")
    return completed.stdout


# read rational media metadata with an explicit invalid value fallback
def rational(value: str | float | None) -> float:
    try:
        return float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return 0.0


# group consecutive review cues into bounded time intervals
def spans(indices: list[int], fps: float, minimum_seconds: float = 0) -> list[dict]:
    """Group consecutive frame indices into half-open time intervals."""
    if not indices:
        return []
    groups = []
    first = previous = indices[0]
    for current in indices[1:] + [indices[-1] + 2]:
        if current != previous + 1:
            duration = (previous - first + 1) / fps
            if duration >= minimum_seconds:
                groups.append({"start": round(first / fps, 4), "end": round((previous + 1) / fps, 4), "duration": round(duration, 4)})
            first = current
        previous = current
    return groups


# choose distinct review frames including the encoded endpoints
def sample_indices(frame_count: int, count: int) -> list[int]:
    if frame_count < 1 or count < 1:
        raise ValueError("Frame count and sample count must be positive")
    return sorted(set(round(index * (frame_count - 1) / max(count - 1, 1)) for index in range(count)))


# inspect MP4 atom order without scanning compressed video payloads
def mp4_faststart(filename: Path) -> bool:
    """Check atom order without reading the video payload into memory."""
    atoms = []
    total = filename.stat().st_size
    with filename.open("rb") as stream:
        while stream.tell() + 8 <= total:
            position = stream.tell()
            size, kind = struct.unpack(">I4s", stream.read(8))
            if size == 1:
                extended = stream.read(8)
                if len(extended) != 8:
                    return False
                size = struct.unpack(">Q", extended)[0]
                header = 16
            else:
                header = 8
            if size == 0:
                size = total - position
            if size < header or position + size > total:
                return False
            atoms.append(kind)
            stream.seek(position + size)
    return b"moov" in atoms and b"mdat" in atoms and atoms.index(b"moov") < atoms.index(b"mdat")


# decode every video frame and measure temporal review cues
def decode_metrics(filename: Path, fps: float) -> dict:
    """Decode every frame; measure small grayscale frames for temporal review."""
    width, height = 64, 36
    command = ["ffmpeg", "-hide_banner", "-v", "error", "-xerror", "-i", str(filename), "-map", "0:v:0", "-vf", f"scale={width}:{height}:flags=area,format=gray", "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    previous = None
    frame_count = 0
    blank, frozen, changes = [], [], []
    try:
        while True:
            data = process.stdout.read(width * height)
            if not data:
                break
            if len(data) != width * height:
                raise RuntimeError("Decoded an incomplete frame")
            frame = np.frombuffer(data, dtype=np.uint8).astype(np.float32)
            mean, std = float(frame.mean()), float(frame.std())
            if std < 1.5 and (mean < 6 or mean > 249):
                blank.append(frame_count)
            if previous is not None:
                delta = float(np.abs(frame - previous).mean())
                changes.append(delta)
                if delta < 0.035:
                    frozen.append(frame_count)
            previous = frame
            frame_count += 1
        error = process.stderr.read().decode(errors="replace")
        if process.wait() or error.strip():
            raise RuntimeError(f"Video decode failed: {error}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()
    abrupt = [{"time": round((index + 1) / fps, 4), "meanLumaChange": round(delta, 3)} for index, delta in enumerate(changes) if delta > 32]
    return {"decodedFrames": frame_count, "flatBlackOrWhiteRanges": spans(blank, fps), "nearIdenticalFrameRanges": spans(frozen, fps, .75), "largeFrameChanges": abrupt, "meanFrameChange": round(float(np.mean(changes)), 4) if changes else 0.0}


# compare encoded delivery metadata and frame count with the contract
def validate_video(video: dict, expected: dict, decoded_frames: int) -> list[str]:
    errors = []
    fps = rational(video.get("avg_frame_rate"))
    for field in ("width", "height"):
        if expected.get(field) is not None and video.get(field) != expected[field]:
            errors.append(f"{field}: expected {expected[field]}, got {video.get(field)}")
    if expected.get("fps") is not None and abs(fps - expected["fps"]) > .001:
        errors.append(f"fps: expected {expected['fps']}, got {fps}")
    if expected.get("frameCount") is not None and decoded_frames != expected["frameCount"]:
        errors.append(f"frame count: expected {expected['frameCount']}, decoded {decoded_frames}")
    if expected.get("duration") is not None:
        duration = rational(video.get("duration"))
        if abs(duration - expected["duration"]) > max(.001, .5 / max(fps, 1)):
            errors.append(f"video duration: expected {expected['duration']}, got {duration}")
    if video.get("codec_name") != "h264":
        errors.append(f"codec: expected h264, got {video.get('codec_name')}")
    if video.get("pix_fmt") != "yuv420p":
        errors.append(f"pixel format: expected yuv420p, got {video.get('pix_fmt')}")
    if expected.get("encoding", {}).get("colorSpace") and video.get("color_space") != expected["encoding"]["colorSpace"]:
        errors.append(f"color space: expected {expected['encoding']['colorSpace']}, got {video.get('color_space')}")
    return errors


# decode one review image at the requested output time
def extract_image(filename: Path, time: float, width: int | None = None) -> Image.Image:
    args = ["ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{time:.8f}", "-i", str(filename), "-frames:v", "1"]
    if width:
        args += ["-vf", f"scale={width}:-1:flags=lanczos"]
    args += ["-f", "image2pipe", "-vcodec", "png", "pipe:1"]
    return Image.open(io.BytesIO(run(args))).convert("RGB")


# write labeled review frames and a contact sheet through the final frame
def review_images(filename: Path, out: Path, frames: int, fps: float, samples: int, columns: int, thumb_width: int) -> dict:
    indices = sample_indices(frames, samples)
    thumbnails = [extract_image(filename, index / fps, thumb_width) for index in indices]
    gap, label_height, header = 16, 30, 50
    cell_height = thumbnails[0].height + label_height
    rows = math.ceil(len(thumbnails) / columns)
    sheet = Image.new("RGB", (columns * (thumb_width + gap) + gap, header + rows * (cell_height + gap) + gap), "#101319")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=18)
    draw.text((gap, 15), f"{filename.name}   /   encoded frame review   /   {fps:g} fps", font=font, fill="#e8eef5")
    for index, (frame_index, thumbnail) in enumerate(zip(indices, thumbnails)):
        x = gap + (index % columns) * (thumb_width + gap)
        y = header + gap + (index // columns) * (cell_height + gap)
        sheet.paste(thumbnail, (x, y))
        draw.text((x + 4, y + thumbnail.height + 6), f"{frame_index / fps:06.2f}s   frame {frame_index}", font=font, fill="#b6c0ce")
    sheet_path = out / "contact-sheet.jpg"
    sheet.save(sheet_path, quality=94, subsampling=0)
    poster_time = round((frames - 1) * .6) / fps
    poster_path = out / "poster.png"
    extract_image(filename, poster_time).save(poster_path)
    return {"contactSheet": str(sheet_path), "sampleFrames": indices, "poster": str(poster_path), "posterTime": poster_time}


# validate a motion export and save mechanical evidence for visual review
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--expect-width", type=int)
    parser.add_argument("--expect-height", type=int)
    parser.add_argument("--expect-fps", type=float)
    parser.add_argument("--expect-duration", type=float)
    parser.add_argument("--expect-audio", action="store_true")
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--columns", type=int, default=3)
    parser.add_argument("--thumbnail-width", type=int, default=480)
    args = parser.parse_args(argv)
    if min(args.samples, args.columns, args.thumbnail_width) < 1:
        parser.error("samples, columns, and thumbnail width must be positive")
    filename = args.video.resolve(strict=True)
    manifest = args.manifest or filename.with_suffix(".render") / "render.json"
    expected = json.loads(manifest.read_text()) if manifest.exists() else {}
    if args.manifest and not manifest.exists():
        raise FileNotFoundError(manifest)
    if not isinstance(expected, dict):
        parser.error("manifest must be a JSON object")
    for field in ("width", "height", "fps", "duration"):
        value = getattr(args, f"expect_{field}")
        if value is not None:
            expected[field] = value
    for field in ("width", "height", "fps", "duration", "frameCount"):
        value = expected.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
            parser.error(f"expected {field} must be a positive finite number")
        if field in {"width", "height", "frameCount"} and value is not None and int(value) != value:
            parser.error(f"expected {field} must be an integer")
    if not isinstance(expected.get("encoding", {}), dict):
        parser.error("manifest encoding must be an object")
    if expected.get("fps") and expected.get("duration"):
        expected.setdefault("frameCount", round(expected["fps"] * expected["duration"]))
    if args.output_dir:
        out = args.output_dir.absolute()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.mkdir()  # Refuse existing directories and links even when they appear empty.
    else:
        out = Path(tempfile.mkdtemp(prefix=filename.stem + ".qa-", dir=filename.parent))
    probe = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(filename)]))
    video = next((stream for stream in probe["streams"] if stream["codec_type"] == "video"), None)
    if video is None:
        raise RuntimeError("No video stream")
    fps = rational(video.get("avg_frame_rate"))
    if fps <= 0:
        raise RuntimeError("Invalid or unknown frame rate")
    metrics = decode_metrics(filename, fps)
    errors = validate_video(video, expected, metrics["decodedFrames"])
    faststart = mp4_faststart(filename)
    if not faststart:
        errors.append("MP4 moov atom does not precede mdat; web playback lacks faststart")
    audio = [stream for stream in probe["streams"] if stream["codec_type"] == "audio"]
    if (args.expect_audio or expected.get("audio")) and not audio:
        errors.append("Expected an audio stream but none exists")
    # Decode audio too; -xerror turns corrupt packets into a failing check.
    if audio:
        run(["ffmpeg", "-hide_banner", "-v", "error", "-xerror", "-i", str(filename), "-map", "0:a", "-f", "null", "-"])
    images = review_images(filename, out, metrics["decodedFrames"], fps, args.samples, args.columns, args.thumbnail_width)
    report = {"schema": "video-use.motion-qa.v1", "video": str(filename), "technicalPass": not errors, "errors": errors, "faststart": faststart, "width": video["width"], "height": video["height"], "fps": fps, "duration": rational(video.get("duration")), "audioStreams": len(audio), "metrics": metrics, "images": images, "designReview": "Required: inspect hierarchy, legibility at playback size, clipping, intentional holds, transition continuity, material quality, and ending. Temporal cues are not aesthetic verdicts."}
    report_path = out / "qa.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Technical validation: {'PASS' if not errors else 'FAIL'} | {metrics['decodedFrames']} frames | {video['width']}x{video['height']} | {fps:g} fps")
    for error in errors:
        print(f"ERROR: {error}")
    print(f"Review cues: {len(metrics['flatBlackOrWhiteRanges'])} flat ranges, {len(metrics['nearIdenticalFrameRanges'])} holds, {len(metrics['largeFrameChanges'])} large frame changes")
    print(f"Contact sheet: {images['contactSheet']}\nReport: {report_path}")
    return int(bool(errors))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError) as error:
        print(f"QA failed: {error}", file=sys.stderr)
        sys.exit(1)
