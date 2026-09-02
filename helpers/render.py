"""Render a video from an EDL.

Implements the HEURISTICS render pipeline in the correct order:

  1. Per-segment extract with color grade + 30ms audio fades baked in
  2. Lossless -c copy concat into base.mp4
  3. If overlays or subtitles: single filter graph that overlays animations
     (with PTS shift so frame 0 lands at the overlay window start)
     and applies `subtitles` filter LAST → final.mp4

Optionally builds a master SRT from the per-source transcripts + EDL
output-timeline offsets, applies the proven force_style (2-word
UPPERCASE chunks, Helvetica 18 Bold, MarginV=35).

Usage:
    python helpers/render.py <edl.json> -o final.mp4
    python helpers/render.py <edl.json> -o preview.mp4 --preview
    python helpers/render.py <edl.json> -o final.mp4 --build-subtitles
    python helpers/render.py <edl.json> -o final.mp4 --no-subtitles
    python helpers/render.py <edl.json> --all-deliverables
    python helpers/render.py <edl.json> -o overlay_preflight.png \
      --preflight-overlays --preflight-base base.mp4
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

HELPERS_DIR = str(Path(__file__).resolve().parent)
if HELPERS_DIR not in sys.path:
    sys.path.insert(0, HELPERS_DIR)

from edl import EDLValidationError, normalize_deliverables, validate_edl

try:
    from grade import get_preset, auto_grade_for_clip  # same directory
except Exception:
    # fallback preset lookup that returns no grade when the grade module is unavailable
    def get_preset(name: str) -> str:
        return ""

    # fallback auto grade that returns a mild fixed correction when the grade module is unavailable
    def auto_grade_for_clip(video, start=0.0, duration=None, verbose=False):  # type: ignore
        return "eq=contrast=1.03:saturation=0.98", {}


# -------- Subtitle style (bold-overlay, proven at 1920×1080 and 1080×1920) --
#
# MarginV is NOT taste — it is a platform safe-zone rule.
# TikTok / IG Reels / Shorts UI (caption, username, music, right-rail actions)
# covers roughly the bottom ~25–30% of a 1080×1920 frame. Captions placed near
# the bottom edge get clipped or obscured by the UI. libass auto-scales the
# render canvas relative to PlayResY=288, so MarginV=90 lands the caption
# baseline roughly 30% up from the bottom on any aspect — clear of the UI on
# every major vertical-video platform. Do not drop this below ~75 without a
# specific reason.
SUB_FORCE_STYLE = (
    "FontName=Helvetica,FontSize=18,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
    "BorderStyle=1,Outline=2,Shadow=0,"
    "Alignment=2,MarginV=90"
)

# -------- Helpers ------------------------------------------------------------


# run a command with check and print an abbreviated version unless quiet
def run(cmd: list[str], quiet: bool = False) -> None:
    if not quiet:
        print(f"  $ {' '.join(str(c) for c in cmd[:6])}{' …' if len(cmd) > 6 else ''}")
    subprocess.run(cmd, check=True)


# turn the edl grade field into a filter string or the auto sentinel
def resolve_grade_filter(grade_field: str | None) -> str:
    """The EDL's 'grade' field can be a preset name, a raw ffmpeg filter, or 'auto'.

    Returns the filter string to embed into the per-segment -vf chain.
    For 'auto', returns the sentinel "__AUTO__" which is resolved per-segment.
    """
    if not grade_field:
        return ""
    if grade_field == "auto":
        return "__AUTO__"
    # Preset names are short identifiers, filter strings contain '=' or ','.
    if re.fullmatch(r"[a-zA-Z0-9_\-]+", grade_field):
        try:
            return get_preset(grade_field)
        except KeyError:
            print(f"warning: unknown preset '{grade_field}', using as raw filter")
            return grade_field
    return grade_field


# resolve a path relative to base unless it is already absolute
def resolve_path(maybe_path: str, base: Path) -> Path:
    """Resolve a path that may be absolute or relative to `base`."""
    p = Path(maybe_path)
    if p.is_absolute():
        return p
    return (base / p).resolve()


# ask ffprobe for the width and height of the first video stream
def probe_video_size(video: Path) -> tuple[int, int]:
    """Return the first video stream's display size as ``(width, height)``."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "json", str(video),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        streams = json.loads(out.stdout).get("streams") or []
        if not streams:
            raise ValueError("no video stream")
        return int(streams[0]["width"]), int(streams[0]["height"])
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, ValueError) as exc:
        raise ValueError(f"could not probe video size: {video}") from exc


