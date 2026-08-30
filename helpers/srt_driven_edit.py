"""SRT-driven edit: assemble a final cut by aligning source ranges to an SRT.

Independent pipeline. Does NOT touch the main render.py flow. Use when you
have a finished script (script.srt = final captions timeline) and a list of
source ranges keyed by SRT id.

Pipeline:
  parse SRT + plan ─> strict validate ─> align ─> resolve style
  ─> extract segments (with cache) ─> insert gap clips ─> concat
  ─> audio replace/mix + subtitle burn LAST (Hard Rule 1) ─> QC report

Schemas (both forms accepted):

  Form A — array, single source (legacy):
    [{"id": 1, "source_start": "HH:MM:SS,ms", "source_end": "HH:MM:SS,ms"}, ...]
    + CLI --source <path>

  Form B — object, multi-source / multi-voice:
    {
      "sources": {"A": "path/a.mp4", "B": "path/b.mp4"},
      "voices":  {"main": "path/v.wav"},
      "segments": [
        {"id": 1, "source": "A", "source_start": "...", "source_end": "...",
         "voice": "main"},
        {"id": 2, "source": "B", "source_start": "...", "source_end": "..."}
      ]
    }

Batch:
    --batch jobs.json    (array of per-job dicts, same fields as CLI flags)
    --batch jobs.csv     (header row of the same fields)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:
    from render import (
        SUB_FORCE_STYLE as _RENDER_SUB_STYLE,
        TONEMAP_CHAIN,
        is_hdr_source,
        is_portrait_source,
    )
except Exception:
    _RENDER_SUB_STYLE = (
        "FontName=Helvetica,FontSize=18,Bold=1,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
        "BorderStyle=1,Outline=2,Shadow=0,"
        "Alignment=2,MarginV=90"
    )
    TONEMAP_CHAIN = ""

    def is_hdr_source(video: Path) -> bool:  # type: ignore
        return False

    def is_portrait_source(video: Path) -> bool:  # type: ignore
        return False


# ============================================================================
# Constants
# ============================================================================

FPS = 24
SAMPLE_RATE = 48000
AUDIO_BITRATE = "192k"
DURATION_DRIFT_TOLERANCE_S = 0.2

STYLE_TEMPLATES: dict[str, str] = {
    "bold-uppercase": _RENDER_SUB_STYLE,
    "cjk-natural": (
        "FontName=Microsoft YaHei UI,FontSize=20,Bold=0,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
        "BorderStyle=1,Outline=2,Shadow=0,"
        "Alignment=2,MarginV=90"
    ),
    "narrative": (
        "FontName=Helvetica,FontSize=20,Bold=0,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
        "BorderStyle=1,Outline=2,Shadow=0,"
        "Alignment=2,MarginV=80"
    ),
}

CJK_RE = re.compile(
    r"[一-鿿㐀-䶿぀-ゟ゠-ヿ가-힯]"
)

CACHE_VERSION = 2  # bumped: cache now keyed by ffmpeg version + encoding params

# Encoding-affecting constants captured into a single fingerprint so that
# any later tweak to codec / preset / sync tails forces a cache miss. If you
# change PARAMS_FINGERPRINT's inputs, existing cached clips are auto-invalidated.
def _params_fingerprint() -> str:
    payload = repr([
        "fps", 24,
        "sr", 48000,
        "ab", "192k",
        "ac", 2,
        "v_codec", "libx264", "preset", "fast", "crf", 20, "pix", "yuv420p",
        "a_codec", "aac",
    ])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


# Encodings tried in order when reading user-supplied SRT files. Windows
# Chinese systems frequently save as GBK/GB18030; macOS / *nix typically
# UTF-8 (with or without BOM). cp1252 is the last-resort Western Latin1.
SRT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "cp936", "cp1252")

# Audio/video sync tails appended to every per-segment filter chain so that
# each extracted clip starts at PTS 0 with monotonic timestamps. Without
# these, concatenating many short clips accumulates sub-frame drift that
# eventually desyncs voice from picture.
V_SYNC_TAIL = f"fps={FPS},setpts=PTS-STARTPTS"
A_SYNC_TAIL = "aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS"

PARAMS_FINGERPRINT = _params_fingerprint()


# ============================================================================
# Path / filter escaping
# ============================================================================


def subs_filter_escape(path: Path) -> str:
    """Escape a path for use inside ffmpeg's subtitles='...' filter argument.

    Order matters: backslashes first (Windows), then drive-letter colons, then
    quotes. The path is returned in forward-slash form for libavfilter sanity.
    """
    s = path.resolve().as_posix()
    s = s.replace("\\", "\\\\")
    s = s.replace(":", r"\:")
    s = s.replace("'", r"\'")
    return s


def safe_ascii_name(stem: str) -> str:
    """Reduce a filename stem to a safe ASCII slug for intermediate files."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
    s = s.strip("_") or "job"
    return s[:48]


def concat_quote_path(p: Path) -> str:
    """Quote a path for ffmpeg's concat demuxer 'file' directive.

    Embeds single quotes via the close-escape-reopen idiom: `'` -> `'\\''`.
    Paths are normalized to posix form so backslashes do not become escape
    sequences when libavformat parses the list.
    """
    s = p.resolve().as_posix()
    escaped = s.replace("'", "'\\''")
    return f"'{escaped}'"


def read_srt_text(path: Path) -> str:
    """Read an SRT with encoding fallback.

    Tries SRT_ENCODINGS in order; returns the first successful decode.
    Raises SystemExit with a helpful message if none work.
    """
    raw = path.read_bytes()
    last_err: Exception | None = None
    for enc in SRT_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError as e:
            last_err = e
            continue
    raise SystemExit(
        f"could not decode SRT {path} with any of {SRT_ENCODINGS}: {last_err}"
    )


def make_safe_work_dir(job_name: str, plan_path: Path) -> Path:
    """Create (or reset) a safe ASCII-named temp dir for one job's intermediates.

    Lives under tempfile.gettempdir() so it never inherits CJK / quote /
    space characters from the user's project path. Deterministic hash means
    re-runs land in the same dir for debuggability.
    """
    h = hashlib.sha1(
        f"{plan_path.resolve().as_posix()}|{job_name}".encode("utf-8")
    ).hexdigest()[:12]
    p = Path(tempfile.gettempdir()) / f"srt_edit_{h}"
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True)
    return p


