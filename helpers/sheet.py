"""Contact sheets labeled with original decoded frame indices and presentation times."""

import argparse
import math
import bisect
import json
import os
import re
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from source_scan import catalog, selected_frames
from edit_io import save_json, sha256


# select the first native frame at or after each requested source-relative time
def frames_at_times(rows, times):
    if not rows:
        raise ValueError("source has no frames")
    pts = [row["pts"] - rows[0]["pts"] for row in rows]
    result = []
    for time in times:
        if not math.isfinite(time) or time < 0 or time > pts[-1] + 1e-9:
            raise ValueError(
                f"review time {time} is outside decoded frame timestamps 0..{pts[-1]:g}"
            )
        # absorb floating-point cancellation when a stream starts at a nonzero PTS
        result.append(rows[bisect.bisect_left(pts, time - 1e-9)]["frame"])
    return sorted(set(result))


# reuse only matching source bytes and settings with every artifact still intact
def cached_review(destination, request):
    try:
        if (destination / "review.json").is_symlink():
            return None
        report = json.loads((destination / "review.json").read_text())
        if report["request"] != request or not report["artifacts"]:
            return None
        expected = {f'frame_{row["frame"]:06}.png' for row in report["frames"]}
        expected.update(report["sheets"])
        if (
            not report["frames"]
            or not report["sheets"]
            or set(report["artifacts"]) != expected
        ):
            return None
        for name, digest in report["artifacts"].items():
            if not re.fullmatch(r"(frame_[0-9]+\.png|sheet_[0-9]+\.jpg)", name):
                return None
            path = destination / name
            if path.is_symlink() or not path.is_file() or sha256(path) != digest:
                return None
        return report
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


# extract critical frames once at native size and publish bounded labeled sheets
def review(source, destination, times, *, width=320, columns=5, force=False):
    """Review a finished render, not a file being written. Never modify an EDL.

    Times are seconds from the first decoded video PTS, snapped forward to an
    existing frame. Native PNGs remain available for full-resolution inspection.
    Reuse skips probing and decoding only when source and artifact hashes match.
    """
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute()
    times = sorted(set(float(t) for t in times))
    if (
        not source.is_file()
        or not times
        or any(not math.isfinite(t) or t < 0 for t in times)
    ):
        raise ValueError("provide a video and nonnegative finite review times")
    if len(times) > 120 or not 32 <= width <= 1920 or not 1 <= columns <= 10:
        raise ValueError(
            "review supports up to 120 times, width 32..1920 and columns 1..10"
        )
    if source.is_relative_to(destination.resolve()) or any(
        p.is_symlink() for p in (destination, *destination.parents)
    ):
        raise ValueError(
            "review destination must not contain the source or use symlinks"
        )
    request = {
        "schema_version": 1,
        "source": str(source),
        "sha256": sha256(source),
        "times": times,
        "width": width,
        "columns": columns,
    }
    if not force:
        hit = cached_review(destination, request)
        if hit is not None:
            return {**hit, "cache_hit": True}
    index = catalog(source)
    if index["sha256"] != request["sha256"]:
        raise ValueError("source changed during review; wait for the render to finish")
    frames = frames_at_times(index["frames"], times)
    destination.mkdir(parents=True, exist_ok=True)
    names, pages, page = [], [], []
    with tempfile.TemporaryDirectory(prefix=".review-", dir=destination) as temp:
        stage = Path(temp)

        # keep only one page of thumbnails in memory while preserving native PNGs
        def flush():
            height = page[0][1].height
            canvas = Image.new(
                "RGB",
                (columns * width, math.ceil(len(page) / columns) * (height + 24)),
                "#151515",
            )
            draw = ImageDraw.Draw(canvas)
            for i, (number, image) in enumerate(page):
                x, y = i % columns * width, i // columns * (height + 24)
                canvas.paste(image, (x, y + 24))
                draw.text(
                    (x + 3, y + 4),
                    f'f{number}  PTS {index["frames"][number]["pts"]:.6f}s',
                    fill="white",
                )
                image.close()
            name = f"sheet_{len(pages) + 1:03}.jpg"
            canvas.save(stage / name, quality=92)
            canvas.close()
            names.append(name)
            pages.append(name)
            page.clear()

        for number, image in selected_frames(source, frames):
            name = f"frame_{number:06}.png"
            image.save(stage / name)
            names.append(name)
            thumb = image.resize(
                (width, max(1, round(image.height * width / image.width))),
                Image.Resampling.LANCZOS,
            )
            image.close()
            page.append((number, thumb))
            if len(page) == columns * 6:
                flush()
        if page:
            flush()
        expected = {f"frame_{number:06}.png" for number in frames}
        if not expected.issubset(names):
            raise ValueError("decoder did not produce every requested frame")
        if sha256(source) != request["sha256"]:
            raise ValueError(
                "source changed during review; previous evidence was retained"
            )
        report = {
            "request": request,
            "frames": [index["frames"][n] for n in frames],
            "sheets": pages,
            "artifacts": {n: sha256(stage / n) for n in names},
            "visual_review": "pending; inspect native PNGs and moving output",
            "cache_hit": False,
        }
        save_json(stage / "review.json", report)
        # reject redirected output paths before publishing any artifact
        for name in [*names, "review.json"]:
            target = destination / name
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise ValueError(f"refusing unsafe review output: {target}")
        # each file is atomic; publish the hash manifest last so partial sets never hit cache
        for name in [*names, "review.json"]:
            os.replace(stage / name, destination / name)
    return report


