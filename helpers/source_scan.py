"""Native frame and PTS catalogs and sequential selected-frame decoding."""

import argparse
import json
import math
import subprocess
import tempfile
import threading
from pathlib import Path
import numpy as np
from PIL import Image
from edit_io import display_dimensions, file_state, probe, run, save_json, sha256


# record native presentation timestamps and the source file fingerprint
def timestamps(path):
    """Read finite, nondecreasing native video presentation timestamps."""
    frames = json.loads(
        run(
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
                path,
            ]
        ).stdout
    )["frames"]
    try:
        pts = [float(f["best_effort_timestamp_time"]) for f in frames]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("source frame is missing a usable presentation timestamp") from exc
    if not pts or not all(map(math.isfinite, pts)) or any(b < a for a, b in zip(pts, pts[1:])):
        raise ValueError(
            "source needs nonempty nondecreasing presentation timestamps"
        )
    return pts


def catalog(path):
    """Record native timestamps only when the source stays unchanged during scanning."""
    before = file_state(path)
    digest = sha256(path)
    data = probe(path)
    stream = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if stream is None:
        raise ValueError(f"no video stream in {path}")
    pts = timestamps(path)
    if file_state(path) != before or sha256(path) != digest or file_state(path) != before:
        raise ValueError("source changed during catalog scan")
    return {
        "file": str(Path(path).resolve()),
        "sha256": digest,
        "stream": stream,
        "frames": [{"frame": i, "pts": t} for i, t in enumerate(pts)],
        "quality_review": "pending; dimensions and codec do not prove native source quality",
    }


# decode selected native frame indices in order with optional thumbnail scaling
def selected_frames(path, frames, width=None, *, frame_count=None, timeout=600):
    """Decode selected native frame indices in order with optional thumbnail scaling."""
    frames = list(frames)
    if not frames or any(type(f) is not int or f < 0 for f in frames):
        raise ValueError("select nonnegative native frame indices")
    frames = sorted(set(frames))
    stream = next((s for s in probe(path)["streams"] if s["codec_type"] == "video"), None)
    if stream is None:
        raise ValueError(f"no video stream in {path}")
    known_count = frame_count
    if known_count is None:
        for key in ("nb_frames", "nb_read_frames"):
            try:
                known_count = int(stream[key])
                if known_count > 0:
                    break
                known_count = None
            except (KeyError, TypeError, ValueError):
                pass
    if known_count is not None and (type(known_count) is not int or known_count < 1 or frames[-1] >= known_count):
        raise ValueError("requested native frame is outside the source frame count")
    sw, sh = display_dimensions(stream)
    width = sw if width is None else width
    if type(width) is not int or width < 1:
        raise ValueError("thumbnail width must be a positive integer")
    height = max(1, round(width * sh / sw))
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("frame decoding timeout must be positive")

    # build a shallow FFmpeg expression for irregular frame selections
    def balanced(values):
        """Build a shallow FFmpeg expression for irregular frame selections."""
        if len(values) == 1:
            return f"eq(n,{values[0]})"
        middle = len(values) // 2
        return f"({balanced(values[:middle])}+{balanced(values[middle:])})"

    step = frames[1] - frames[0] if len(frames) > 1 else 1
    expression = (
        f"between(n,{frames[0]},{frames[-1]})*not(mod(n-{frames[0]},{step}))"
        if all(b - a == step for a, b in zip(frames, frames[1:]))
        else balanced(frames)
    )
    # A script keeps large irregular selections out of the OS argument-size limit.
    with tempfile.TemporaryDirectory(prefix="video-use-frames-") as folder:
        script = Path(folder) / "selection.txt"
        script.write_text(f"select='{expression}',scale={width}:{height}", encoding="utf-8")
        with (Path(folder) / "ffmpeg.log").open("w+b") as errors:
            process = subprocess.Popen(
                ["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-threads", "2", "-i", str(path),
                 "-map", "0:v:0", "-an", "-sn", "-dn", "-filter_threads", "1",
                 "-filter_script:v", str(script), "-fps_mode", "passthrough", "-frames:v", str(len(frames)),
                 "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                stdout=subprocess.PIPE, stderr=errors,
            )
            expired = threading.Event()

            def stop():
                expired.set()
                try:
                    process.kill()
                except ProcessLookupError:
                    pass

            timer = threading.Timer(timeout, stop)
            timer.daemon = True
            timer.start()
            try:
                for frame in frames:
                    raw = process.stdout.read(width * height * 3)
                    if expired.is_set():
                        raise ValueError(f"frame decoding exceeded {timeout:g} seconds")
                    if len(raw) != width * height * 3:
                        raise ValueError(f"source ended before native frame {frame}")
                    yield frame, Image.frombytes("RGB", (width, height), raw)
                result = process.wait()
                if expired.is_set():
                    raise ValueError(f"frame decoding exceeded {timeout:g} seconds")
                if result:
                    errors.seek(max(0, errors.tell() - 6000))
                    raise ValueError(f"frame decoding failed ({result}): {errors.read().decode(errors='replace')}")
            finally:
                timer.cancel()
                process.stdout.close()
                if process.poll() is None:
                    process.kill()
                process.wait()


# suggest cuts from adjacent thumbnail differences for human review
def scene_candidates(path, index, threshold=0.18):
    """Suggest cuts from adjacent thumbnail differences for human review."""
    previous = None
    rows = []
    for frame, image in selected_frames(
        path, [r["frame"] for r in index["frames"]], 160, frame_count=len(index["frames"])
    ):
        current = np.asarray(image, np.float32) / 255
        if previous is not None:
            change = float(np.abs(current - previous).mean())
            if change >= threshold:
                rows.append(
                    {
                        "frame": frame,
                        "pts": index["frames"][frame]["pts"],
                        "difference": change,
                    }
                )
        previous = current
    return rows


# write a source catalog and optional scene candidates from CLI arguments
def main():
    """Write a source catalog and optional scene candidates from CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--out", required=True)
    parser.add_argument("--scenes", action="store_true")
    args = parser.parse_args()
    if Path(args.source).resolve() == Path(args.out).resolve():
        parser.error("output cannot replace source")
    if Path(args.out).exists() or Path(args.out).is_symlink():
        parser.error("output already exists choose a new catalog path")
    data = catalog(args.source)
    if args.scenes:
        data["scene_candidates"] = scene_candidates(args.source, data)
    save_json(args.out, data, exclusive=True)


if __name__ == "__main__":
    main()