def _path_is_filter_safe(p: Path) -> bool:
    """Cheap libavfilter-path safety check: ASCII only and no single quotes."""
    s = str(p)
    return s.isascii() and "'" not in s


def ensure_safe_subs_path(src: Path) -> tuple[Path, Path | None]:
    """Return (path_to_feed_to_ffmpeg, cleanup_target_or_None).

    If src is already filter-safe, return it as-is and no cleanup target.
    Otherwise copy to a deterministic ASCII path under the system temp dir
    and return that, plus a handle the caller should unlink in finally.

    Decoded through read_srt_text so GB18030 / cp936 inputs become UTF-8.
    """
    if _path_is_filter_safe(src):
        return src, None
    h = hashlib.sha1(src.resolve().as_posix().encode("utf-8")).hexdigest()[:12]
    safe = Path(tempfile.gettempdir()) / f"srt_burn_{h}.srt"
    safe.write_text(read_srt_text(src), encoding="utf-8")
    return safe, safe


# ============================================================================
# Preflight: tool availability + media stream probing
# ============================================================================


_FFMPEG_VERSION_RE = re.compile(r"^ffmpeg version (\S+)")
_FFPROBE_VERSION_RE = re.compile(r"^ffprobe version (\S+)")


def preflight() -> dict[str, str]:
    """Verify ffmpeg + ffprobe are on PATH and runnable. Return version dict.

    Used both for early failure and to fingerprint cache keys: encoding
    behavior can shift between ffmpeg versions, so a version bump should
    invalidate cached clips.
    """
    info: dict[str, str] = {}
    for tool, rx in (("ffmpeg", _FFMPEG_VERSION_RE), ("ffprobe", _FFPROBE_VERSION_RE)):
        try:
            r = subprocess.run(
                [tool, "-version"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
        except FileNotFoundError:
            raise SystemExit(
                f"required tool not on PATH: {tool}. Install ffmpeg first "
                f"(e.g. `winget install Gyan.FFmpeg` on Windows, "
                f"`brew install ffmpeg` on macOS)."
            )
        except subprocess.TimeoutExpired:
            raise SystemExit(f"{tool} timed out on `-version`. Bad install?")
        if r.returncode != 0:
            raise SystemExit(
                f"{tool} `-version` exited {r.returncode}: {(r.stderr or '')[:300]}"
            )
        first_line = (r.stdout.splitlines() or [""])[0].strip()
        m = rx.match(first_line)
        info[tool] = m.group(1) if m else first_line[:40] or "unknown"
    return info


def probe_streams(path: Path) -> dict:
    """Probe a media file for {has_video, has_audio, duration}.

    Raises SystemExit on probe failure so the caller doesn't continue
    blindly. Result is cheap to memoize per source path.
    """
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=codec_type",
                "-show_entries", "format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, check=True,
            encoding="utf-8", errors="replace",
        )
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            f"ffprobe failed on {path}: {(e.stderr or '')[:300]}"
        )
    data = json.loads(r.stdout)
    types: set[str] = set()
    for s in data.get("streams", []) or []:
        t = s.get("codec_type")
        if t:
            types.add(t)
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "has_video": "video" in types,
        "has_audio": "audio" in types,
        "duration": duration,
    }


# ============================================================================
# Time parsing
# ============================================================================