# find an ffmpeg binary whose filter list includes subtitles so libass is available
def ffmpeg_with_subtitles() -> str:
    """Find an ffmpeg build with libass, including Homebrew's keg-only build."""
    candidates = [
        shutil.which("ffmpeg"),
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
    ]
    # dedupe candidates while preserving order and skip empty entries
    for candidate in dict.fromkeys(item for item in candidates if item):
        probe = subprocess.run(
            [str(candidate), "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
        )
        # the filters listing names subtitles only when libass is compiled in
        if re.search(r"\bsubtitles\b", probe.stdout + probe.stderr):
            return str(candidate)
    raise RuntimeError(
        "subtitles require an ffmpeg build with libass; install ffmpeg-full or "
        "another libass-enabled build"
    )


# -------- HDR → SDR tone mapping (HLG / PQ sources) --------------------------
#
# iPhone defaults to HLG HDR in Rec.2020 (and many mirrorless cameras ship PQ).
# If the source is HDR and we only downconvert bit depth (yuv420p10le → yuv420p)
# without tone-mapping, the output is 8-bit but still carries HLG/PQ transfer
# metadata. Players that honor the metadata (screen recorders, most social
# upload re-encodes) interpret 8-bit values in an HDR container and the result
# looks oversaturated / blown out. QuickTime on macOS can hide this locally —
# screen recording and uploaded renders cannot.
#
# Fix: detect HDR via color_transfer and prepend a zscale+tonemap chain to the
# vf graph so the output is clean Rec.709 SDR.

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ (HDR10) and HLG

TONEMAP_CHAIN = (
    "zscale=t=linear:npl=100,"
    "format=gbrpf32le,"
    "zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,"
    "format=yuv420p"
)


# detect pq or hlg transfer metadata on the first video stream
def is_hdr_source(video: Path) -> bool:
    """Return True if the source uses a PQ or HLG transfer function."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=color_transfer",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip() in HDR_TRANSFERS
    except subprocess.CalledProcessError:
        return False


# report whether the first video stream is taller than it is wide
def is_portrait_source(video: Path) -> bool:
    """Return True if the displayed video is portrait, including rotation."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries",
             "stream=width,height:stream_side_data=rotation",
             "-of", "json", str(video)],
            capture_output=True, text=True, check=True,
        )
        streams = json.loads(out.stdout).get("streams") or []
        if not streams:
            return False
        stream = streams[0]
        w, h = int(stream["width"]), int(stream["height"])

        # ffmpeg autorotates display-matrix side data before applying filters.
        # Swap coded dimensions for quarter-turns so the scale axis is selected
        # from the dimensions the filter actually sees. A plain metadata tag is
        # intentionally ignored because it does not guarantee autorotation.
        rotation = 0
        for side_data in stream.get("side_data_list") or []:
            if side_data.get("rotation") is not None:
                rotation = side_data["rotation"]
                break
        if int(round(float(rotation))) % 360 in (90, 270):
            w, h = h, w
        return h > w
    except (
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        OSError,
        OverflowError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return False


# validate an fps argument and return it as a reduced rational string
def parse_fps(value: str) -> str:
    """Validate and canonicalize an ffmpeg frame rate."""
    text = value.strip()
    if len(text) > 32 or not re.fullmatch(
        r"(?:[0-9]+(?:\.[0-9]+)?|[0-9]+/[0-9]+)", text
    ):
        raise argparse.ArgumentTypeError(
            "FPS must be a positive number or rational, e.g. 30 or 30000/1001"
        )
    try:
        rate = Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise argparse.ArgumentTypeError(
            "FPS must be a positive number or rational, e.g. 30 or 30000/1001"
        ) from exc
    if rate <= 0:
        raise argparse.ArgumentTypeError("FPS must be greater than zero")
    # FFmpeg stores video rates as AVRational (signed 32-bit components).
    # Bounding the reduced fraction keeps every accepted canonical value safe
    # for ffmpeg and makes parse_fps(parse_fps(value)) idempotent.
    max_component = 2_147_483_647
    if rate.numerator > max_component or rate.denominator > max_component:
        raise argparse.ArgumentTypeError("FPS precision or magnitude is too large")
    return f"{rate.numerator}/{rate.denominator}"


# read the source frame rate from ffprobe preferring the average rate
def probe_source_fps(video: Path) -> str | None:
    """Return an ffmpeg-ready source rate, preferring the average frame rate.

    ``avg_frame_rate`` represents the observed average and is the better default
    for variable-frame-rate inputs. ``r_frame_rate`` remains a fallback for
    streams where the average is unavailable. Values are normalized to an exact
    rational so rates such as ``30000/1001`` survive without rounding.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate,r_frame_rate",
             "-of", "json", str(video)],
            capture_output=True, text=True, check=True,
        )
        streams = json.loads(out.stdout).get("streams") or []
        if not streams:
            return None
        # take the first usable rate and skip zero or unparsable values
        for field in ("avg_frame_rate", "r_frame_rate"):
            value = streams[0].get(field)
            if value and value != "0/0":
                try:
                    return parse_fps(value)
                except argparse.ArgumentTypeError:
                    continue
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return None
    return None


# -------- Per-segment extraction (Rule 2 + Rule 3) --------------------------


# encode one edl range as its own mp4 with tone mapping scaling grade and audio fades applied
def extract_segment(
    source: Path,
    seg_start: float,
    duration: float,
    grade_filter: str,
    out_path: Path,
    preview: bool = False,
    draft: bool = False,
    rate: str | None = None,
) -> None:
    """Extract a cut range as its own MP4 with grade + 30ms audio fades baked in.

    `-ss` before `-i` for fast accurate seeking. Scale to 1080p from 4K.
    Portrait sources (height > width) are scaled by height to preserve orientation.

    Quality ladder:
      - final (default): 1080p libx264 fast CRF 20
      - preview:         1080p libx264 medium CRF 22 (evaluable for QC)
      - draft:           720p libx264 ultrafast CRF 28 (cut-point check only)
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # scale by height for portrait sources so orientation is preserved
    portrait = is_portrait_source(source)
    if draft:
        scale = "scale=-2:1280" if portrait else "scale=1280:-2"
    else:
        scale = "scale=-2:1920" if portrait else "scale=1920:-2"

    # tone map hdr first then scale then grade
    vf_parts: list[str] = []
    if is_hdr_source(source):
        vf_parts.append(TONEMAP_CHAIN)
    vf_parts.append(scale)
    if grade_filter:
        vf_parts.append(grade_filter)
    vf = ",".join(vf_parts)

    # 30ms audio fades at both edges (Rule 3) — prevent pops
    fade_out_start = max(0.0, duration - 0.03)
    af = f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out_start:.3f}:d=0.03"

    if draft:
        preset, crf = "ultrafast", "28"
    elif preview:
        preset, crf = "medium", "22"
    else:
        preset, crf = "fast", "20"

    # Frame rate: use the rate the caller resolved once for the whole render
    # (every segment must share it — concat -c copy in Rule 2 requires a uniform
    # frame rate). When called standalone with no rate, preserve this source's
    # own rate; fall back to 24 only if it can't be probed.
    out_rate = rate if rate is not None else (probe_source_fps(source) or "24")

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{seg_start:.3f}",
        "-i", str(source),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-r", out_rate,
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


# extract every edl range into graded segment files sharing one output frame rate
def extract_all_segments(
    edl: dict,
    edit_dir: Path,
    preview: bool,
    draft: bool = False,
    fps: str | None = None,
) -> list[Path]:
    """Extract every EDL range into edit_dir/clips_graded/seg_NN.mp4.
    Returns the ordered list of segment paths.

    If the EDL `grade` is "auto", analyze each segment range with
    `auto_grade_for_clip` and apply a per-segment subtle correction.
    Otherwise, apply the same preset/raw filter to every segment.
    """
    resolved = resolve_grade_filter(edl.get("grade"))
    is_auto = resolved == "__AUTO__"
    clips_dir = edit_dir / (
        "clips_draft" if draft else ("clips_preview" if preview else "clips_graded")
    )
    clips_dir.mkdir(parents=True, exist_ok=True)

    ranges = edl["ranges"]
    sources = edl["sources"]

    # Resolve ONE output frame rate for the entire render and apply it to every
    # segment. The lossless concat (Rule 2, `-c copy`) requires all segments to
    # share a frame rate; probing per-segment would diverge for multi-source
    # EDLs that mix rates (e.g. a 30fps and a 60fps source) and break the concat.
    # Explicit --fps wins; otherwise preserve the first source's rate.
    if fps is not None:
        out_rate = parse_fps(str(fps))
    elif ranges:
        first_src = resolve_path(sources[ranges[0]["source"]], edit_dir)
        out_rate = probe_source_fps(first_src) or "24"
    else:
        out_rate = "24"

    seg_paths: list[Path] = []
    print(f"extracting {len(ranges)} segment(s) → {clips_dir.name}/  @ {out_rate} fps"
          f"{' (forced)' if fps is not None else ' (from source)'}")
    if is_auto:
        print("  (auto-grade per segment: analyzing each range)")
    for i, r in enumerate(ranges):
        src_name = r["source"]
        src_path = resolve_path(sources[src_name], edit_dir)
        start = float(r["start"])
        end = float(r["end"])
        duration = end - start
        out_path = clips_dir / f"seg_{i:02d}_{src_name}.mp4"

        if is_auto:
            seg_filter, _stats = auto_grade_for_clip(src_path, start=start, duration=duration, verbose=False)
        else:
            seg_filter = resolved

        note = r.get("beat") or r.get("note") or ""
        print(f"  [{i:02d}] {src_name}  {start:7.2f}-{end:7.2f}  ({duration:5.2f}s)  {note}")
        if is_auto:
            print(f"        grade: {seg_filter or '(none)'}")
        extract_segment(src_path, start, duration, seg_filter, out_path, preview=preview, draft=draft, rate=out_rate)
        seg_paths.append(out_path)

    return seg_paths


# -------- Lossless concat ----------------------------------------------------


