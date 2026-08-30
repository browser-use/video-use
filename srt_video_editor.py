"""srt_video_editor — minimal viable, learning-grade scaffold.

================================================================
THIS IS NOT THE PRODUCTION ENTRY POINT. Use `python main.py` (or
`python helpers/srt_driven_edit.py` directly) for any real work.
================================================================

What this script does:
  - reads script.srt + edit_plan.json
  - validates ids match
  - prints the planned mapping
  - cuts each cue out of source.mp4 to temp/clip_<id:03d>.mp4
  - lossless-concats into output/final.mp4

What it deliberately DOES NOT do (use main.py / srt_driven_edit.py
for any of these):
  - encoding fallback — only UTF-8 / UTF-8-with-BOM SRT is accepted;
    GB18030 / cp936 input will crash
  - source range bounds check — a plan that overruns the source's
    duration will surface as a confusing ffmpeg error, not a clear
    "id=X exceeds source duration" up front
  - QC report — no per-clip drift, no disk-usage accounting, no
    structured failure record
  - overwrite protection — every run silently `-y` overwrites the
    temp/ clips and output/final.mp4
  - audio fades at cut points (you may hear pops on hard cuts)
  - voice replacement, subtitle burn, color grade, HDR tone-map,
    sync tails, segment cache, batch / per-episode discovery

Self-contained on purpose: no imports from helpers/, so the entire
flow fits in one readable file. Use this to learn the pipeline; ship
with main.py.

Usage:
    python srt_video_editor.py
    python srt_video_editor.py --srt input/script.srt --plan input/edit_plan.json \\
                               --source input/source.mp4 \\
                               --temp-dir temp/ --output output/final.mp4
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


# ---------- timestamp helpers ----------

_TS_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def parse_ts(s: str) -> float:
    """Parse 'HH:MM:SS,ms' or 'HH:MM:SS.ms' to seconds."""
    m = _TS_RE.fullmatch(s.strip())
    if not m:
        raise ValueError(f"bad timestamp: {s!r}")
    h, mn, sec, ms = m.groups()
    return int(h) * 3600 + int(mn) * 60 + int(sec) + int(ms.ljust(3, "0")) / 1000.0


def format_ts(seconds: float) -> str:
    """SRT-style HH:MM:SS,ms — comma separator (for log output / errors)."""
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_ts_dot(seconds: float) -> str:
    """ffmpeg-style HH:MM:SS.ms — dot separator. Used for `-ss` / `-t` args.

    SRT timestamps use a comma between seconds and milliseconds; ffmpeg
    expects a dot. The two forms refer to the same point in time but the
    comma form is rejected by ffmpeg's parser.
    """
    return format_ts(seconds).replace(",", ".")


# ---------- parsers ----------


def parse_srt(path: Path) -> list[dict]:
    """Return a list of {id, start, end, text} in file order.

    Tolerates UTF-8 with or without BOM, CRLF / LF line endings, and
    SRT cue settings ('position:90% align:start') trailing the time line.

    Per-cue duration is validated here: `end <= start` is rejected with
    an id-pinned error so the downstream ffmpeg call never sees a
    non-positive `-t` argument.
    """
    raw = path.read_text(encoding="utf-8-sig")
    cues: list[dict] = []
    for block in re.split(r"\r?\n\r?\n+", raw.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        try:
            cid = int(lines[0].strip())
        except ValueError:
            raise SystemExit(f"SRT id line is not an integer: {lines[0]!r}")
        if "-->" not in lines[1]:
            raise SystemExit(f"SRT block missing '-->' time line: {lines[1]!r}")
        left, right = lines[1].split("-->", 1)
        start = parse_ts(left.strip().split()[-1])
        end = parse_ts(right.strip().split()[0])
        if end <= start:
            raise SystemExit(
                f"SRT id={cid}: end {format_ts(end)} <= start "
                f"{format_ts(start)} (srt_duration {end - start:.3f}s). "
                f"Fix the timestamp in {path}."
            )
        text = "\n".join(lines[2:])
        cues.append({"id": cid, "start": start, "end": end, "text": text})
    if not cues:
        raise SystemExit(f"SRT has no cues: {path}")
    return cues


def parse_plan(path: Path) -> list[dict]:
    """Return a list of {id, source_start, source_end}. Only Form A is
    accepted here (a flat JSON array); Form B is out of scope for the
    minimal version.

    JSON syntax errors are reported as a SystemExit with the file path
    plus the offending line / column / message, rather than as a bare
    JSONDecodeError traceback.
    """
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"edit_plan is not valid JSON: {path}: "
            f"line {e.lineno} col {e.colno}: {e.msg}"
        )
    if not isinstance(data, list):
        raise SystemExit(
            "edit_plan.json must be a JSON array of "
            "{id, source_start, source_end} objects (Form A)."
        )
    out: list[dict] = []
    for row in data:
        try:
            out.append({
                "id": int(row["id"]),
                "source_start": parse_ts(row["source_start"]),
                "source_end": parse_ts(row["source_end"]),
            })
        except (KeyError, ValueError) as e:
            raise SystemExit(f"plan row {row!r}: {e}")
    return out


# ---------- validation ----------


def validate_ids(cues: list[dict], plan: list[dict]) -> None:
    """Each id must appear exactly once in both sides, and the two id sets
    must be equal. Any deviation is a hard failure with a clear message.
    """
    cue_ids = [c["id"] for c in cues]
    plan_ids = [p["id"] for p in plan]

    dup_cue = {i for i in cue_ids if cue_ids.count(i) > 1}
    if dup_cue:
        raise SystemExit(f"SRT has duplicate ids: {sorted(dup_cue)}")
    dup_plan = {i for i in plan_ids if plan_ids.count(i) > 1}
    if dup_plan:
        raise SystemExit(f"edit_plan has duplicate ids: {sorted(dup_plan)}")

    only_srt = set(cue_ids) - set(plan_ids)
    only_plan = set(plan_ids) - set(cue_ids)
    if only_srt or only_plan:
        msg = []
        if only_srt:
            msg.append(f"in SRT but missing in plan: {sorted(only_srt)}")
        if only_plan:
            msg.append(f"in plan but missing in SRT: {sorted(only_plan)}")
        raise SystemExit("id mismatch: " + "; ".join(msg))


# ---------- report ----------


def probe_clip_duration(path: Path) -> float | None:
    """Return the duration of `path` in seconds via ffprobe.

    Returns None if ffprobe is missing or the file is unreadable —
    verification is informational, so probe failures should not abort
    the run after a successful extraction.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def cut_clip(source: Path, start: float, cut_duration: float,
             out_path: Path) -> None:
    """Cut `cut_duration` seconds starting at `start` from source.

    `-ss` placed before `-i` makes ffmpeg do a fast container-level seek
    to the nearest keyframe, then libx264 re-encodes from there —
    frame-accurate at the cost of one encode pass. Stream copy (`-c copy`)
    would be faster but cuts at keyframes only, which makes downstream
    concat / sync less predictable; we trade a few seconds of encode
    time per clip for cleaner cut boundaries.

    Audio is mapped optionally via `-map 0:a?` so a video-only source
    does not crash the run. Video is the first stream (`-map 0:v:0`).

    Raises SystemExit with the full ffmpeg command + stderr on failure
    so the caller never has to scroll the terminal to find what went wrong.
    """
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-ss", format_ts_dot(start),
        "-i", str(source),
        "-t", format_ts_dot(cut_duration),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(out_path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        raise SystemExit(
            "ffmpeg not found on PATH. Install ffmpeg "
            "(`winget install Gyan.FFmpeg` on Windows, "
            "`brew install ffmpeg` on macOS) and re-run."
        )
    if proc.returncode != 0:
        raise SystemExit(
            f"ffmpeg failed on {out_path.name} (exit {proc.returncode})\n"
            f"--- command ---\n{' '.join(cmd)}\n"
            f"--- stderr ---\n{proc.stderr or '(empty)'}"
        )


def extract_clips(
    cues: list[dict],
    plan: list[dict],
    source: Path,
    temp_dir: Path,
) -> list[Path]:
    """Cut one clip per cue. Returns the list of output paths in cue-id order.

    Per-cue duration logic:
      source_duration = plan.source_end - plan.source_start
      srt_duration    = cue.end - cue.start

      source_duration <= 0           -> hard error pointing at the id
      source_duration <  srt_duration -> hard error (source is too short
                                        to cover the SRT cue; either
                                        extend the source range or
                                        shorten the cue)
      source_duration >= srt_duration -> cut exactly `srt_duration`
                                        starting at source_start. Any
                                        extra source tail is discarded.

    Stale `clip_*.mp4` files in `temp_dir` are removed before cutting so
    a previous failed run with sparser ids doesn't leave misleading
    leftovers next to the new clips. The `_concat.txt` from a future
    concat step is NOT touched here — concat owns its own list file.

    Filenames are `clip_<id:03d>.mp4`, indexed by SRT id (not position).
    """
    plan_by_id = {p["id"]: p for p in plan}
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Pre-clean stale clip files. Only the clip_*.mp4 pattern so user-
    # created neighbours (notes, recordings, etc.) are left alone.
    stale = sorted(temp_dir.glob("clip_*.mp4"))
    if stale:
        print(f"clearing {len(stale)} stale clip(s) from {temp_dir}/")
        for p in stale:
            p.unlink()

    print()
    print(f"cutting {len(cues)} clip(s) -> {temp_dir}/")
    outputs: list[Path] = []
    targets: list[float] = []  # parallel to outputs — used by post-verify pass
    for cue in sorted(cues, key=lambda c: c["id"]):
        cid = cue["id"]
        p = plan_by_id[cid]
        start = p["source_start"]
        source_duration = p["source_end"] - start
        srt_duration = cue["end"] - cue["start"]

        if source_duration <= 0:
            raise SystemExit(
                f"plan id={cid}: source_end {format_ts(p['source_end'])} <= "
                f"source_start {format_ts(start)} "
                f"(source_duration {source_duration:.3f}s)"
            )
        if source_duration < srt_duration - 1e-6:
            raise SystemExit(
                f"plan id={cid}: source range is shorter than SRT cue. "
                f"source_duration={source_duration:.3f}s, "
                f"srt_duration={srt_duration:.3f}s. "
                f"Extend the source range or shorten the SRT cue."
            )
        # source_duration >= srt_duration: cut exactly srt_duration
        cut_duration = srt_duration

        out_path = temp_dir / f"clip_{cid:03d}.mp4"
        text_preview = cue["text"].replace("\n", " ").strip()
        if len(text_preview) > 60:
            text_preview = text_preview[:57] + "..."
        print(
            f"  id={cid:>3}  src@{format_ts(start)}  "
            f"cut={cut_duration:.3f}s  -> {out_path}\n"
            f"        text: {text_preview!r}"
        )
        cut_clip(source, start, cut_duration, out_path)
        outputs.append(out_path)
        targets.append(cut_duration)

    # ---- ffprobe verification ----
    # Container duration can drift a few hundredths of a second from the
    # target after re-encoding (libx264 GOP / first-keyframe boundary).
    # Print the actual vs target side by side so the user can spot a
    # clip that's wildly off — e.g. ffmpeg silently truncated to 0s.
    print()
    print(f"verifying {len(outputs)} clip(s) with ffprobe:")
    for out_path, target in zip(outputs, targets):
        actual = probe_clip_duration(out_path)
        if actual is None:
            print(f"  {out_path.name}: (probe failed)，target: {target:.2f}s")
        else:
            print(f"  {out_path.name}: {actual:.2f}s，target: {target:.2f}s")

    return outputs


def concat_clips(clip_paths: list[Path], out_path: Path) -> None:
    """Lossless concat of pre-encoded clips via ffmpeg's concat demuxer.

    The clips produced by `cut_clip` all share the same encoder params
    (libx264, yuv420p, aac), so `-c copy` is safe and instant — no
    re-encode. The concat list file is written next to the first clip
    (typically `temp/_concat.txt`) and removed in `finally` so a clean
    run leaves a tidy temp/ and a failed run doesn't leave a stale list.

    Raises SystemExit with the full ffmpeg command + stderr on failure.
    """
    if not clip_paths:
        raise SystemExit("concat: no clips to concatenate")

    list_file = clip_paths[0].parent / "_concat.txt"
    list_file.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in clip_paths),
        encoding="utf-8",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]
    try:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
        except FileNotFoundError:
            raise SystemExit(
                "ffmpeg not found on PATH. Install ffmpeg "
                "(`winget install Gyan.FFmpeg` on Windows, "
                "`brew install ffmpeg` on macOS) and re-run."
            )
        if proc.returncode != 0:
            raise SystemExit(
                f"ffmpeg concat failed (exit {proc.returncode})\n"
                f"--- command ---\n{' '.join(cmd)}\n"
                f"--- stderr ---\n{proc.stderr or '(empty)'}"
            )
    finally:
        list_file.unlink(missing_ok=True)
    print(f"  concat {len(clip_paths)} clip(s) -> {out_path}")