_TS_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def parse_timestamp(ts: str) -> float:
    m = _TS_RE.fullmatch(ts.strip())
    if not m:
        raise ValueError(f"bad timestamp: {ts!r}")
    h, mn, s, ms = m.groups()
    return int(h) * 3600 + int(mn) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def format_srt_ts(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass
class SrtCue:
    id: int
    final_start: float
    final_end: float
    text: str

    @property
    def duration(self) -> float:
        return self.final_end - self.final_start


@dataclass
class PlanEntry:
    id: int
    source_name: str       # key into sources map (Form A: synthetic "_default")
    source_start: float
    source_end: float
    voice_name: str | None = None  # key into voices map

    @property
    def duration(self) -> float:
        return self.source_end - self.source_start


@dataclass
class Segment:
    id: int
    source_path: Path
    source_start: float
    source_end: float
    out_start: float
    out_end: float
    leading_gap: float
    text: str
    voice_path: Path | None
    pad_short: bool = False
    plan_src_dur: float = 0.0

    @property
    def duration(self) -> float:
        return self.out_end - self.out_start


# ============================================================================
# SRT parser + validation
# ============================================================================


def _split_time_line(line: str) -> tuple[str, str]:
    """Split an SRT time line into (start_ts, end_ts) strings.

    Tolerates trailing cue settings like 'position:90% align:start' by
    keeping only the first whitespace-delimited token on each side of '-->'.
    """
    parts = line.split("-->", 1)
    if len(parts) != 2:
        raise ValueError(f"missing '-->' in time line: {line!r}")
    left_tokens = parts[0].strip().split()
    right_tokens = parts[1].strip().split()
    if not left_tokens or not right_tokens:
        raise ValueError(f"missing timestamps in time line: {line!r}")
    return left_tokens[-1], right_tokens[0]


def parse_srt(path: Path) -> list[SrtCue]:
    raw = read_srt_text(path)
    blocks = re.split(r"\r?\n\r?\n+", raw.strip())
    cues: list[SrtCue] = []
    for block in blocks:
        lines = [ln.rstrip() for ln in block.splitlines() if ln.strip() != ""]
        if len(lines) < 2:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            raise SystemExit(f"SRT block missing numeric id: {lines[0]!r}")
        if "-->" not in lines[1]:
            raise SystemExit(f"SRT block missing time line: {lines[1]!r}")
        try:
            a, b = _split_time_line(lines[1])
            start = parse_timestamp(a)
            end = parse_timestamp(b)
        except ValueError as e:
            raise SystemExit(f"SRT id={lines[0]}: {e}")
        cues.append(SrtCue(id=idx, final_start=start, final_end=end,
                           text="\n".join(lines[2:])))
    return cues


def validate_srt(cues: list[SrtCue]) -> None:
    if not cues:
        raise SystemExit("SRT has no cues")
    seen: set[int] = set()
    for c in cues:
        if c.id in seen:
            raise SystemExit(f"SRT duplicate id: {c.id}")
        seen.add(c.id)
        if c.final_end <= c.final_start:
            raise SystemExit(
                f"SRT id={c.id}: end {c.final_end:.3f} <= start {c.final_start:.3f}"
            )
        if c.final_start < 0:
            raise SystemExit(f"SRT id={c.id}: negative start {c.final_start:.3f}")
    sorted_cues = sorted(cues, key=lambda x: x.id)
    for i in range(1, len(sorted_cues)):
        prev, cur = sorted_cues[i - 1], sorted_cues[i]
        if cur.final_start < prev.final_start:
            raise SystemExit(
                f"SRT non-monotonic by id: id={cur.id} starts at "
                f"{cur.final_start:.3f}s, earlier than id={prev.id} at "
                f"{prev.final_start:.3f}s"
            )
        if cur.final_start < prev.final_end - 1e-6:
            raise SystemExit(
                f"SRT cue overlap: id={prev.id} ends {prev.final_end:.3f}, "
                f"id={cur.id} starts {cur.final_start:.3f}"
            )


# ============================================================================
# Plan parser + validation
# ============================================================================


def parse_plan(path: Path) -> tuple[dict[str, Path], dict[str, Path], list[PlanEntry]]:
    """Returns (sources_map, voices_map, entries). Detects Form A vs B."""
    data = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent

    if isinstance(data, list):
        entries: list[PlanEntry] = []
        for row in data:
            entries.append(PlanEntry(
                id=int(row["id"]),
                source_name="_default",
                source_start=parse_timestamp(row["source_start"]),
                source_end=parse_timestamp(row["source_end"]),
                voice_name=None,
            ))
        return {}, {}, entries

    if not isinstance(data, dict):
        raise SystemExit("edit_plan must be a JSON array or object")
    if "segments" not in data:
        raise SystemExit("Form B plan missing 'segments' field")

    sources_map: dict[str, Path] = {}
    for name, p in (data.get("sources") or {}).items():
        sp = Path(p)
        if not sp.is_absolute():
            sp = (base / sp).resolve()
        sources_map[name] = sp

    voices_map: dict[str, Path] = {}
    for name, p in (data.get("voices") or {}).items():
        vp = Path(p)
        if not vp.is_absolute():
            vp = (base / vp).resolve()
        voices_map[name] = vp

    entries = []
    for row in data["segments"]:
        entries.append(PlanEntry(
            id=int(row["id"]),
            source_name=str(row["source"]),
            source_start=parse_timestamp(row["source_start"]),
            source_end=parse_timestamp(row["source_end"]),
            voice_name=row.get("voice"),
        ))
    return sources_map, voices_map, entries


def validate_plan(
    entries: list[PlanEntry],
    sources_map: dict[str, Path],
    voices_map: dict[str, Path],
    legacy_default_source: Path | None,
) -> None:
    if not entries:
        raise SystemExit("edit_plan has no segments")
    seen: set[int] = set()
    for e in entries:
        if e.id in seen:
            raise SystemExit(f"plan duplicate id: {e.id}")
        seen.add(e.id)
        if e.source_start < 0:
            raise SystemExit(f"plan id={e.id}: negative source_start {e.source_start}")
        if e.source_end <= e.source_start:
            raise SystemExit(
                f"plan id={e.id}: source_end {e.source_end:.3f} <= "
                f"source_start {e.source_start:.3f}"
            )
        if e.source_name == "_default":
            if legacy_default_source is None:
                raise SystemExit(
                    "Form A plan requires --source <path> at the CLI"
                )
        else:
            if e.source_name not in sources_map:
                raise SystemExit(
                    f"plan id={e.id}: source '{e.source_name}' not in sources map"
                )
        if e.voice_name is not None and e.voice_name not in voices_map:
            raise SystemExit(
                f"plan id={e.id}: voice '{e.voice_name}' not in voices map"
            )
    for name, sp in sources_map.items():
        if not sp.exists():
            raise SystemExit(f"source '{name}' missing on disk: {sp}")
    for name, vp in voices_map.items():
        if not vp.exists():
            raise SystemExit(f"voice '{name}' missing on disk: {vp}")
    if legacy_default_source is not None and not legacy_default_source.exists():
        raise SystemExit(f"--source missing on disk: {legacy_default_source}")


def validate_alignment(cues: list[SrtCue], entries: list[PlanEntry]) -> None:
    cue_ids = {c.id for c in cues}
    plan_ids = {e.id for e in entries}
    if cue_ids != plan_ids:
        only_srt = cue_ids - plan_ids
        only_plan = plan_ids - cue_ids
        msg = []
        if only_srt:
            msg.append(f"in SRT but not in plan: {sorted(only_srt)}")
        if only_plan:
            msg.append(f"in plan but not in SRT: {sorted(only_plan)}")
        raise SystemExit("id mismatch: " + "; ".join(msg))


# ============================================================================
# Alignment
# ============================================================================


def align(
    cues: list[SrtCue],
    entries: list[PlanEntry],
    sources_map: dict[str, Path],
    voices_map: dict[str, Path],
    legacy_default_source: Path | None,
    tolerance: float,
    trim_direction: str,
    on_short: str,
) -> list[Segment]:
    cue_by_id = {c.id: c for c in cues}
    plan_by_id = {e.id: e for e in entries}

    segments: list[Segment] = []
    prev_out_end = 0.0
    for cid in sorted(cue_by_id):
        cue = cue_by_id[cid]
        pln = plan_by_id[cid]
        src_dur = pln.duration
        target = cue.duration

        pad_short = False
        if src_dur + tolerance < target:
            short_by = target - src_dur
            if on_short == "error":
                raise SystemExit(
                    f"id={cid}: source is {short_by:.3f}s shorter than SRT target "
                    f"({src_dur:.3f}s vs {target:.3f}s). Pass --on-short=pad to "
                    f"freeze-pad the tail, or extend the source range."
                )
            pad_short = True
            src_start = pln.source_start
            src_end = pln.source_end
        elif src_dur > target + tolerance:
            if trim_direction == "tail":
                src_start = pln.source_start
                src_end = pln.source_start + target
            elif trim_direction == "head":
                src_start = pln.source_end - target
                src_end = pln.source_end
            elif trim_direction == "center":
                overhang = (src_dur - target) / 2
                src_start = pln.source_start + overhang
                src_end = pln.source_end - overhang
            else:
                raise ValueError(f"unknown trim_direction: {trim_direction}")
        else:
            src_start = pln.source_start
            src_end = pln.source_start + target

        if pln.source_name == "_default":
            assert legacy_default_source is not None
            source_path = legacy_default_source
        else:
            source_path = sources_map[pln.source_name]

        voice_path = voices_map[pln.voice_name] if pln.voice_name else None
        gap = max(0.0, cue.final_start - prev_out_end)
        segments.append(Segment(
            id=cid,
            source_path=source_path,
            source_start=src_start,
            source_end=src_end,
            out_start=cue.final_start,
            out_end=cue.final_end,
            leading_gap=gap,
            text=cue.text,
            voice_path=voice_path,
            pad_short=pad_short,
            plan_src_dur=src_dur,
        ))
        prev_out_end = cue.final_end

    return segments


# ============================================================================
# Style resolution
# ============================================================================


def has_cjk(cues: list[SrtCue]) -> bool:
    return any(CJK_RE.search(c.text) for c in cues)


def resolve_style(style_arg: str, cues: list[SrtCue]) -> str:
    if style_arg == "auto":
        return STYLE_TEMPLATES["cjk-natural" if has_cjk(cues) else "bold-uppercase"]
    if style_arg in STYLE_TEMPLATES:
        return STYLE_TEMPLATES[style_arg]
    if "=" in style_arg:
        return style_arg
    raise SystemExit(
        f"unknown style: {style_arg!r}. Known templates: "
        f"{sorted(STYLE_TEMPLATES)}. Pass a raw ASS string with '=' to override."
    )


# ============================================================================
# Clip cache
# ============================================================================


def _file_fingerprint(path: Path) -> tuple[int, int]:
    st = path.stat()
    return (int(st.st_mtime_ns), st.st_size)


def cache_key(seg: Segment, effective_bg_volume: float, hdr: bool,
              portrait: bool, voice_signature: tuple | None,
              ffmpeg_version: str) -> str:
    fp = _file_fingerprint(seg.source_path)
    payload = json.dumps([
        CACHE_VERSION,
        str(seg.source_path.resolve()), fp[0], fp[1],
        round(seg.source_start, 4), round(seg.source_end, 4),
        round(seg.duration, 4),
        round(effective_bg_volume, 4),
        hdr, portrait,
        seg.pad_short, round(seg.plan_src_dur, 4),
        PARAMS_FINGERPRINT,
        ffmpeg_version,
        voice_signature,
    ], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def cache_lookup(cache_dir: Path, key: str) -> Path | None:
    p = cache_dir / f"{key}.mp4"
    return p if p.exists() else None


def cache_store(cache_dir: Path, key: str, clip_path: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(clip_path, cache_dir / f"{key}.mp4")


# ============================================================================
# ffmpeg orchestration
# ============================================================================


def run_ff(cmd: list[str], desc: str) -> None:
    print(f"  $ {desc}")
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or "")
        raise SystemExit(f"ffmpeg failed: {desc}")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def scale_filter_for(source: Path) -> str:
    return "scale=-2:1920" if is_portrait_source(source) else "scale=1920:-2"


def _voice_signature(voice_path: Path | None, target: float) -> tuple | None:
    if voice_path is None:
        return None
    fp = _file_fingerprint(voice_path)
    return (str(voice_path.resolve()), fp[0], fp[1], round(target, 4))


def extract_segment(
    seg: Segment,
    out_path: Path,
    bg_volume: float,
) -> None:
    """Extract one segment to 1080p 24fps with audio resolved per-segment.

    `bg_volume` here is the EFFECTIVE level — callers must already have
    zeroed it for sources whose ffprobe says there is no audio track.

    Audio resolution:
      voice_path present + bg_volume > 0  → mix voice + source*bg
      voice_path present + bg_volume == 0 → voice only
      voice_path absent  + bg_volume > 0  → source audio at bg_volume (fades)
      voice_path absent  + bg_volume == 0 → silent
    """
    keep_audio_from_source = bg_volume > 0.0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    target = seg.duration

    vf_parts: list[str] = []
    if is_hdr_source(seg.source_path):
        vf_parts.append(TONEMAP_CHAIN)
    vf_parts.append(scale_filter_for(seg.source_path))

    if seg.pad_short and seg.plan_src_dur + 1e-6 < target:
        vf_parts.append(
            f"tpad=stop_mode=clone:stop_duration={target - seg.plan_src_dur:.3f}"
        )
        v_input_dur = seg.plan_src_dur
    else:
        v_input_dur = target

    vf_parts.append(V_SYNC_TAIL)
    vf = ",".join(vf_parts)

    inputs: list[str] = [
        "-ss", f"{seg.source_start:.3f}",
        "-i", str(seg.source_path),
        "-t", f"{v_input_dur:.3f}",
    ]

    has_voice = seg.voice_path is not None
    voice_index: int | None = None
    if has_voice:
        voice_index = 1
        inputs += ["-i", str(seg.voice_path)]

    # Audio filter graph — applied via -filter_complex when we have voice,
    # otherwise simple -af on source audio.
    audio_args: list[str] = []
    if has_voice and bg_volume <= 0.0:
        fade_out = max(0.0, target - 0.03)
        ac_parts = [
            f"[{voice_index}:a]apad=whole_dur={target:.3f},"
            f"atrim=duration={target:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d=0.03,"
            f"afade=t=out:st={fade_out:.3f}:d=0.03,"
            f"{A_SYNC_TAIL}[outa]"
        ]
        audio_args = ["-filter_complex", ";".join(ac_parts),
                      "-map", "[outa]"]
    elif has_voice and bg_volume > 0.0:
        fade_out = max(0.0, target - 0.03)
        ac_parts = [
            f"[{voice_index}:a]apad=whole_dur={target:.3f},"
            f"atrim=duration={target:.3f},asetpts=PTS-STARTPTS[voice]",
            f"[0:a]volume={bg_volume:.3f},"
            f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out:.3f}:d=0.03[bg]",
            f"[voice][bg]amix=inputs=2:duration=first:normalize=0,"
            f"{A_SYNC_TAIL}[outa]",
        ]
        audio_args = ["-filter_complex", ";".join(ac_parts),
                      "-map", "[outa]"]
    elif not has_voice and keep_audio_from_source:
        fade_out = max(0.0, target - 0.03)
        af = (
            f"volume={bg_volume:.3f},"
            f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out:.3f}:d=0.03,"
            f"{A_SYNC_TAIL}"
        )
        if seg.pad_short and seg.plan_src_dur + 1e-6 < target:
            af = f"apad=whole_dur={target:.3f},{af}"
        audio_args = ["-af", af, "-map", "0:a"]
    else:
        # silent track via lavfi so concat inputs share an audio stream
        inputs += [
            "-f", "lavfi", "-t", f"{target:.3f}",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE}",
        ]
        silent_idx = 2 if has_voice else 1
        audio_args = ["-af", A_SYNC_TAIL, "-map", f"{silent_idx}:a"]

    cmd: list[str] = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        *inputs,
        "-vf", vf, "-r", str(FPS),
        "-map", "0:v",
        *audio_args,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-t", f"{target:.3f}",
        "-movflags", "+faststart",
        str(out_path),
    ]
    run_ff(cmd, f"extract id={seg.id}  src[{seg.source_start:.2f}-{seg.source_end:.2f}] → {out_path.name}")


