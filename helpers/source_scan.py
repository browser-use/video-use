"""Native frame and PTS catalogs and sequential selected-frame decoding."""

import argparse
import json
import math
import subprocess
from pathlib import Path
import numpy as np
from PIL import Image
from edit_io import probe, run, save_json, sha256


# record native presentation timestamps and the source file fingerprint
def catalog(path):
    """Record native presentation timestamps and the source file fingerprint."""
    data = probe(path)
    stream = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if stream is None:
        raise ValueError(f"no video stream in {path}")
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
    if not pts or not all(map(math.isfinite, pts)) or any(b <= a for a, b in zip(pts, pts[1:])):
        raise ValueError(
            "source needs nonempty strictly increasing presentation timestamps"
        )
    return {
        "file": str(Path(path).resolve()),
        "sha256": sha256(path),
        "stream": stream,
        "frames": [{"frame": i, "pts": t} for i, t in enumerate(pts)],
        "quality_review": "pending; dimensions and codec do not prove native source quality",
    }


# decode selected native frame indices in order with optional thumbnail scaling
def selected_frames(path, frames, width=None):
    """Decode selected native frame indices in order with optional thumbnail scaling."""
    frames = sorted(set(frames))
    if not frames or any(type(f) is not int or f < 0 for f in frames):
        raise ValueError("select nonnegative native frame indices")
    stream = next((s for s in probe(path)["streams"] if s["codec_type"] == "video"), None)
    if stream is None:
        raise ValueError(f"no video stream in {path}")
    width = width or stream["width"]
    height = max(1, round(width * stream["height"] / stream["width"]))

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
    process = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-threads",
            "2",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-filter_threads",
            "1",
            "-vf",
            f"select='{expression}',scale={width}:{height}",
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        for frame in frames:
            raw = process.stdout.read(width * height * 3)
            if len(raw) != width * height * 3:
                raise ValueError(f"source ended before native frame {frame}")
            yield frame, Image.frombytes("RGB", (width, height), raw)
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
        process.wait()


# suggest cuts from adjacent thumbnail differences for human review
def scene_candidates(path, index, threshold=0.18):
    """Suggest cuts from adjacent thumbnail differences for human review."""
    previous = None
    rows = []
    for frame, image in selected_frames(
        path, [r["frame"] for r in index["frames"]], 160
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