# join segment files losslessly with the concat demuxer
def concat_segments(segment_paths: list[Path], out_path: Path, edit_dir: Path) -> None:
    """Lossless concat via the concat demuxer. No re-encode."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list = edit_dir / "_concat.txt"
    # the concat demuxer reads a text file listing each segment path
    concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in segment_paths))

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]
    print(f"concat → {out_path.name}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    concat_list.unlink(missing_ok=True)


# -------- Master SRT (Rule 5) ------------------------------------------------


PUNCT_BREAK = set(".,!?;:")


# format seconds as an srt timestamp with millisecond precision
def _srt_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# return transcript words that overlap the given source time range
def _words_in_range(transcript: dict, t_start: float, t_end: float) -> list[dict]:
    out: list[dict] = []
    for w in transcript.get("words", []):
        if w.get("type") != "word":
            continue
        ws = w.get("start")
        we = w.get("end")
        if ws is None or we is None:
            continue
        # keep only words that overlap the range
        if we <= t_start or ws >= t_end:
            continue
        out.append(w)
    return out


# build an output timeline srt from per source transcripts using two word uppercase chunks
def build_master_srt(edl: dict, edit_dir: Path, out_path: Path) -> None:
    """Build an output-timeline SRT from per-source transcripts.

    - 2-word chunks (break on any punctuation in between)
    - UPPERCASE text
    - Output times computed as word.start - segment_start + segment_offset
    """
    transcripts_dir = edit_dir / "transcripts"
    sources = edl["sources"]

    entries: list[tuple[float, float, str]] = []
    seg_offset = 0.0

    for r in edl["ranges"]:
        src_name = r["source"]
        seg_start = float(r["start"])
        seg_end = float(r["end"])
        seg_duration = seg_end - seg_start

        tr_path = transcripts_dir / f"{src_name}.json"
        if not tr_path.exists():
            print(f"  no transcript for {src_name}, skipping captions for this segment")
            seg_offset += seg_duration
            continue

        transcript = json.loads(tr_path.read_text())
        words_in_seg = _words_in_range(transcript, seg_start, seg_end)

        # Group into 2-word chunks, break on punctuation
        chunks: list[list[dict]] = []
        current: list[dict] = []
        for w in words_in_seg:
            text = (w.get("text") or "").strip()
            if not text:
                continue
            current.append(w)
            # Break if the current text ends in punctuation or we hit 2 words
            ends_in_punct = bool(text) and text[-1] in PUNCT_BREAK
            if len(current) >= 2 or ends_in_punct:
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)

        for chunk in chunks:
            # clamp chunk times to the segment then shift them onto the output timeline
            local_start = max(seg_start, chunk[0].get("start", seg_start))
            local_end = min(seg_end, chunk[-1].get("end", seg_end))
            out_start = max(0.0, local_start - seg_start) + seg_offset
            out_end = max(0.0, local_end - seg_start) + seg_offset
            if out_end <= out_start:
                out_end = out_start + 0.4
            text = " ".join((w.get("text") or "").strip() for w in chunk)
            text = re.sub(r"\s+", " ", text).strip()
            # Strip trailing punctuation for cleaner uppercase look
            text = text.rstrip(",;:")
            text = text.upper()
            entries.append((out_start, out_end, text))

        seg_offset += seg_duration

    # Sort and write as SRT
    entries.sort(key=lambda e: e[0])
    lines: list[str] = []
    for i, (a, b, t) in enumerate(entries, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(a)} --> {_srt_timestamp(b)}")
        lines.append(t)
        lines.append("")
    out_path.write_text("\n".join(lines))
    print(f"master SRT → {out_path.name} ({len(entries)} cues)")


# -------- Loudness normalization (social-ready audio) -----------------------


# Social-media standard: -14 LUFS integrated, -1 dBTP peak, LRA 11 LU.
# Matches YouTube / Instagram / TikTok / X / LinkedIn normalization targets.
LOUDNORM_I = -14.0
LOUDNORM_TP = -1.0
LOUDNORM_LRA = 11.0


# run the loudnorm first pass and parse its json measurement from stderr
def measure_loudness(
    video_path: Path,
    integrated_lufs: float = LOUDNORM_I,
    true_peak_dbtp: float = LOUDNORM_TP,
    lra: float = LOUDNORM_LRA,
) -> dict[str, str] | None:
    """Run ffmpeg loudnorm first pass and parse the JSON measurement.

    Returns a dict with measured_i, measured_tp, measured_lra, measured_thresh,
    target_offset, or None if measurement failed.
    """
    filter_str = (
        f"loudnorm=I={integrated_lufs}:TP={true_peak_dbtp}:LRA={lra}:print_format=json"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-i", str(video_path),
        "-af", filter_str,
        "-vn", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # loudnorm prints the JSON to stderr at the end of the run
    stderr = proc.stderr

    # Find the JSON block — loudnorm output contains a `{ ... }` block
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(stderr[start : end + 1])
    except json.JSONDecodeError:
        return None
    # require every measured field the second pass needs
    needed = {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}
    if not needed.issubset(data.keys()):
        return None
    return data


# normalize audio with two pass loudnorm or a one pass approximation in preview mode
def apply_loudnorm_two_pass(
    input_path: Path,
    output_path: Path,
    preview: bool = False,
    integrated_lufs: float = LOUDNORM_I,
    true_peak_dbtp: float = LOUDNORM_TP,
    lra: float = LOUDNORM_LRA,
) -> bool:
    """Run two-pass loudnorm on input_path, write normalized copy to output_path.

    Returns True on success, False if measurement failed (caller should fall
    back to copying the input unchanged).

    In preview mode, skips the measurement pass and uses a one-pass approximation
    for speed. Final mode always does the proper two-pass.
    """
    if preview:
        # One-pass approximation — faster, slightly less accurate.
        filter_str = (
            f"loudnorm=I={integrated_lufs}:TP={true_peak_dbtp}:LRA={lra}"
        )
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            "-i", str(input_path),
            "-c:v", "copy",
            "-af", filter_str,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(output_path),
        ]
        print(f"  loudnorm (1-pass preview) → {output_path.name}")
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True

    # Full two-pass
    print(f"  loudnorm pass 1: measuring {input_path.name}")
    measurement = measure_loudness(
        input_path,
        integrated_lufs=integrated_lufs,
        true_peak_dbtp=true_peak_dbtp,
        lra=lra,
    )
    # fall back to the one pass filter when measurement did not produce json
    if measurement is None:
        print("  loudnorm measurement failed — falling back to 1-pass")
        return apply_loudnorm_two_pass(
            input_path,
            output_path,
            preview=True,
            integrated_lufs=integrated_lufs,
            true_peak_dbtp=true_peak_dbtp,
            lra=lra,
        )

    print(f"    measured: I={measurement['input_i']} LUFS  "
          f"TP={measurement['input_tp']}  LRA={measurement['input_lra']}")

    # feed the measured values back so the second pass applies a linear gain
    filter_str = (
        f"loudnorm=I={integrated_lufs}:TP={true_peak_dbtp}:LRA={lra}"
        f":measured_I={measurement['input_i']}"
        f":measured_TP={measurement['input_tp']}"
        f":measured_LRA={measurement['input_lra']}"
        f":measured_thresh={measurement['input_thresh']}"
        f":offset={measurement['target_offset']}"
        f":linear=true"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-i", str(input_path),
        "-c:v", "copy",
        "-af", filter_str,
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(output_path),
    ]
    print(f"  loudnorm pass 2: normalizing → {output_path.name}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return True


# -------- Per-deliverable reframing -----------------------------------------


# load and validate subject center keyframes from inline data or a json track file
def _load_track_keyframes(reframe: dict, edit_dir: Path) -> list[dict]:
    """Load normalized subject-center keyframes from inline data or JSON."""
    raw = reframe.get("keyframes")
    if raw is None and isinstance(reframe.get("track"), list):
        raw = reframe["track"]
    if raw is None:
        track_value = reframe.get("track_file") or reframe.get("track")
        if not isinstance(track_value, str):
            raise ValueError("tracked reframe requires inline keyframes or a JSON track file")
        track_path = resolve_path(track_value, edit_dir)
        try:
            payload = json.loads(track_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not read tracked reframe data: {track_path}") from exc
        # a file with named tracks needs a track_id to pick one
        if isinstance(payload, dict) and isinstance(payload.get("tracks"), dict):
            track_id = reframe.get("track_id")
            if not track_id:
                raise ValueError(
                    f"tracked reframe file contains named tracks; choose one with track_id: {track_path}"
                )
            raw = payload["tracks"].get(str(track_id))
            if raw is None:
                raise ValueError(f"tracked reframe track_id '{track_id}' was not found: {track_path}")
        else:
            raw = payload.get("keyframes") if isinstance(payload, dict) else payload
    if not isinstance(raw, list) or not raw:
        raise ValueError("tracked reframe keyframes must be a non-empty list")

    keyframes: list[dict] = []
    previous_time = -1.0
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"tracked reframe keyframe {index} must be an object")
        try:
            timestamp = float(item.get("time", item.get("t")))
            # accept a center pair or a subject box or separate center fields
            center = item.get("center")
            box = item.get("subject_box") or item.get("box")
            if isinstance(center, (list, tuple)) and len(center) == 2:
                raw_x, raw_y = center
            elif isinstance(box, dict):
                raw_x = float(box["x"]) + float(box["width"]) / 2
                raw_y = float(box["y"]) + float(box["height"]) / 2
            else:
                raw_x = item.get("center_x", item.get("x"))
                raw_y = item.get("center_y", item.get("y"))
            center_x = float(raw_x)
            center_y = float(raw_y)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"tracked reframe keyframe {index} requires numeric time, center_x, and center_y"
            ) from exc
        # keyframes must be strictly increasing so the piecewise expression is well defined
        if timestamp < 0 or timestamp <= previous_time:
            raise ValueError("tracked reframe keyframe times must be non-negative and increasing")
        if not 0.0 <= center_x <= 1.0 or not 0.0 <= center_y <= 1.0:
            raise ValueError("tracked reframe centers must use normalized values from 0 to 1")
        keyframes.append({"time": timestamp, "center_x": center_x, "center_y": center_y})
        previous_time = timestamp
    return keyframes


# build a nested if expression that interpolates one center coordinate over time
def _piecewise_track_expression(
    keyframes: list[dict],
    coordinate: str,
    interpolation: str = "linear",
) -> str:
    """Build a continuous ffmpeg expression from sparse tracking keyframes."""
    if coordinate not in {"center_x", "center_y"}:
        raise ValueError("track coordinate must be center_x or center_y")
    if len(keyframes) == 1:
        return f"{float(keyframes[0][coordinate]):.8f}"

    expressions: list[tuple[float, str]] = []
    for first, second in zip(keyframes, keyframes[1:]):
        start = float(first["time"])
        end = float(second["time"])
        first_value = float(first[coordinate])
        delta = float(second[coordinate]) - first_value
        # progress runs from 0 to 1 across this keyframe pair and is clamped outside it
        progress = f"max(0,min(1,(t-{start:.8f})/{end - start:.8f}))"
        # smoothstep easing for smooth and a frozen start value for hold
        if interpolation == "smooth":
            progress = f"({progress})*({progress})*(3-2*({progress}))"
        elif interpolation == "hold":
            progress = "0"
        elif interpolation != "linear":
            raise ValueError("track interpolation must be linear, smooth, or hold")
        segment_expression = f"{first_value:.8f}+({delta:.8f})*({progress})"
        expressions.append((end, segment_expression))
    # nest the segments from last to first so each if guards its own end time
    result = f"{float(keyframes[-1][coordinate]):.8f}"
    for end, interpolation in reversed(expressions):
        result = f"if(lt(t,{end:.8f}),{interpolation},{result})"
    return result


# crop or letterbox the base into one deliverable aspect ratio while keeping its audio
def build_reframed_base(
    base_path: Path,
    output_path: Path,
    *,
    width: int,
    height: int,
    fps: str,
    reframe: dict,
    edit_dir: Path,
    preview: bool = False,
    draft: bool = False,
) -> None:
    """Create one aspect-ratio-specific base while preserving its audio.

    ``cover`` uses a centered crop, ``contain`` letterboxes, and ``track``
    follows interpolated normalized subject centers authored on the output
    timeline. Tracking is explicit and deterministic; it never silently falls
    back to a center crop when tracking data is missing.
    """
    source_width, source_height = probe_video_size(base_path)
    mode = str(reframe.get("mode") or "cover")
    # contain scales to fit then pads to the target size with a background color
    if mode == "contain":
        background = str(reframe.get("background") or "black").replace("'", "")
        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={background},"
            f"setsar=1,fps={fps}"
        )
    else:
        # pick the largest even crop that matches the target ratio inside the source
        source_ratio = source_width / source_height
        target_ratio = width / height
        if source_ratio > target_ratio:
            crop_width = max(2, int(source_height * target_ratio) // 2 * 2)
            crop_height = source_height // 2 * 2
        else:
            crop_width = source_width // 2 * 2
            crop_height = max(2, int(source_width / target_ratio) // 2 * 2)

        if mode == "track":
            keyframes = _load_track_keyframes(reframe, edit_dir)
            interpolation = str(reframe.get("interpolation") or "linear")
            center_x = _piecewise_track_expression(
                keyframes, "center_x", interpolation
            )
            center_y = _piecewise_track_expression(
                keyframes, "center_y", interpolation
            )
            # center the crop on the tracked point and clamp so it stays inside the frame
            crop_x = f"max(0,min(iw-ow,({center_x})*iw-ow/2))"
            crop_y = f"max(0,min(ih-oh,({center_y})*ih-oh/2))"
        elif mode == "cover":
            crop_x, crop_y = "(iw-ow)/2", "(ih-oh)/2"
        else:
            raise ValueError(f"unsupported reframe mode: {mode}")
        video_filter = (
            f"crop={crop_width}:{crop_height}:x='{crop_x}':y='{crop_y}',"
            f"scale={width}:{height},setsar=1,fps={fps}"
        )

    if draft:
        preset, crf = "ultrafast", "28"
    elif preview:
        preset, crf = "medium", "22"
    else:
        preset, crf = "fast", "20"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"reframing → {output_path.name} ({width}x{height}@{fps}, {mode})")
    command = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-i", str(base_path),
        "-vf", video_filter,
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


# -------- Final compositing (Rule 1 + Rule 4) -------------------------------


DEFAULT_CAPTION_REGION = {"x": 0.0, "y": 0.84, "width": 1.0, "height": 0.16}

OVERLAY_LAYOUTS = {
    # ``full`` means the full visual canvas. When captions are present, that
    # canvas stops above the caption rail rather than hiding words under video.
    "full": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
    # Inset layouts preserve a title band above and the caption rail below.
    "center": {"x": 0.18, "y": 0.15, "width": 0.64, "height": 0.58},
    "left": {"x": 0.04, "y": 0.17, "width": 0.44, "height": 0.57},
    "right": {"x": 0.52, "y": 0.17, "width": 0.44, "height": 0.57},
    "pip_left": {"x": 0.05, "y": 0.50, "width": 0.30, "height": 0.28},
    "pip_center": {"x": 0.35, "y": 0.50, "width": 0.30, "height": 0.28},
    "pip_right": {"x": 0.65, "y": 0.50, "width": 0.30, "height": 0.28},
}

# Composition names express the editorial relationship, not merely coordinates.
# For split modes, the name describes where the external footage sits.
COMPOSITION_LAYOUTS = {
    "cutaway": "full",
    "split_left": "left",
    "split_right": "right",
}


# validate a rectangle given as fractions of the output frame
def _normalized_rect(value: dict, label: str) -> dict[str, float]:
    """Validate an EDL rectangle expressed as fractions of the output frame."""
    try:
        rect = {key: float(value[key]) for key in ("x", "y", "width", "height")}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"{label} must contain numeric x, y, width, and height values"
        ) from exc

    # nan and inf slip through ordered comparisons so they are rejected first
    if not all(math.isfinite(number) for number in rect.values()):
        raise ValueError(f"{label} must contain finite numbers")
    # reject rects with a negative origin or non positive size or that spill past the frame edge
    if rect["x"] < 0 or rect["y"] < 0 or rect["width"] <= 0 or rect["height"] <= 0:
        raise ValueError(f"{label} must use non-negative x/y and positive width/height")
    if rect["x"] + rect["width"] > 1.000001 or rect["y"] + rect["height"] > 1.000001:
        raise ValueError(f"{label} must stay inside the normalized output frame")
    return rect


# report whether two normalized rectangles overlap in space
def _rects_intersect(a: dict[str, float], b: dict[str, float]) -> bool:
    # two rects miss when one is entirely left right above or below the other
    return not (
        a["x"] + a["width"] <= b["x"]
        or b["x"] + b["width"] <= a["x"]
        or a["y"] + a["height"] <= b["y"]
        or b["y"] + b["height"] <= a["y"]
    )


# report whether two half open time ranges overlap
def _time_ranges_intersect(
    first_start: float,
    first_end: float,
    second_start: float,
    second_end: float,
) -> bool:
    return first_start < second_end and second_start < first_end


# read and validate the output timeline start and end of an overlay
def overlay_time_range(overlay: dict) -> tuple[float, float]:
    try:
        start = float(overlay["start_in_output"])
        duration = float(overlay["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("overlay requires numeric start_in_output and duration") from exc
    if start < 0 or duration <= 0:
        raise ValueError("overlay requires start_in_output >= 0 and duration > 0")
    return start, start + duration


# resolve an overlay layout or rect into a normalized rectangle that stays clear of the caption rail
def resolve_overlay_rect(
    overlay: dict,
    has_subtitles: bool,
    caption_config: dict | None = None,
) -> dict[str, float] | None:
    """Resolve a named/custom overlay layout and protect the caption rail.

    Legacy overlays with neither ``layout`` nor ``rect`` return ``None`` and
    preserve their original full-frame behavior. New explainer overlays should
    always name a layout so their spatial contract can be validated.
    """
    composition = overlay.get("composition")
    layout = overlay.get("layout")
    custom = overlay.get("rect")
    # a composition name fixes the layout so conflicting layout or rect values are rejected
    if composition is not None:
        allowed = {*COMPOSITION_LAYOUTS, "picture_in_picture"}
        if composition not in allowed:
            choices = ", ".join(sorted(allowed))
            raise ValueError(
                f"unknown overlay composition '{composition}'; choose {choices}"
            )
        if composition in COMPOSITION_LAYOUTS:
            expected_layout = COMPOSITION_LAYOUTS[composition]
            if custom is not None or layout not in (None, expected_layout):
                raise ValueError(
                    f"composition '{composition}' owns layout '{expected_layout}' and "
                    "cannot be combined with another layout or custom rect"
                )
            layout = expected_layout
        elif layout is None and custom is None:
            raise ValueError(
                "picture_in_picture requires layout pip_left/pip_center/pip_right or a custom rect"
            )
        elif custom is None and layout not in {"pip_left", "pip_center", "pip_right"}:
            raise ValueError(
                "picture_in_picture must use pip_left, pip_center, pip_right, or a custom rect"
            )
    if layout is None and custom is None:
        return None
    if custom is not None and layout not in (None, "custom"):
        raise ValueError("overlay must use either a named layout or rect, not both")

    if custom is not None:
        rect = _normalized_rect(custom, "overlay rect")
    else:
        if layout not in OVERLAY_LAYOUTS:
            choices = ", ".join([*OVERLAY_LAYOUTS, "custom"])
            raise ValueError(f"unknown overlay layout '{layout}'; choose {choices}")
        rect = dict(OVERLAY_LAYOUTS[layout])

    caption_region: dict[str, float] | None = None
    if has_subtitles:
        raw_region = (caption_config or {}).get("safe_region", DEFAULT_CAPTION_REGION)
        caption_region = _normalized_rect(raw_region, "caption safe_region")
        if layout == "full" and custom is None:
            # Full footage fills every pixel that is not reserved for captions.
            if caption_region["x"] == 0.0 and caption_region["width"] == 1.0:
                rect["height"] = min(rect["height"], caption_region["y"])

    # any remaining overlap with the caption rail is a hard error
    if caption_region and _rects_intersect(rect, caption_region):
        raise ValueError(
            "overlay rectangle intersects captions.safe_region; move or resize the "
            "overlay so captions never cover footage or illustrations"
        )
    return rect


# reject overlays that collide with each other or protected regions and check web asset provenance
def validate_overlay_contracts(
    overlays: list[dict],
    protected_regions: list[dict] | None,
    has_subtitles: bool,
    caption_config: dict | None = None,
) -> None:
    """Reject temporal/spatial collisions before starting an expensive render."""
    resolved: list[tuple[dict, dict[str, float] | None, float, float]] = []
    protected_regions = protected_regions or []

    seen_web_assets: dict[str, dict] = {}
    for index, overlay in enumerate(overlays):
        start, end = overlay_time_range(overlay)
        rect = resolve_overlay_rect(overlay, has_subtitles, caption_config)
        resolved.append((overlay, rect, start, end))

        # web overlays must carry provenance and may only repeat via reuse_of
        if overlay.get("media_kind") == "web":
            required = ("source_url", "source_start", "source_end", "asset_id")
            missing = [field for field in required if overlay.get(field) in (None, "")]
            if not (overlay.get("id") or overlay.get("beat_id")):
                missing.append("id or beat_id")
            if missing:
                raise ValueError(
                    f"web overlay {index} is missing provenance fields: {', '.join(missing)}"
                )
            asset_id = str(overlay["asset_id"])
            prior = seen_web_assets.get(asset_id)
            if prior is not None:
                prior_id = prior.get("id") or prior.get("beat_id")
                if overlay.get("reuse_of") != prior_id:
                    raise ValueError(
                        f"web asset {asset_id} appears more than once; reuse_of must "
                        f"reference the original overlay '{prior_id}'"
                    )
                if overlay.get("file") != prior.get("file"):
                    raise ValueError(
                        f"reused web asset {asset_id} must reference the same prepared file"
                    )
            else:
                seen_web_assets[asset_id] = overlay

    # protected illustration regions only conflict with overlays active in the same interval and cutaways are exempt
    for region_index, region in enumerate(protected_regions):
        label = str(region.get("owner") or region.get("id") or region_index)
        try:
            region_start = float(region["start_in_output"])
            region_end = region_start + float(region["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"protected region '{label}' requires numeric start_in_output and duration"
            ) from exc
        if region_start < 0 or region_end <= region_start:
            raise ValueError(f"protected region '{label}' has an invalid time range")
        region_rect = _normalized_rect(region.get("rect"), f"protected region '{label}'")
        for overlay, rect, start, end in resolved:
            if overlay.get("composition") == "cutaway":
                continue
            if rect is None or not _time_ranges_intersect(start, end, region_start, region_end):
                continue
            if _rects_intersect(rect, region_rect):
                overlay_label = overlay.get("id") or overlay.get("beat_id") or overlay.get("file")
                raise ValueError(
                    f"overlay '{overlay_label}' intersects protected illustration '{label}' "
                    "during the same output interval; use a cutaway or move the overlay"
                )

    # overlays that share space and time are rejected unless one allows overlap
    for index, (first, first_rect, first_start, first_end) in enumerate(resolved):
        if first_rect is None or first.get("allow_overlap"):
            continue
        for second, second_rect, second_start, second_end in resolved[index + 1 :]:
            if second_rect is None or second.get("allow_overlap"):
                continue
            if not _time_ranges_intersect(first_start, first_end, second_start, second_end):
                continue
            if _rects_intersect(first_rect, second_rect):
                first_label = first.get("id") or first.get("file")
                second_label = second.get("id") or second.get("file")
                raise ValueError(
                    f"overlays '{first_label}' and '{second_label}' overlap in space and time"
                )


# convert a normalized value to an even pixel count clamped within bounds
def _even_pixel(value: float, maximum: int, *, minimum: int = 0) -> int:
    """Convert a normalized coordinate/size to an even pixel value for yuv420p."""
    pixels = int(round(value * maximum))
    pixels = max(minimum, min(maximum, pixels))
    if pixels % 2:
        pixels -= 1
    return max(minimum, pixels)


# composite overlays onto the base and burn subtitles last in one ffmpeg filter graph
def build_final_composite(
    base_path: Path,
    overlays: list[dict],
    subtitles_path: Path | None,
    out_path: Path,
    edit_dir: Path,
    caption_config: dict | None = None,
    protected_regions: list[dict] | None = None,
) -> None:
    """Final pass: base → overlays (PTS-shifted) → subtitles LAST → out.

    If there are no overlays and no subtitles, just copy base to out.
    """
    has_overlays = bool(overlays)
    has_subs = subtitles_path is not None and subtitles_path.exists()
    validate_overlay_contracts(
        overlays, protected_regions, has_subs, caption_config
    )

    if not has_overlays and not has_subs:
        # Nothing to do — just rename/copy base to final name
        run(["ffmpeg", "-y", "-i", str(base_path), "-c", "copy", str(out_path)], quiet=True)
        return

    inputs: list[str] = ["-i", str(base_path)]
    for ov in overlays:
        ov_path = resolve_path(ov["file"], edit_dir)
        inputs += ["-i", str(ov_path)]

    base_width, base_height = probe_video_size(base_path)
    filter_parts: list[str] = []
    # PTS-shift every overlay so its frame 0 lands at start_in_output
    for idx, ov in enumerate(overlays, start=1):
        t = float(ov["start_in_output"])
        rect = resolve_overlay_rect(ov, has_subs, caption_config)
        # legacy overlays without a layout fill the whole frame
        if rect is None:
            filter_parts.append(
                f"[{idx}:v]scale={base_width}:{base_height}:"
                "force_original_aspect_ratio=increase,"
                f"crop={base_width}:{base_height},setsar=1,"
                f"setpts=PTS-STARTPTS+{t}/TB[a{idx}]"
            )
            continue

        width = _even_pixel(rect["width"], base_width, minimum=2)
        height = _even_pixel(rect["height"], base_height, minimum=2)
        # cover fills the rect by cropping and contain letterboxes with a background
        fit = ov.get("fit", "cover")
        if fit == "cover":
            geometry = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}"
            )
        elif fit == "contain":
            background = str(ov.get("background", "#050914")).replace("'", "")
            geometry = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={background}"
            )
        else:
            raise ValueError("overlay fit must be 'cover' or 'contain'")
        filter_parts.append(
            f"[{idx}:v]{geometry},setsar=1,setpts=PTS-STARTPTS+{t}/TB[a{idx}]"
        )

    # Chain overlays on top of base
    current = "[0:v]"
    for idx, ov in enumerate(overlays, start=1):
        t = float(ov["start_in_output"])
        dur = float(ov["duration"])
        end = t + dur
        next_label = f"[v{idx}]"
        rect = resolve_overlay_rect(ov, has_subs, caption_config)
        if rect is None:
            position = "x=0:y=0"
        else:
            x = _even_pixel(rect["x"], base_width)
            y = _even_pixel(rect["y"], base_height)
            position = f"x={x}:y={y}"
        # enable limits the overlay to its output window even though the input is pts shifted
        filter_parts.append(
            f"{current}[a{idx}]overlay={position}:enable="
            f"'between(t,{t:.3f},{end:.3f})'{next_label}"
        )
        current = next_label

    # Subtitles LAST — Rule 1
    if has_subs:
        # escape colons and quotes so the path survives filter option parsing
        subs_abs = str(subtitles_path.resolve()).replace(":", r"\:").replace("'", r"\'")
        # ASS files carry their own style while srt gets the forced style
        if subtitles_path.suffix.lower() == ".ass":
            filter_parts.append(f"{current}subtitles='{subs_abs}'[outv]")
        else:
            filter_parts.append(
                f"{current}subtitles='{subs_abs}':force_style='{SUB_FORCE_STYLE}'[outv]"
            )
        out_label = "[outv]"
    else:
        # Rename the last overlay output to [outv] for consistency
        if has_overlays:
            filter_parts.append(f"{current}null[outv]")
            out_label = "[outv]"
        else:
            out_label = "[0:v]"

    filter_complex = ";".join(filter_parts)

    ffmpeg_binary = ffmpeg_with_subtitles() if has_subs else "ffmpeg"
    cmd = [
        ffmpeg_binary, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", out_label,
        "-map", "0:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]
    print(f"compositing → {out_path.name}")
    print(f"  overlays: {len(overlays)}, subtitles: {'yes' if has_subs else 'no'}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


# grab one frame from a video or normalize a still image for the preflight sheet
def _extract_review_frame(media: Path, timestamp: float, output: Path) -> None:
    """Extract one video frame, or normalize a still image, for preflight."""
    # still images are converted directly instead of going through ffmpeg
    if media.suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
        from PIL import Image

        with Image.open(media) as image:
            image.convert("RGBA").save(output)
        return
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, timestamp):.3f}",
        "-i",
        str(media),
        "-frames:v",
        "1",
        str(output),
    ]
    subprocess.run(command, check=True)


# draw an entry middle and exit contact sheet for every overlay without rendering the video
def build_overlay_preflight(
    base_path: Path,
    overlays: list[dict],
    output: Path,
    edit_dir: Path,
    *,
    has_subtitles: bool,
    caption_config: dict | None,
    protected_regions: list[dict] | None,
) -> Path:
    """Create an entry/middle/exit contact sheet without rendering the video.

    The sheet uses the exact layout math from final compositing and marks the
    caption rail plus active protected illustration regions. It is intended as
    the cheap approval gate before an expensive full Manim/FFmpeg pass.
    """
    if not overlays:
        raise ValueError("overlay preflight requires at least one overlay")
    if output.suffix.casefold() != ".png":
        raise ValueError("overlay preflight output must be a .png file")
    validate_overlay_contracts(
        overlays, protected_regions, has_subtitles, caption_config
    )

    from PIL import Image, ImageDraw, ImageOps

    cell_width, cell_height = 640, 360
    label_height = 34
    columns = 3
    sheet = Image.new(
        "RGB",
        (cell_width * columns, (cell_height + label_height) * len(overlays)),
        "#080b12",
    )
    protected_regions = protected_regions or []
    caption_region = None
    if has_subtitles:
        caption_region = _normalized_rect(
            (caption_config or {}).get("safe_region", DEFAULT_CAPTION_REGION),
            "caption safe_region",
        )

    with tempfile.TemporaryDirectory(prefix="video-use-preflight-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        for row, overlay in enumerate(overlays):
            start, end = overlay_time_range(overlay)
            duration = end - start
            # sample just inside each edge and at the middle of the overlay window
            inset = min(0.10, duration / 4)
            local_samples = (inset, duration / 2, max(inset, duration - inset))
            rect = resolve_overlay_rect(overlay, has_subtitles, caption_config)
            if rect is None:
                rect = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
            overlay_path = resolve_path(str(overlay["file"]), edit_dir)
            if not overlay_path.exists():
                raise ValueError(f"overlay file does not exist: {overlay_path}")

            for column, local_time in enumerate(local_samples):
                output_time = start + local_time
                base_frame = temp_dir / f"base-{row}-{column}.png"
                media_frame = temp_dir / f"overlay-{row}-{column}.png"
                _extract_review_frame(base_path, output_time, base_frame)
                _extract_review_frame(overlay_path, local_time, media_frame)

                with Image.open(base_frame) as image:
                    frame = ImageOps.fit(
                        image.convert("RGBA"), (cell_width, cell_height), method=Image.Resampling.LANCZOS
                    )
                with Image.open(media_frame) as image:
                    source = image.convert("RGBA")

                x = int(round(rect["x"] * cell_width))
                y = int(round(rect["y"] * cell_height))
                width = max(1, int(round(rect["width"] * cell_width)))
                height = max(1, int(round(rect["height"] * cell_height)))
                # mirror the contain and cover math from the final composite
                if overlay.get("fit", "cover") == "contain":
                    background = Image.new(
                        "RGBA", (width, height), str(overlay.get("background", "#050914"))
                    )
                    contained = ImageOps.contain(
                        source, (width, height), method=Image.Resampling.LANCZOS
                    )
                    background.alpha_composite(
                        contained,
                        ((width - contained.width) // 2, (height - contained.height) // 2),
                    )
                    placed = background
                else:
                    placed = ImageOps.fit(
                        source, (width, height), method=Image.Resampling.LANCZOS
                    )
                frame.alpha_composite(placed, (x, y))

                draw = ImageDraw.Draw(frame, "RGBA")
                draw.rectangle((x, y, x + width - 1, y + height - 1), outline="#48E0A4", width=3)
                # tint the caption rail and outline protected regions that are active at this time
                if caption_region is not None:
                    cx = int(round(caption_region["x"] * cell_width))
                    cy = int(round(caption_region["y"] * cell_height))
                    cw = int(round(caption_region["width"] * cell_width))
                    ch = int(round(caption_region["height"] * cell_height))
                    draw.rectangle((cx, cy, cx + cw, cy + ch), fill=(255, 177, 66, 35))
                    draw.rectangle((cx, cy, cx + cw, cy + ch), outline="#FFB142", width=2)
                for region in protected_regions:
                    region_start = float(region["start_in_output"])
                    region_end = region_start + float(region["duration"])
                    if not region_start <= output_time <= region_end:
                        continue
                    protected = _normalized_rect(region.get("rect"), "protected region")
                    px = int(round(protected["x"] * cell_width))
                    py = int(round(protected["y"] * cell_height))
                    pw = int(round(protected["width"] * cell_width))
                    ph = int(round(protected["height"] * cell_height))
                    draw.rectangle((px, py, px + pw, py + ph), outline="#FF5964", width=3)

                label = (
                    f"{overlay.get('id') or overlay.get('beat_id') or f'overlay {row + 1}'}  "
                    f"{('entry', 'middle', 'exit')[column]}  t={output_time:.2f}s"
                )
                cell_y = row * (cell_height + label_height)
                sheet.paste(frame.convert("RGB"), (column * cell_width, cell_y))
                label_draw = ImageDraw.Draw(sheet)
                label_draw.text(
                    (column * cell_width + 10, cell_y + cell_height + 8),
                    label,
                    fill="#EEF2FA",
                )

    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    print(f"overlay preflight: {output}")
    return output


# -------- Main ---------------------------------------------------------------


# reframe composite and normalize one output or deliverable and clean up intermediates
def render_one_output(
    *,
    base_path: Path,
    edl: dict,
    edit_dir: Path,
    out_path: Path,
    subtitles_path: Path | None,
    preview: bool,
    draft: bool,
    no_loudnorm: bool,
    deliverable: dict | None = None,
) -> None:
    """Composite and normalize one legacy output or declared deliverable."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    render_base = base_path
    reframed_path: Path | None = None
    loudness = {
        "integrated_lufs": LOUDNORM_I,
        "true_peak_dbtp": LOUDNORM_TP,
        "lra": LOUDNORM_LRA,
    }
    overlays = edl.get("overlays") or []
    # a deliverable gets a reframed base and its own loudness and optional overlay list
    if deliverable is not None:
        reframed_path = out_path.with_suffix(".reframed.mp4")
        build_reframed_base(
            base_path,
            reframed_path,
            width=int(deliverable["width"]),
            height=int(deliverable["height"]),
            fps=str(deliverable["fps"]),
            reframe=deliverable["reframe"],
            edit_dir=edit_dir,
            preview=preview,
            draft=draft,
        )
        render_base = reframed_path
        loudness = deliverable["loudness"]
        if "overlays" in deliverable:
            overlays = deliverable.get("overlays") or []

    # the prenorm composite and reframed base are removed even when a step fails
    try:
        if no_loudnorm:
            build_final_composite(
                render_base,
                overlays,
                subtitles_path,
                out_path,
                edit_dir,
                edl.get("captions"),
                edl.get("protected_regions"),
            )
        else:
            tmp_composite = out_path.with_suffix(".prenorm.mp4")
            try:
                build_final_composite(
                    render_base,
                    overlays,
                    subtitles_path,
                    tmp_composite,
                    edit_dir,
                    edl.get("captions"),
                    edl.get("protected_regions"),
                )
                print(
                    "loudness normalization → "
                    f"{loudness['integrated_lufs']:g} LUFS / "
                    f"{loudness['true_peak_dbtp']:g} dBTP / "
                    f"LRA {loudness['lra']:g}"
                )
                apply_loudnorm_two_pass(
                    tmp_composite,
                    out_path,
                    preview=draft,
                    integrated_lufs=float(loudness["integrated_lufs"]),
                    true_peak_dbtp=float(loudness["true_peak_dbtp"]),
                    lra=float(loudness["lra"]),
                )
            finally:
                tmp_composite.unlink(missing_ok=True)
    finally:
        if reframed_path is not None:
            reframed_path.unlink(missing_ok=True)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    label = f" [{deliverable['id']}]" if deliverable else ""
    print(f"done:{label} {out_path} ({size_mb:.1f} MB)")