def make_gap_clip(duration: float, portrait: bool, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    size = "1080x1920" if portrait else "1920x1080"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-f", "lavfi", "-i", f"color=c=black:s={size}:r={FPS}:d={duration:.3f}",
        "-f", "lavfi", "-i",
        f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE}",
        "-t", f"{duration:.3f}",
        "-vf", V_SYNC_TAIL,
        "-af", A_SYNC_TAIL,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(SAMPLE_RATE),
        "-movflags", "+faststart",
        str(out_path),
    ]
    run_ff(cmd, f"gap {duration:.3f}s → {out_path.name}")


def concat_clips(clip_paths: list[Path], out_path: Path, work_dir: Path) -> None:
    """Concat losslessly via the demuxer. work_dir is assumed safe-ASCII.

    Each line is `file <quoted-path>` with the quoting routine that handles
    spaces, single quotes, and CJK. Callers should register the list file
    for cleanup BEFORE this is invoked so a mid-write failure still cleans up.
    """
    concat_list = work_dir / "_concat_srt_driven.txt"
    lines = [f"file {concat_quote_path(p)}\n" for p in clip_paths]
    concat_list.write_text("".join(lines), encoding="utf-8")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]
    run_ff(cmd, f"concat {len(clip_paths)} clips → {out_path.name}")