# build paginated thumbnails with labels tied to decoded source frames
def build(source, out, frames=None, every=1, width=320, columns=5):
    index = catalog(source)
    rows = index["frames"]
    if every <= 0 or width < 32 or columns < 1:
        raise ValueError("invalid sheet geometry or sample interval")
    if frames is None:
        frames = []
        next_time = rows[0]["pts"]
        for row in rows:
            if row["pts"] >= next_time:
                frames.append(row["frame"])
                next_time = row["pts"] + every
    if not frames or min(frames) < 0 or max(frames) >= len(rows):
        raise ValueError("selected frame is outside source")
    out = Path(out)
    if out.resolve() == Path(source).resolve():
        raise ValueError("sheet cannot overwrite source")
    out.parent.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    page = []
    paths = []
    page_size = columns * 6

    # write one page without retaining thumbnails for the whole video
    def flush(items):
        h = items[0][1].height
        sheet = Image.new(
            "RGB",
            (columns * width, math.ceil(len(items) / columns) * (h + 24)),
            "#151515",
        )
        draw = ImageDraw.Draw(sheet)
        for i, (frame, im) in enumerate(items):
            x = i % columns * width
            y = i // columns * (h + 24)
            sheet.paste(im, (x, y + 24))
            draw.text(
                (x + 3, y + 4),
                f'f{frame}  PTS {rows[frame]["pts"]:.6f}s',
                font=font,
                fill="white",
            )
        target = (
            out
            if not paths
            else out.with_name(f"{out.stem}_{len(paths)+1:03}{out.suffix}")
        )
        sheet.save(target)
        paths.append(str(target))

    for item in selected_frames(source, frames, width):
        page.append(item)
        if len(page) == page_size:
            flush(page)
            page = []
    if page:
        flush(page)
    result = {
        "source_sha256": index["sha256"],
        "frames": [rows[f] for f in sorted(set(frames))],
        "sheets": paths,
        "visual_review": "pending",
    }
    save_json(str(out) + ".json", result)
    return result


# expose native indices or critical times through mutually exclusive CLI modes
def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source")
    p.add_argument(
        "-o",
        "--out",
        required=True,
        help="Sheet file, or review directory with --times",
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument("--frames", type=int, nargs="+")
    group.add_argument(
        "--times",
        type=float,
        nargs="+",
        help="Critical seconds: native PNGs plus cached paginated sheets",
    )
    p.add_argument("--every", type=float, default=1)
    p.add_argument("--width", type=int, default=320)
    p.add_argument("--columns", type=int, default=5)
    p.add_argument(
        "--force",
        action="store_true",
        help="Rebuild a --times review even if unchanged",
    )
    a = p.parse_args()
    if a.force and a.times is None:
        p.error("--force requires --times")
    try:
        result = (
            review(
                a.source,
                a.out,
                a.times,
                width=a.width,
                columns=a.columns,
                force=a.force,
            )
            if a.times is not None
            else build(a.source, a.out, a.frames, a.every, a.width, a.columns)
        )
    except (ValueError, OSError, RuntimeError) as exc:
        p.exit(1, f"sheet: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