# command line entry point that validates the edl and runs the full pipeline
def main() -> None:
    ap = argparse.ArgumentParser(description="Render a video from an EDL")
    ap.add_argument("edl", type=Path, help="Path to edl.json")
    ap.add_argument("-o", "--output", type=Path, help="Output video path")
    selection = ap.add_mutually_exclusive_group()
    selection.add_argument(
        "--deliverable",
        help="Render one deliverable id from the EDL; its declared file is used unless -o is set",
    )
    selection.add_argument(
        "--all-deliverables",
        action="store_true",
        help="Render every deliverable declared in the EDL",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        help="Override declared deliverable files and write <id>.mp4 into this directory",
    )
    ap.add_argument(
        "--preview",
        action="store_true",
        help="Preview mode: 1080p, medium, CRF 22 — evaluable for QC, faster than final.",
    )
    ap.add_argument(
        "--draft",
        action="store_true",
        help="Draft mode: 720p, ultrafast, CRF 28 — cut-point verification only.",
    )
    ap.add_argument(
        "--build-subtitles",
        action="store_true",
        help="Build master.srt from transcripts + EDL offsets before compositing",
    )
    ap.add_argument(
        "--no-subtitles",
        action="store_true",
        help="Skip subtitles even if the EDL references one",
    )
    ap.add_argument(
        "--no-loudnorm",
        action="store_true",
        help="Skip audio loudness normalization. Default is on and uses each "
             "deliverable target, or -14 LUFS / -1 dBTP / LRA 11 for legacy output.",
    )
    ap.add_argument(
        "--preflight-overlays",
        action="store_true",
        help="Write a PNG contact sheet for overlay entry/middle/exit frames and stop",
    )
    ap.add_argument(
        "--preflight-base",
        type=Path,
        default=None,
        help="Existing base video for a fast overlay preflight; skips segment extraction",
    )
    ap.add_argument(
        "--fps",
        type=parse_fps,
        default=None,
        help="Output frame rate. Default: preserve the source's frame rate "
             "(falls back to 24 if it can't be probed). Pass e.g. --fps 30 or "
             "--fps 30000/1001 to force.",
    )
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    if not edl_path.exists():
        sys.exit(f"edl not found: {edl_path}")

    edl = json.loads(edl_path.read_text())
    edit_dir = edl_path.parent
    try:
        validate_edl(edl, edit_dir)
    except EDLValidationError as exc:
        sys.exit(str(exc))
    output_dir = args.output_dir.resolve() if args.output_dir else None
    try:
        deliverables = normalize_deliverables(edl, edit_dir, output_dir=output_dir)
    except EDLValidationError as exc:
        sys.exit(str(exc))
    # cross check the output selectors before any media work starts
    if args.output_dir and not (args.all_deliverables or args.deliverable):
        ap.error("--output-dir requires --deliverable or --all-deliverables")
    if args.all_deliverables and not deliverables:
        ap.error("--all-deliverables requires EDL deliverables")
    if args.deliverable and not deliverables:
        ap.error("--deliverable requires EDL deliverables")
    selected_deliverables: list[dict] = []
    if args.all_deliverables:
        selected_deliverables = deliverables
    elif args.deliverable:
        selected_deliverables = [
            item for item in deliverables if item["id"] == args.deliverable
        ]
        if not selected_deliverables:
            choices = ", ".join(item["id"] for item in deliverables)
            ap.error(f"unknown deliverable '{args.deliverable}'; choose {choices}")
    elif args.output is None:
        ap.error("-o/--output is required unless a deliverable selector is used")
    if args.output is not None and args.all_deliverables:
        ap.error("-o/--output cannot be combined with --all-deliverables; use --output-dir")

    out_path = args.output.resolve() if args.output else None
    overlays = edl.get("overlays") or []

    # a preflight base skips extraction and concat and only draws the contact sheet
    if args.preflight_base is not None:
        if not args.preflight_overlays:
            ap.error("--preflight-base requires --preflight-overlays")
        base_path = resolve_path(str(args.preflight_base), edit_dir)
        if not base_path.exists():
            sys.exit(f"preflight base not found: {base_path}")
        if out_path is None:
            ap.error("overlay preflight requires -o/--output")
        has_subtitles = not args.no_subtitles and bool(edl.get("subtitles"))
        build_overlay_preflight(
            base_path,
            overlays,
            out_path,
            edit_dir,
            has_subtitles=has_subtitles,
            caption_config=edl.get("captions"),
            protected_regions=edl.get("protected_regions"),
        )
        return

    # 1. Extract per-segment (auto-grade per range if EDL grade is "auto")
    segment_paths = extract_all_segments(
        edl, edit_dir, preview=args.preview, draft=args.draft, fps=args.fps
    )

    # 2. Concat → base
    if args.draft:
        base_name = "base_draft.mp4"
    elif args.preview:
        base_name = "base_preview.mp4"
    else:
        base_name = "base.mp4"
    base_path = edit_dir / base_name
    concat_segments(segment_paths, base_path, edit_dir)

    # 3. Subtitles: build if requested, resolve final path
    subs_path: Path | None = None
    if not args.no_subtitles:
        if args.build_subtitles:
            subs_path = edit_dir / "master.srt"
            build_master_srt(edl, edit_dir, subs_path)
        elif edl.get("subtitles"):
            subs_path = resolve_path(edl["subtitles"], edit_dir)
            if not subs_path.exists():
                print(f"warning: subtitles path in EDL does not exist: {subs_path}")
                subs_path = None

    # preflight after concat uses the freshly built base
    if args.preflight_overlays:
        if out_path is None:
            ap.error("overlay preflight requires -o/--output")
        build_overlay_preflight(
            base_path,
            overlays,
            out_path,
            edit_dir,
            has_subtitles=subs_path is not None and subs_path.exists(),
            caption_config=edl.get("captions"),
            protected_regions=edl.get("protected_regions"),
        )
        return

    # 4. Reframe each delivery, composite overlays/subtitles LAST, then loudnorm.
    if selected_deliverables:
        for deliverable in selected_deliverables:
            # a single selected deliverable may be redirected with the output flag
            delivery_output = (
                out_path
                if len(selected_deliverables) == 1 and out_path is not None
                else deliverable["output_path"]
            )
            render_one_output(
                base_path=base_path,
                edl=edl,
                edit_dir=edit_dir,
                out_path=delivery_output,
                subtitles_path=subs_path,
                preview=args.preview,
                draft=args.draft,
                no_loudnorm=args.no_loudnorm,
                deliverable=deliverable,
            )
    else:
        assert out_path is not None
        render_one_output(
            base_path=base_path,
            edl=edl,
            edit_dir=edit_dir,
            out_path=out_path,
            subtitles_path=subs_path,
            preview=args.preview,
            draft=args.draft,
            no_loudnorm=args.no_loudnorm,
        )


if __name__ == "__main__":
    main()