def burn_subtitles(
    base_path: Path,
    subs_path: Path,
    style: str,
    fontsdir: Path | None,
    out_path: Path,
    *,
    global_voice: Path | None = None,
    total_duration: float = 0.0,
) -> None:
    """Final pass: optional global-voice mix + subtitle burn (LAST).

    Self-defending on subs_path: if not filter-safe, copied to a deterministic
    temp SRT first so libavfilter never sees the problematic original.
    fontsdir, if given, must already be filter-safe — we error rather than
    copy an entire font directory.

    Audio handling:
      - global_voice is None: pass base audio through (`-c:a copy`).
      - global_voice given: voice is apad'd / atrim'd to exactly total_duration
        so it spans the entire output timeline, then mixed on top of base's
        audio. Base already contains source*bg_volume (or silence) from
        extract_segment, so we do NOT re-scale it here — that would double-
        attenuate the background. amix uses duration=first so the result
        runs exactly total_duration; normalize=0 keeps levels predictable.
    """
    if fontsdir is not None and not _path_is_filter_safe(fontsdir):
        raise SystemExit(
            f"fontsdir contains non-ASCII or single-quote characters; "
            f"move it to a safe ASCII path first: {fontsdir}"
        )

    safe_subs, cleanup_target = ensure_safe_subs_path(subs_path)
    try:
        subs_arg = subs_filter_escape(safe_subs)
        style_escaped = style.replace("'", r"\'")
        if fontsdir is not None:
            fd = subs_filter_escape(fontsdir)
            subs_filter = f"subtitles='{subs_arg}':fontsdir='{fd}':force_style='{style_escaped}'"
        else:
            subs_filter = f"subtitles='{subs_arg}':force_style='{style_escaped}'"

        if global_voice is None:
            # No audio work — just burn subtitles, copy audio.
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-nostats",
                "-i", str(base_path),
                "-vf", subs_filter,
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(out_path),
            ]
            label = f"subtitle burn (LAST) → {out_path.name}"
        else:
            if total_duration <= 0.0:
                raise SystemExit(
                    "burn_subtitles: total_duration must be > 0 when global_voice is set"
                )
            voice_chain = (
                f"[1:a]apad=whole_dur={total_duration:.3f},"
                f"atrim=duration={total_duration:.3f},"
                f"asetpts=PTS-STARTPTS,"
                f"{A_SYNC_TAIL}"
            )
            # base [0:a] already contains source*bg_volume from extract; do NOT
            # apply bg_volume again here. amix combines voice + existing base
            # audio (which is silent on gaps and on segments with bg_volume=0).
            filter_complex = (
                f"[0:v]{subs_filter}[outv];"
                f"{voice_chain}[voice];"
                f"[voice][0:a]amix=inputs=2:duration=first:normalize=0[outa]"
            )
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-nostats",
                "-i", str(base_path),
                "-i", str(global_voice),
                "-filter_complex", filter_complex,
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(SAMPLE_RATE),
                "-movflags", "+faststart",
                str(out_path),
            ]
            label = f"subtitle burn (LAST) + global voice mix → {out_path.name}"

        run_ff(cmd, label)
    finally:
        if cleanup_target is not None:
            try:
                cleanup_target.unlink()
            except OSError:
                pass


# ============================================================================
# EDL + QC artifacts
# ============================================================================