def print_report(cues: list[dict], plan: list[dict]) -> None:
    plan_by_id = {p["id"]: p for p in plan}
    print(f"{len(cues)} cue(s), all ids matched.")
    print()
    header = f"  {'ID':>3}  {'OUTPUT (cue)':<23}  {'SOURCE (planned)':<23}  TEXT"
    print(header)
    print(f"  {'-' * 3}  {'-' * 23}  {'-' * 23}  {'-' * 4}")
    for cue in sorted(cues, key=lambda c: c["id"]):
        p = plan_by_id[cue["id"]]
        out_range = f"{format_ts(cue['start'])} -> {format_ts(cue['end'])}"
        src_range = f"{format_ts(p['source_start'])} -> {format_ts(p['source_end'])}"
        preview = cue["text"].replace("\n", " ")
        if len(preview) > 50:
            preview = preview[:47] + "..."
        print(f"  {cue['id']:>3}  {out_range:<23}  {src_range:<23}  {preview}")


# ---------- entry ----------


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "MINIMAL learning-grade SRT-driven editor. NOT for production — "
            "use `python main.py` for that. This script reads script.srt + "
            "edit_plan.json, validates id matching, prints the planned range "
            "table, cuts each cue out of source.mp4 into temp/clip_<id>.mp4, "
            "then lossless-concats them into output/final.mp4. No encoding "
            "fallback, no range-bounds check, no QC report, no overwrite "
            "protection."
        ),
    )
    ap.add_argument("--srt", type=Path, default=Path("input/script.srt"))
    ap.add_argument("--plan", type=Path, default=Path("input/edit_plan.json"))
    ap.add_argument("--source", type=Path, default=Path("input/source.mp4"))
    ap.add_argument("--temp-dir", type=Path, default=Path("temp"))
    ap.add_argument("--output", type=Path, default=Path("output/final.mp4"))
    args = ap.parse_args()

    print(
        "[srt_video_editor: minimal mode — UTF-8 SRT only, no range/QC "
        "checks, temp/ + output/ will be overwritten. For production "
        "use `python main.py`.]"
    )

    for p in (args.srt, args.plan, args.source):
        if not p.is_file():
            raise SystemExit(f"file not found: {p}")

    cues = parse_srt(args.srt)
    plan = parse_plan(args.plan)
    validate_ids(cues, plan)
    print_report(cues, plan)
    clip_paths = extract_clips(cues, plan, args.source, args.temp_dir)
    print()
    print(f"concatenating -> {args.output}")
    concat_clips(clip_paths, args.output)
    print()
    print(f"done. final video: {args.output}")


if __name__ == "__main__":
    main()
