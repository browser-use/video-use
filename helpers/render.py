"""Render a video from an EDL.

Implements the HEURISTICS render pipeline in the correct order:

  1. Per-segment extract with color grade + 30ms audio fades baked in
  2. Lossless -c copy concat into base.mp4
  3. If overlays or subtitles: single filter graph that overlays animations
     (with PTS shift so frame 0 lands at the overlay window start)
     and applies `subtitles` / `ass` filter LAST → final.mp4

Optionally builds a master ASS (default) or SRT from per-source transcripts
+ EDL output-timeline offsets.

ASS mode (default):
  - 2-word UPPERCASE chunks
  - Fade in 120 ms / fade out 80 ms per cue
  - Subtle pop scale (107 % → 100 % over 220 ms)
  - Brand keywords highlighted in orange (configurable via BRAND_KEYWORDS)

Music:
  Add a "music" block to the EDL:
    "music": { "file": "/path/to/track.mp3", "volume": 0.12,
               "fade_in": 2.0, "fade_out": 2.0 }
  The track is mixed under speech at the specified volume with fade in/out.

Usage:
    python helpers/render.py <edl.json> -o final.mp4
    python helpers/render.py <edl.json> -o preview.mp4 --preview
    python helpers/render.py <edl.json> -o final.mp4 --build-subtitles
    python helpers/render.py <edl.json> -o final.mp4 --build-subtitles --srt
    python helpers/render.py <edl.json> -o final.mp4 --no-subtitles
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    from grade import get_preset, auto_grade_for_clip  # same directory
except Exception:
    def get_preset(name: str) -> str:
        return ""

    def auto_grade_for_clip(video, start=0.0, duration=None, verbose=False):  # type: ignore
        return "eq=contrast=1.03:saturation=0.98", {}


# -------- SRT subtitle style (legacy fallback) --------------------------------

SUB_FORCE_STYLE = (
    "FontName=Helvetica,FontSize=18,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
    "BorderStyle=1,Outline=2,Shadow=0,"
    "Alignment=2,MarginV=35"
)

# -------- ASS subtitle config -------------------------------------------------

# Play resolution — must match render output (1080p portrait or landscape)
ASS_PLAY_RES_X = 1080
ASS_PLAY_RES_Y = 1920

# ASS style row (fields match Format line in [V4+ Styles])
# FontSize 22 @ 1080p portrait, outline 2.5, alignment 2 (bottom-centre), MarginV 60
ASS_STYLE_ROW = (
    "Style: Default,Helvetica,22,"
    "&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"   # primary, secondary, outline, back
    "1,0,0,0,"                                        # bold, italic, underline, strikeout
    "100,100,0,0,"                                    # scaleX, scaleY, spacing, angle
    "1,2.5,0,"                                        # border style, outline, shadow
    "2,10,10,60,1"                                    # alignment, marginL, marginR, marginV, encoding
)

# Brand keywords highlighted in orange (#FF8C00 → ASS &HAABBGGRR = &H00008CFF)
BRAND_ORANGE = "&H00008CFF"
BRAND_KEYWORDS: set[str] = {
    "EGGHEY", "LAID", "LAID-TODAY'S", "LAID-TODAY", "LAID-DATE",
}

# Per-cue animation: fade in 120ms / out 80ms + pop scale 107%→100% over 220ms
ASS_CUE_FX = r"{\fad(120,80)\t(0,80,\fscx107\fscy107)\t(80,220,\fscx100\fscy100)}"

# -------- Helpers ------------------------------------------------------------


def run(cmd: list[str], quiet: bool = False) -> None:
    if not quiet:
        print(f"  $ {' '.join(str(c) for c in cmd[:6])}{' …' if len(cmd) > 6 else ''}")
    subprocess.run(cmd, check=True)


def resolve_grade_filter(grade_field: str | None) -> str:
    if not grade_field:
        return ""
    if grade_field == "auto":
        return "__AUTO__"
    if re.fullmatch(r"[a-zA-Z0-9_\-]+", grade_field):
        try:
            return get_preset(grade_field)
        except KeyError:
            print(f"warning: unknown preset '{grade_field}', using as raw filter")
            return grade_field
    return grade_field


def resolve_path(maybe_path: str, base: Path) -> Path:
    p = Path(maybe_path)
    if p.is_absolute():
        return p
    return (base / p).resolve()


# -------- Per-segment extraction ---------------------------------------------


def extract_segment(
    source: Path,
    seg_start: float,
    duration: float,
    grade_filter: str,
    out_path: Path,
    preview: bool = False,
    draft: bool = False,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if draft:
        scale = "scale=w='if(gte(iw,ih),1280,trunc(1280*iw/ih/2)*2)':h='if(gte(iw,ih),trunc(1280*ih/iw/2)*2,1280)'"
    else:
        scale = "scale=w='if(gte(iw,ih),1920,trunc(1920*iw/ih/2)*2)':h='if(gte(iw,ih),trunc(1920*ih/iw/2)*2,1920)'"

    vf_parts = [scale]
    if grade_filter:
        vf_parts.append(grade_filter)
    vf = ",".join(vf_parts)

    fade_out_start = max(0.0, duration - 0.03)
    af = f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out_start:.3f}:d=0.03"

    if draft:
        preset, crf = "ultrafast", "28"
    elif preview:
        preset, crf = "medium", "22"
    else:
        preset, crf = "fast", "20"

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{seg_start:.3f}",
        "-i", str(source),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-r", "24",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def extract_all_segments(
    edl: dict,
    edit_dir: Path,
    preview: bool,
    draft: bool = False,
) -> list[Path]:
    resolved = resolve_grade_filter(edl.get("grade"))
    is_auto = resolved == "__AUTO__"
    clips_dir = edit_dir / (
        "clips_draft" if draft else ("clips_preview" if preview else "clips_graded")
    )
    clips_dir.mkdir(parents=True, exist_ok=True)

    ranges = edl["ranges"]
    sources = edl["sources"]

    seg_paths: list[Path] = []
    print(f"extracting {len(ranges)} segment(s) → {clips_dir.name}/")
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
        extract_segment(src_path, start, duration, seg_filter, out_path, preview=preview, draft=draft)
        seg_paths.append(out_path)

    return seg_paths


# -------- Lossless concat ----------------------------------------------------


def concat_segments(segment_paths: list[Path], out_path: Path, edit_dir: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list = edit_dir / "_concat.txt"
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


# -------- ASS subtitle builder -----------------------------------------------

PUNCT_BREAK = set(".,!?;:")


def _ass_timestamp(seconds: float) -> str:
    """Convert seconds to ASS timestamp H:MM:SS.cs"""
    total_cs = int(round(seconds * 100))
    h, rem = divmod(total_cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _colorize_ass(text: str) -> str:
    """Wrap brand keywords in orange ASS inline colour tags."""
    words = text.split()
    out = []
    for word in words:
        bare = re.sub(r"[.,!?;:]+$", "", word)
        if bare in BRAND_KEYWORDS:
            out.append(f"{{\\c{BRAND_ORANGE}&}}{word}{{\\r}}")
        else:
            out.append(word)
    return " ".join(out)


def _words_in_range(transcript: dict, t_start: float, t_end: float) -> list[dict]:
    out: list[dict] = []
    for w in transcript.get("words", []):
        if w.get("type") != "word":
            continue
        ws = w.get("start")
        we = w.get("end")
        if ws is None or we is None:
            continue
        if we <= t_start or ws >= t_end:
            continue
        out.append(w)
    return out


def build_master_ass(edl: dict, edit_dir: Path, out_path: Path) -> None:
    """Build an output-timeline ASS file from per-source transcripts.

    Features vs plain SRT:
    - Fade in 120 ms / fade out 80 ms per cue  {\fad(120,80)}
    - Pop scale animation 107%→100% over 220 ms {\t(...)}
    - Brand keywords coloured in orange
    - Larger font (22pt) and higher MarginV (60) for portrait
    """
    transcripts_dir = edit_dir / "transcripts"
    sources = edl["sources"]

    header_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {ASS_PLAY_RES_X}",
        f"PlayResY: {ASS_PLAY_RES_Y}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        ASS_STYLE_ROW,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    dialogue_lines: list[str] = []
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
            ends_in_punct = bool(text) and text[-1] in PUNCT_BREAK
            if len(current) >= 2 or ends_in_punct:
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)

        for chunk in chunks:
            local_start = max(seg_start, chunk[0].get("start", seg_start))
            local_end = min(seg_end, chunk[-1].get("end", seg_end))
            out_start = max(0.0, local_start - seg_start) + seg_offset
            out_end = max(0.0, local_end - seg_start) + seg_offset
            if out_end <= out_start:
                out_end = out_start + 0.4

            text = " ".join((w.get("text") or "").strip() for w in chunk)
            text = re.sub(r"\s+", " ", text).strip()
            text = text.rstrip(",;:")
            text = text.upper()
            text = _colorize_ass(text)
            ass_text = ASS_CUE_FX + text

            line = (
                f"Dialogue: 0,{_ass_timestamp(out_start)},{_ass_timestamp(out_end)},"
                f"Default,,0,0,0,,{ass_text}"
            )
            dialogue_lines.append(line)

        seg_offset += seg_duration

    out_path.write_text("\n".join(header_lines) + "\n" + "\n".join(dialogue_lines) + "\n")
    print(f"master ASS → {out_path.name} ({len(dialogue_lines)} cues)")


# -------- SRT subtitle builder (legacy) --------------------------------------


def _srt_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_master_srt(edl: dict, edit_dir: Path, out_path: Path) -> None:
    """Build an output-timeline SRT from per-source transcripts (legacy)."""
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

        chunks: list[list[dict]] = []
        current: list[dict] = []
        for w in words_in_seg:
            text = (w.get("text") or "").strip()
            if not text:
                continue
            current.append(w)
            ends_in_punct = bool(text) and text[-1] in PUNCT_BREAK
            if len(current) >= 2 or ends_in_punct:
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)

        for chunk in chunks:
            local_start = max(seg_start, chunk[0].get("start", seg_start))
            local_end = min(seg_end, chunk[-1].get("end", seg_end))
            out_start = max(0.0, local_start - seg_start) + seg_offset
            out_end = max(0.0, local_end - seg_start) + seg_offset
            if out_end <= out_start:
                out_end = out_start + 0.4
            text = " ".join((w.get("text") or "").strip() for w in chunk)
            text = re.sub(r"\s+", " ", text).strip()
            text = text.rstrip(",;:")
            text = text.upper()
            entries.append((out_start, out_end, text))

        seg_offset += seg_duration

    entries.sort(key=lambda e: e[0])
    lines: list[str] = []
    for i, (a, b, t) in enumerate(entries, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(a)} --> {_srt_timestamp(b)}")
        lines.append(t)
        lines.append("")
    out_path.write_text("\n".join(lines))
    print(f"master SRT → {out_path.name} ({len(entries)} cues)")


# -------- Loudness normalization ---------------------------------------------

LOUDNORM_I = -14.0
LOUDNORM_TP = -1.0
LOUDNORM_LRA = 11.0


def measure_loudness(video_path: Path) -> dict[str, str] | None:
    filter_str = (
        f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}:print_format=json"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-i", str(video_path),
        "-af", filter_str,
        "-vn", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    stderr = proc.stderr
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(stderr[start : end + 1])
    except json.JSONDecodeError:
        return None
    needed = {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}
    if not needed.issubset(data.keys()):
        return None
    return data


def apply_loudnorm_two_pass(
    input_path: Path,
    output_path: Path,
    preview: bool = False,
) -> bool:
    if preview:
        filter_str = f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
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

    print(f"  loudnorm pass 1: measuring {input_path.name}")
    measurement = measure_loudness(input_path)
    if measurement is None:
        print("  loudnorm measurement failed — falling back to 1-pass")
        return apply_loudnorm_two_pass(input_path, output_path, preview=True)

    print(f"    measured: I={measurement['input_i']} LUFS  "
          f"TP={measurement['input_tp']}  LRA={measurement['input_lra']}")

    filter_str = (
        f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
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


# -------- Final compositing --------------------------------------------------


def build_final_composite(
    base_path: Path,
    overlays: list[dict],
    subtitles_path: Path | None,
    out_path: Path,
    edit_dir: Path,
    music: dict | None = None,
) -> None:
    """Final pass: base → overlays → subtitles → music mix → out.

    music dict keys:
      file       path to audio file (mp3/aac/wav)
      volume     float 0–1, default 0.12
      fade_in    seconds, default 2.0
      fade_out   seconds, default 2.0
    """
    has_overlays = bool(overlays)
    has_subs = subtitles_path is not None and subtitles_path.exists()
    has_music = bool(music and music.get("file"))

    if not has_overlays and not has_subs and not has_music:
        run(["ffmpeg", "-y", "-i", str(base_path), "-c", "copy", str(out_path)], quiet=True)
        return

    inputs: list[str] = ["-i", str(base_path)]

    # Overlay video inputs
    for ov in overlays:
        ov_path = resolve_path(ov["file"], edit_dir)
        inputs += ["-i", str(ov_path)]

    # Music input (last)
    music_idx: int | None = None
    if has_music:
        music_path = resolve_path(music["file"], edit_dir)  # type: ignore[index]
        inputs += ["-i", str(music_path)]
        music_idx = 1 + len(overlays)

    filter_parts: list[str] = []

    # PTS-shift overlays
    for idx, ov in enumerate(overlays, start=1):
        t = float(ov["start_in_output"])
        filter_parts.append(f"[{idx}:v]setpts=PTS-STARTPTS+{t}/TB[a{idx}]")

    # Chain overlays on base video
    current = "[0:v]"
    for idx, ov in enumerate(overlays, start=1):
        t = float(ov["start_in_output"])
        dur = float(ov["duration"])
        end = t + dur
        next_label = f"[v{idx}]"
        filter_parts.append(
            f"{current}[a{idx}]overlay=enable='between(t,{t:.3f},{end:.3f})'{next_label}"
        )
        current = next_label

    # Subtitles — detect format from extension
    if has_subs:
        subs_abs = str(subtitles_path.resolve()).replace("'", r"\'")
        ext = subtitles_path.suffix.lower()
        if ext == ".ass":
            # Use ass filter — honours embedded styles, no force_style needed
            subs_escaped = subs_abs.replace(":", r"\:")
            filter_parts.append(f"{current}ass='{subs_escaped}'[outv]")
        else:
            # SRT — apply legacy force_style
            subs_escaped = subs_abs.replace(":", r"\:")
            filter_parts.append(
                f"{current}subtitles='{subs_escaped}':force_style='{SUB_FORCE_STYLE}'[outv]"
            )
        out_video_label = "[outv]"
    else:
        if has_overlays:
            filter_parts.append(f"{current}null[outv]")
            out_video_label = "[outv]"
        else:
            out_video_label = "[0:v]"

    # Music mix
    out_audio_label = "0:a"
    if has_music and music_idx is not None:
        vol = float(music.get("volume", 0.12))          # type: ignore[union-attr]
        fi = float(music.get("fade_in", 2.0))           # type: ignore[union-attr]
        fo = float(music.get("fade_out", 2.0))          # type: ignore[union-attr]
        # Estimate total duration from EDL for fade-out start
        total_dur = float(music.get("total_duration_s", 60.0))
        fo_start = max(0.0, total_dur - fo)
        music_chain = (
            f"[{music_idx}:a]"
            f"volume={vol:.4f},"
            f"afade=t=in:st=0:d={fi},"
            f"afade=t=out:st={fo_start:.2f}:d={fo},"
            f"apad[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[outa]"
        )
        filter_parts.append(music_chain)
        out_audio_label = "[outa]"

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", out_video_label,
        "-map", out_audio_label,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(out_path),
    ]
    print(f"compositing → {out_path.name}")
    print(f"  overlays: {len(overlays)}, subtitles: {'yes (' + subtitles_path.suffix + ')' if has_subs else 'no'}, music: {'yes' if has_music else 'no'}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


# -------- Main ---------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Render a video from an EDL")
    ap.add_argument("edl", type=Path, help="Path to edl.json")
    ap.add_argument("-o", "--output", type=Path, required=True, help="Output video path")
    ap.add_argument("--preview", action="store_true",
                    help="Preview mode: 1080p medium CRF 22")
    ap.add_argument("--draft", action="store_true",
                    help="Draft mode: 720p ultrafast CRF 28")
    ap.add_argument("--build-subtitles", action="store_true",
                    help="Build master.ass (default) or master.srt (with --srt) from transcripts")
    ap.add_argument("--srt", action="store_true",
                    help="Use SRT format instead of ASS when --build-subtitles is set")
    ap.add_argument("--no-subtitles", action="store_true",
                    help="Skip subtitles entirely")
    ap.add_argument("--no-loudnorm", action="store_true",
                    help="Skip loudness normalisation")
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    if not edl_path.exists():
        sys.exit(f"edl not found: {edl_path}")

    edl = json.loads(edl_path.read_text())
    edit_dir = edl_path.parent
    out_path = args.output.resolve()

    # 1. Extract per-segment
    segment_paths = extract_all_segments(
        edl, edit_dir, preview=args.preview, draft=args.draft
    )

    # 2. Concat → base
    base_name = "base_draft.mp4" if args.draft else ("base_preview.mp4" if args.preview else "base.mp4")
    base_path = edit_dir / base_name
    concat_segments(segment_paths, base_path, edit_dir)

    # 3. Subtitles
    subs_path: Path | None = None
    if not args.no_subtitles:
        if args.build_subtitles:
            if args.srt:
                subs_path = edit_dir / "master.srt"
                build_master_srt(edl, edit_dir, subs_path)
            else:
                subs_path = edit_dir / "master.ass"
                build_master_ass(edl, edit_dir, subs_path)
        elif edl.get("subtitles"):
            subs_path = resolve_path(edl["subtitles"], edit_dir)
            if not subs_path.exists():
                print(f"warning: subtitles path in EDL does not exist: {subs_path}")
                subs_path = None

    # 4. Music config from EDL
    music_cfg = edl.get("music") or None
    if music_cfg:
        # Inject total_duration so fade-out lands correctly
        total_dur = sum(float(r["end"]) - float(r["start"]) for r in edl["ranges"])
        music_cfg = {**music_cfg, "total_duration_s": total_dur}
        print(f"music: {music_cfg.get('file')}  vol={music_cfg.get('volume', 0.12)}")

    # 5. Composite
    overlays = edl.get("overlays") or []
    if args.no_loudnorm:
        build_final_composite(base_path, overlays, subs_path, out_path, edit_dir, music=music_cfg)
    else:
        tmp_composite = out_path.with_suffix(".prenorm.mp4")
        build_final_composite(base_path, overlays, subs_path, tmp_composite, edit_dir, music=music_cfg)
        print("loudness normalization → social-ready (-14 LUFS / -1 dBTP / LRA 11)")
        apply_loudnorm_two_pass(tmp_composite, out_path, preview=args.draft)
        tmp_composite.unlink(missing_ok=True)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"\ndone: {out_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