def write_edl(segments: list[Segment], srt: Path, plan: Path,
              bg_volume: float, style_name: str, out_path: Path) -> None:
    edl = {
        "version": "srt-driven-2",
        "script_srt": str(srt.resolve()),
        "plan": str(plan.resolve()),
        "bg_volume": bg_volume,
        "style": style_name,
        "segments": [
            {
                "id": s.id,
                "source": str(s.source_path.resolve()),
                "source_start": format_srt_ts(s.source_start),
                "source_end": format_srt_ts(s.source_end),
                "out_start": format_srt_ts(s.out_start),
                "out_end": format_srt_ts(s.out_end),
                "duration": round(s.duration, 3),
                "leading_gap": round(s.leading_gap, 3),
                "voice": str(s.voice_path.resolve()) if s.voice_path else None,
                "pad_short": s.pad_short,
                "text": s.text,
            }
            for s in segments
        ],
        "total_duration_s": round(segments[-1].out_end, 3) if segments else 0.0,
    }
    out_path.write_text(json.dumps(edl, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  EDL → {out_path.name}")


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def build_qc_report(
    job_name: str,
    segments: list[Segment],
    seg_clip_info: list[dict],
    output_path: Path,
    expected_duration: float,
    style_name: str,
    style_resolved: str,
    bg_volume: float,
    has_any_voice: bool,
    elapsed_s: float,
    edit_dir: Path,
    work_dir: Path,
    cache_dir: Path,
    out_qc_path: Path,
) -> dict:
    actual_dur = probe_duration(output_path)
    drift_ms = round((actual_dur - expected_duration) * 1000)

    audio_mode = (
        "voice_replace" if has_any_voice and bg_volume <= 0.0
        else "voice_mix" if has_any_voice
        else "original_only" if bg_volume > 0.0
        else "silent"
    )

    seg_records = []
    for seg, info in zip(segments, seg_clip_info):
        actual_seg = probe_duration(info["clip_path"]) if Path(info["clip_path"]).exists() else 0.0
        seg_records.append({
            "id": seg.id,
            "expected_duration_s": round(seg.duration, 3),
            "actual_duration_s": round(actual_seg, 3),
            "drift_ms": round((actual_seg - seg.duration) * 1000),
            "cached": info["cached"],
            "clip_size_bytes": Path(info["clip_path"]).stat().st_size if Path(info["clip_path"]).exists() else 0,
            "source": str(seg.source_path),
            "voice": str(seg.voice_path) if seg.voice_path else None,
        })

    clips_size = sum(s["clip_size_bytes"] for s in seg_records)
    final_size = output_path.stat().st_size
    cache_size = _dir_size(cache_dir)
    work_dir_size = _dir_size(work_dir)

    report = {
        "job": job_name,
        "ok": abs(actual_dur - expected_duration) <= DURATION_DRIFT_TOLERANCE_S,
        "elapsed_s": round(elapsed_s, 2),
        "duration": {
            "expected_s": round(expected_duration, 3),
            "actual_s": round(actual_dur, 3),
            "drift_ms": drift_ms,
            "tolerance_ms": int(DURATION_DRIFT_TOLERANCE_S * 1000),
            "within_tolerance": abs(actual_dur - expected_duration) <= DURATION_DRIFT_TOLERANCE_S,
        },
        "segments": seg_records,
        "subtitles": {
            "applied": True,
            "style_name": style_name,
            "force_style": style_resolved,
            "cue_count": len(segments),
        },
        "audio": {
            "mode": audio_mode,
            "bg_volume": bg_volume,
            "voice_used": has_any_voice,
        },
        "disk_usage_bytes": {
            "work_dir_total": work_dir_size,
            "clips_in_work_dir": clips_size,
            "final_output": final_size,
            "cache": cache_size,
        },
        "output_path": str(output_path),
    }
    out_qc_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  QC report → {out_qc_path.name}")
    return report


# ============================================================================
# Single-job runner
# ============================================================================


@dataclass
class Job:
    source: Path | None         # legacy single-source path; None if Form B
    srt: Path
    plan: Path
    voice: Path | None          # global voice override (mutually exclusive with per-segment)
    bg_volume: float
    tolerance: float
    trim_direction: str
    on_short: str
    style: str
    fontsdir: Path | None
    output: Path | None
    name: str
    no_cache: bool
    keep_intermediates: bool
    no_overwrite: bool = False


def run_job(job: Job, ffmpeg_version: str) -> dict:
    t0 = time.time()
    print(f"\n== job: {job.name} ==")

    cues = parse_srt(job.srt)
    validate_srt(cues)
    sources_map, voices_map, entries = parse_plan(job.plan)

    legacy_source: Path | None = None
    if sources_map:
        if job.source is not None:
            print("  note: --source ignored (plan defines its own sources)")
    else:
        if job.source is None:
            raise SystemExit("Form A plan needs --source <path>")
        legacy_source = job.source.resolve()

    has_per_seg_voice = any(e.voice_name for e in entries)
    if job.voice is not None and has_per_seg_voice:
        raise SystemExit(
            "voice conflict: --voice given AND plan contains per-segment voices. "
            "Pick one."
        )

    # Global voice is NOT expanded into per-segment entries. Per-segment voices
    # play during their segment's window; a global voice spans the entire
    # output timeline and is mixed in during the final compose step. Doing it
    # at extract time would replay voice[0:seg_dur] for every segment, which
    # is wrong for any voice longer than one segment.
    global_voice: Path | None = job.voice
    if global_voice is not None:
        v_info = probe_streams(global_voice)
        if not v_info["has_audio"]:
            raise SystemExit(f"global --voice file has no audio track: {global_voice}")
        print(f"  global voice: {global_voice.name} ({v_info['duration']:.3f}s)")

    validate_plan(entries, sources_map, voices_map, legacy_source)
    validate_alignment(cues, entries)

    # Probe every source once. Cache by Path to avoid repeat ffprobe calls
    # when many segments share a source.
    unique_sources: dict[str, Path] = {}
    if legacy_source is not None:
        unique_sources["_default"] = legacy_source
    for name, p in sources_map.items():
        unique_sources[name] = p

    source_info: dict[str, dict] = {}
    source_info_by_path: dict[Path, dict] = {}
    print("  probing sources:")
    for name, p in unique_sources.items():
        info = probe_streams(p)
        source_info[name] = info
        source_info_by_path[p] = info
        print(f"    {name}: video={info['has_video']} audio={info['has_audio']} "
              f"duration={info['duration']:.3f}s")
        if not info["has_video"]:
            raise SystemExit(f"source '{name}' has no video stream: {p}")

    # Range bounds — fail fast rather than letting ffmpeg fail mid-batch.
    for e in entries:
        info = source_info[e.source_name]
        if e.source_end > info["duration"] + job.tolerance:
            raise SystemExit(
                f"plan id={e.id}: source_end {e.source_end:.3f}s exceeds "
                f"source '{e.source_name}' duration {info['duration']:.3f}s "
                f"(tolerance ±{job.tolerance}s)"
            )

    # Effective bg_volume per source: if source has no audio track, force to 0
    # rather than letting ffmpeg fail on a missing 0:a stream reference.
    no_audio_names = [n for n, info in source_info.items() if not info["has_audio"]]
    if no_audio_names and job.bg_volume > 0.0:
        print(f"  WARNING: source(s) {no_audio_names} have no audio track — "
              f"bg_volume forced to 0 for segments from them")

    segments = align(
        cues, entries, sources_map, voices_map, legacy_source,
        tolerance=job.tolerance, trim_direction=job.trim_direction,
        on_short=job.on_short,
    )

    edit_dir = (job.output.parent if job.output else job.plan.parent / "edit")
    edit_dir.mkdir(parents=True, exist_ok=True)
    out_path = job.output.resolve() if job.output else (
        edit_dir / f"final_srt_driven_{safe_ascii_name(job.name)}.mp4"
    )

    if out_path.exists():
        if job.no_overwrite:
            raise SystemExit(f"output exists and --no-overwrite set: {out_path}")
        print(f"  WARNING: overwriting existing output: {out_path}")

    style_resolved = resolve_style(job.style, cues)
    print(f"  style: {job.style} ({len(cues)} cues, cjk={has_cjk(cues)})")

    # All intermediates live in a safe-ASCII temp dir under tempfile.gettempdir().
    # Wiped at start so a previous crashed run cannot pollute. Wiped at end
    # (in finally) unless --keep-intermediates is set.
    work_dir = make_safe_work_dir(job.name, job.plan)
    print(f"  work dir: {work_dir}")

    try:
        # SRT normalized to UTF-8 with encoding fallback (handles GB18030 input).
        # Lives in the safe work dir so its path is guaranteed friendly to libass.
        safe_subs = work_dir / "subs.srt"
        safe_subs.write_text(read_srt_text(job.srt), encoding="utf-8")

        edl_path = edit_dir / f"edl_srt_driven_{safe_ascii_name(job.name)}.json"
        write_edl(segments, job.srt, job.plan, job.bg_volume, job.style, edl_path)

        clips_dir = work_dir / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        cache_dir = edit_dir / "cache_srt_driven"

        portrait = is_portrait_source(segments[0].source_path)

        clip_paths: list[Path] = []
        seg_clip_info: list[dict] = []
        any_voice = any(s.voice_path is not None for s in segments)

        print(f"\n  extracting {len(segments)} segments  cache={'off' if job.no_cache else 'on'}  voice={'per-seg' if any_voice else 'none'}")
        for i, seg in enumerate(segments):
            if seg.leading_gap > 0.001:
                gap_path = clips_dir / f"gap_{i:02d}_{seg.leading_gap:.3f}.mp4"
                if not gap_path.exists():
                    make_gap_clip(seg.leading_gap, portrait, gap_path)
                clip_paths.append(gap_path)

            seg_path = clips_dir / f"seg_{i:02d}_id{seg.id}.mp4"
            voice_sig = _voice_signature(seg.voice_path, seg.duration)

            # Effective bg_volume for THIS segment: forced to 0 if its source
            # has no audio track. Keeps ffmpeg from referencing a missing 0:a.
            src_has_audio = source_info_by_path[seg.source_path]["has_audio"]
            effective_bg = job.bg_volume if src_has_audio else 0.0

            ck = cache_key(
                seg,
                effective_bg_volume=effective_bg,
                hdr=is_hdr_source(seg.source_path),
                portrait=portrait,
                voice_signature=voice_sig,
                ffmpeg_version=ffmpeg_version,
            ) if not job.no_cache else None

            cached_hit = False
            if ck and (hit := cache_lookup(cache_dir, ck)) is not None:
                shutil.copy2(hit, seg_path)
                print(f"  [cache hit] id={seg.id} → {seg_path.name}")
                cached_hit = True
            else:
                extract_segment(seg, seg_path, bg_volume=effective_bg)
                if ck:
                    cache_store(cache_dir, ck, seg_path)

            clip_paths.append(seg_path)
            seg_clip_info.append({"clip_path": str(seg_path), "cached": cached_hit})

        base_path = work_dir / "base.mp4"
        concat_clips(clip_paths, base_path, work_dir)

        total_duration = segments[-1].out_end
        burn_subtitles(
            base_path, safe_subs, style_resolved, job.fontsdir, out_path,
            global_voice=global_voice,
            total_duration=total_duration,
        )

        # QC voice flag must reflect EITHER per-segment OR global voice usage.
        voice_used = any_voice or (global_voice is not None)

        qc_path = edit_dir / f"qc_report_{safe_ascii_name(job.name)}.json"
        qc_report = build_qc_report(
            job_name=job.name,
            segments=segments,
            seg_clip_info=seg_clip_info,
            output_path=out_path,
            expected_duration=total_duration,
            style_name=job.style,
            style_resolved=style_resolved,
            bg_volume=job.bg_volume,
            has_any_voice=voice_used,
            elapsed_s=time.time() - t0,
            edit_dir=edit_dir,
            work_dir=work_dir,
            cache_dir=cache_dir,
            out_qc_path=qc_path,
        )
        print(f"\n  done in {qc_report['elapsed_s']}s, drift={qc_report['duration']['drift_ms']}ms")
        return qc_report

    finally:
        if job.keep_intermediates:
            print(f"  intermediates kept at: {work_dir}")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)


# ============================================================================
# Batch manifest
# ============================================================================


def load_manifest(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SystemExit("batch manifest JSON must be an array of job dicts")
        return data
    if suffix == ".csv":
        rows: list[dict] = []
        with path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                rows.append({k: v for k, v in row.items() if v != ""})
        return rows
    raise SystemExit(f"unsupported manifest format: {suffix}")


def job_from_dict(d: dict, defaults: argparse.Namespace, manifest_dir: Path,
                  idx: int) -> Job:
    def _path(key: str) -> Path | None:
        v = d.get(key)
        if v in (None, ""):
            return None
        p = Path(v)
        return p if p.is_absolute() else (manifest_dir / p).resolve()

    def _float(key: str, fb: float) -> float:
        v = d.get(key)
        return float(v) if v not in (None, "") else fb

    def _str(key: str, fb: str) -> str:
        v = d.get(key)
        return str(v) if v not in (None, "") else fb

    def _bool(key: str, fb: bool) -> bool:
        v = d.get(key)
        if isinstance(v, bool):
            return v
        if v in (None, ""):
            return fb
        return str(v).lower() in ("1", "true", "yes", "on")

    srt_path = _path("srt")
    plan_path = _path("plan")
    if srt_path is None:
        raise SystemExit(f"manifest row {idx}: missing srt")
    if plan_path is None:
        raise SystemExit(f"manifest row {idx}: missing plan")

    job_name = _str("name", plan_path.stem)
    explicit_output = _path("output")
    if explicit_output is None:
        # Auto-isolate outputs by index so two jobs with the same name never
        # silently overwrite each other.
        explicit_output = (
            manifest_dir / f"final_srt_driven_{safe_ascii_name(job_name)}_{idx:02d}.mp4"
        )

    return Job(
        source=_path("source"),
        srt=srt_path,
        plan=plan_path,
        voice=_path("voice"),
        bg_volume=_float("bg_volume", defaults.bg_volume),
        tolerance=_float("tolerance", defaults.tolerance),
        trim_direction=_str("trim_direction", defaults.trim_direction),
        on_short=_str("on_short", defaults.on_short),
        style=_str("style", defaults.style),
        fontsdir=_path("fontsdir"),
        output=explicit_output,
        name=job_name,
        no_cache=_bool("no_cache", defaults.no_cache),
        keep_intermediates=_bool("keep_intermediates", defaults.keep_intermediates),
        no_overwrite=_bool("no_overwrite", defaults.no_overwrite),
    )


# ============================================================================
# CLI
# ============================================================================


def main() -> None:
    ap = argparse.ArgumentParser(description="SRT-driven edit assembly")
    ap.add_argument("--source", type=Path, default=None,
                    help="Form A: single source.mp4. Ignored if plan declares sources.")
    ap.add_argument("--srt", type=Path, default=None, help="script.srt")
    ap.add_argument("--plan", type=Path, default=None, help="edit_plan.json (Form A or B)")
    ap.add_argument("--voice", type=Path, default=None,
                    help="Global voice.wav spanning the whole timeline. "
                         "Mutually exclusive with per-segment voices in the plan.")
    ap.add_argument("--bg-volume", type=float, default=0.0,
                    help="original audio level (0.0=mute, 0.1=10%%). Default 0.0.")
    ap.add_argument("--tolerance", type=float, default=0.5,
                    help="seconds. |source_dur - srt_dur| > tolerance triggers trim/error.")
    ap.add_argument("--trim-direction", choices=["tail", "head", "center"], default="tail")
    ap.add_argument("--on-short", choices=["error", "pad"], default="error")
    ap.add_argument("--style", default="auto",
                    help=f"subtitle style. Templates: {sorted(STYLE_TEMPLATES)}. "
                         "'auto' picks cjk-natural if SRT has CJK, else bold-uppercase. "
                         "Pass a raw ASS string containing '=' to override.")
    ap.add_argument("--fontsdir", type=Path, default=None,
                    help="extra fonts directory passed to libass.")
    ap.add_argument("-o", "--output", type=Path, default=None)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-overwrite", action="store_true",
                    help="refuse to run if output file already exists.")
    ap.add_argument("--keep-intermediates", action="store_true",
                    help="keep the temp work dir (clips, base, concat list) after rendering.")
    ap.add_argument("--batch", type=Path, default=None,
                    help="run a batch manifest (jobs.json or jobs.csv) instead.")
    ap.add_argument("--continue-on-error", action="store_true",
                    help="when --batch: skip failing jobs instead of aborting.")
    args = ap.parse_args()

    versions = preflight()
    print(f"== preflight: ffmpeg {versions['ffmpeg']} / ffprobe {versions['ffprobe']} ==")

    if args.batch is not None:
        manifest_path = args.batch.resolve()
        rows = load_manifest(manifest_path)
        results: list[dict] = []
        for i, row in enumerate(rows):
            try:
                job = job_from_dict(row, args, manifest_path.parent, i)
            except SystemExit as e:
                if args.continue_on_error:
                    print(f"[batch {i}] skipped: {e}")
                    results.append({"job": row.get("name", f"row{i}"), "ok": False, "error": str(e)})
                    continue
                raise
            try:
                results.append(run_job(job, versions["ffmpeg"]))
            except SystemExit as e:
                if args.continue_on_error:
                    print(f"[batch {i}] FAILED: {e}")
                    results.append({"job": job.name, "ok": False, "error": str(e)})
                    continue
                raise
        summary_path = manifest_path.with_name(manifest_path.stem + "_qc_summary.json")
        summary_path.write_text(
            json.dumps({"jobs": results, "total": len(results),
                        "ok": sum(1 for r in results if r.get("ok"))}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nbatch QC summary → {summary_path}")
        ok = sum(1 for r in results if r.get("ok"))
        print(f"  {ok}/{len(results)} jobs ok")
        return

    if args.srt is None or args.plan is None:
        ap.error("--srt and --plan required (or use --batch)")

    job = Job(
        source=args.source.resolve() if args.source else None,
        srt=args.srt.resolve(),
        plan=args.plan.resolve(),
        voice=args.voice.resolve() if args.voice else None,
        bg_volume=args.bg_volume,
        tolerance=args.tolerance,
        trim_direction=args.trim_direction,
        on_short=args.on_short,
        style=args.style,
        fontsdir=args.fontsdir.resolve() if args.fontsdir else None,
        output=args.output.resolve() if args.output else None,
        name=args.plan.stem,
        no_cache=args.no_cache,
        keep_intermediates=args.keep_intermediates,
        no_overwrite=args.no_overwrite,
    )
    run_job(job, versions["ffmpeg"])


if __name__ == "__main__":
    main()
