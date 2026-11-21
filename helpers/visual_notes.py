"""Log what the picture shows in raw footage, with Gemini watching the video.

The transcript tells the editor what was said; this tells it what was on
screen — shot changes, framing, focus/exposure problems, the speaker looking
away, slides and B-roll-worthy moments — so takes can be chosen without
pulling a timeline_view at every candidate.

Each source is shrunk to a 360p / 1 fps proxy (Gemini samples video at 1 fps
anyway), split into windows, uploaded through the Gemini Files API and logged
as plain-prose notes with approximate time ranges. Per-source results are
cached in <edit_dir>/visual/<stem>.json; every run then rebuilds
<edit_dir>/visual_notes.md from all cached sources.

Timestamps are only good to about a second. They are for FINDING moments —
cut edges still snap to word boundaries from the Scribe transcript.

Usage:
    python helpers/visual_notes.py <video_or_dir> [<video_or_dir> ...]
    python helpers/visual_notes.py <videos_dir> --context "two-person podcast, launch promo"
    python helpers/visual_notes.py <video> --model gemini-pro-latest --window 600
    python helpers/visual_notes.py <video> --force          # ignore the cache for these sources
    python helpers/visual_notes.py --edit-dir <edit_dir>    # just rebuild visual_notes.md
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from pack_transcripts import format_duration, format_time
from transcribe import load_api_key
from transcribe_batch import VIDEO_EXTS, find_videos


API_BASE = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-flash-latest"
DEFAULT_WINDOW = 900.0   # seconds of footage per request — keeps notes detailed and output under the token cap
PROXY_HEIGHT = 360

PROMPT = """You are logging raw footage for a video editor. The editor already has a \
word-level transcript of the speech, so do NOT transcribe dialogue. Log what the \
PICTURE shows, so the editor can choose takes and cuts without watching everything.

This clip is {clip_desc}. All timestamps you write are relative to the start of \
THIS clip, as MM:SS (or H:MM:SS).

Note, with time ranges:
- shot changes, framing (wide / medium / close-up), camera moves, zooms, angle changes
- technical problems: soft focus or focus hunting, exposure or white-balance jumps, \
shake, dropped frames, lens obstructed, mic or crew visible
- performer problems: looking off camera, eyes closed, reading notes, fidgeting, \
breaking or laughing, someone walking into frame
- what is on screen: slides, screen recordings, products, text, locations, objects — \
anything usable as B-roll or an insert
- slates, claps, setup and dead time before or after a take
- strong visual moments: reactions, gestures, expressive beats worth keeping

Write each note as one or two plain, concrete sentences ("speaker glances down at \
notes", not "minor issue"). Cover the whole clip; a stretch where nothing changes is \
one note saying so. Do not grade or score anything. If you are unsure, say so rather \
than guess.{context}"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {
            "type": "STRING",
            "description": "One paragraph: setting, people, shot types, overall picture quality.",
        },
        "moments": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "start": {"type": "STRING", "description": "MM:SS from the start of this clip"},
                    "end": {"type": "STRING", "description": "MM:SS from the start of this clip"},
                    "note": {"type": "STRING"},
                },
                "required": ["start", "end", "note"],
                "propertyOrdering": ["start", "end", "note"],
            },
        },
    },
    "required": ["summary", "moments"],
    "propertyOrdering": ["summary", "moments"],
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def parse_timestamp(value) -> float:
    """'MM:SS', 'H:MM:SS', 'MM:SS.s', '75' or a number → seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().rstrip("s")
    if not re.fullmatch(r"\d+(:\d+){0,2}(\.\d+)?", text):
        raise ValueError(f"not a timestamp: {value!r}")
    seconds = 0.0
    for part in text.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def plan_windows(duration: float, window: float) -> list[tuple[float, float]]:
    """Split [0, duration] into windows of at most `window` seconds.

    A tail shorter than a quarter window is folded into the previous one, so a
    15m10s take is one 15m10s request instead of 15m plus a 10s scrap.
    """
    if duration <= 0:
        return []
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        end = min(start + window, duration)
        if duration - end < window / 4:
            end = duration
        windows.append((start, end))
        start = end
    return windows


def proxy_cmd(video: Path, dest: Path, start: float, end: float, height: int = PROXY_HEIGHT) -> list[str]:
    """ffmpeg command for a small 1 fps proxy of [start, end] with mono speech-band audio.

    The audio stays in: hearing the take helps Gemini tie what it sees to what is
    happening. `0:a:0?` keeps sources with no audio working.
    """
    return [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(video),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", f"fps=1,scale=-2:{height}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "1", "-ar", "16000", "-b:a", "32k",
        "-movflags", "+faststart",
        str(dest),
    ]


def clip_description(index: int, total: int, start: float, end: float) -> str:
    if total == 1:
        return "a whole take"
    return (f"part {index + 1} of {total} of a longer take "
            f"(covering {format_clock(start)}–{format_clock(end)} of the original)")


def format_clock(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def response_text(payload: dict) -> str:
    """The answer text of a generateContent response, minus thought parts."""
    candidates = payload.get("candidates") or []
    if not candidates:
        reason = (payload.get("promptFeedback") or {}).get("blockReason", "no candidates")
        raise RuntimeError(f"Gemini returned no answer ({reason})")
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    finish = cand.get("finishReason", "STOP")
    if finish not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
        raise RuntimeError(f"Gemini stopped early ({finish}); try a smaller --window")
    if not text.strip():
        raise RuntimeError("Gemini returned an empty answer")
    return text


def offset_moments(raw: list[dict], start: float, end: float) -> list[dict]:
    """Clip-relative moments → source-timeline moments, clamped to the window."""
    length = end - start
    out: list[dict] = []
    for m in raw:
        note = (m.get("note") or "").strip()
        if not note:
            continue
        try:
            s = parse_timestamp(m.get("start"))
            e = parse_timestamp(m.get("end", m.get("start")))
        except ValueError:
            continue
        s = min(max(s, 0.0), length)
        e = min(max(e, s), length)
        out.append({"start": round(start + s, 2), "end": round(start + e, 2), "note": note})
    return out


def render_markdown(entries: list[dict]) -> str:
    lines = [
        "# Visual notes",
        "",
        "What the picture shows, logged by Gemini from 360p / 1 fps proxies.",
        "Times are approximate (±1s): use them to find moments, then cut on word",
        "boundaries from `takes_packed.md` and check edges with `timeline_view`.",
        "",
    ]
    for entry in entries:
        moments = entry.get("moments", [])
        lines.append(f"## {entry['source']}  (duration: {format_duration(entry.get('duration', 0.0))}, "
                     f"{len(moments)} notes)")
        for summary in entry.get("summaries", []):
            lines.append(f"  {summary}")
        for m in moments:
            lines.append(f"  [{format_time(m['start'])}-{format_time(m['end'])}] {m['note']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Gemini API (REST, no SDK)
# ---------------------------------------------------------------------------

def _request(method: str, url: str, api_key: str, retries: int = 3, **kw) -> requests.Response:
    """HTTP call with backoff on rate limits and transient server errors."""
    headers = {"x-goog-api-key": api_key, **kw.pop("headers", {})}
    kw.setdefault("timeout", 600)
    for attempt in range(retries + 1):
        resp = requests.request(method, url, headers=headers, **kw)
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(5 * 2 ** attempt)
            continue
        if resp.status_code >= 400:
            raise RuntimeError(f"Gemini {method} {url.split('?')[0]} returned "
                               f"{resp.status_code}: {resp.text[:500]}")
        return resp
    raise AssertionError("unreachable")


def upload_file(path: Path, api_key: str, mime_type: str = "video/mp4") -> dict:
    """Resumable upload to the Files API; returns the file resource once it is ACTIVE."""
    size = path.stat().st_size
    start = _request(
        "POST", f"{API_BASE}/upload/v1beta/files", api_key,
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime_type,
            "Content-Type": "application/json",
        },
        json={"file": {"display_name": path.name}},
    )
    upload_url = start.headers.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError("Gemini upload did not return an upload URL")

    # Bytes, not a file handle: a retry must resend the whole proxy (a few MB).
    done = _request(
        "POST", upload_url, api_key,
        headers={
            "Content-Length": str(size),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
        data=path.read_bytes(),
    )
    file = done.json()["file"]

    # Video needs server-side processing before it can be referenced.
    deadline = time.time() + 600
    while file.get("state") == "PROCESSING":
        if time.time() > deadline:
            raise RuntimeError(f"{file['name']} still processing after 10 min")
        time.sleep(3)
        file = _request("GET", f"{API_BASE}/v1beta/{file['name']}", api_key).json()
    if file.get("state") != "ACTIVE":
        raise RuntimeError(f"upload of {path.name} ended in state {file.get('state')}: "
                           f"{file.get('error')}")
    return file


def delete_file(name: str, api_key: str) -> None:
    """Best effort: uploads expire after 48h on their own."""
    try:
        _request("DELETE", f"{API_BASE}/v1beta/{name}", api_key, retries=0)
    except Exception:
        pass


def generate_notes(file: dict, prompt: str, api_key: str, model: str, hi_res: bool = False) -> dict:
    body = {
        "contents": [{
            "parts": [
                {"file_data": {"mime_type": file.get("mimeType", "video/mp4"), "file_uri": file["uri"]}},
                {"text": prompt},
            ],
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            # LOW is ~66 tokens a frame instead of ~258: plenty to read framing and
            # problems, and it lets an hour of footage fit comfortably.
            "mediaResolution": "MEDIA_RESOLUTION_MEDIUM" if hi_res else "MEDIA_RESOLUTION_LOW",
        },
    }
    resp = _request("POST", f"{API_BASE}/v1beta/models/{model}:generateContent", api_key, json=body)
    return resp.json()


# ---------------------------------------------------------------------------
# Per-source driver
# ---------------------------------------------------------------------------

def probe_duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise RuntimeError(f"could not read the duration of {video.name}")


def has_video_stream(video: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=index",
         "-of", "csv=p=0", str(video)],
        capture_output=True, text=True,
    )
    return bool(out.stdout.strip())


def notes_path(edit_dir: Path, video: Path) -> Path:
    return edit_dir / "visual" / f"{video.stem}.json"


def notes_one(
    video: Path,
    edit_dir: Path,
    api_key: str,
    model: str = DEFAULT_MODEL,
    window: float = DEFAULT_WINDOW,
    context: str | None = None,
    hi_res: bool = False,
    force: bool = False,
) -> Path:
    """Log one source. Cached: returns the existing JSON unless force is set."""
    out_path = notes_path(edit_dir, video)
    if out_path.exists() and not force:
        print(f"cached: {out_path.name}")
        return out_path
    if not has_video_stream(video):
        raise RuntimeError(f"{video.name} has no video stream")

    duration = probe_duration(video)
    windows = plan_windows(duration, window)
    extra = f"\n\nContext from the editor: {context}" if context else ""

    t0 = time.time()
    moments: list[dict] = []
    summaries: list[str] = []
    usage: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (ws, we) in enumerate(windows):
            proxy = Path(tmp) / f"{video.stem}_{i:02d}.mp4"
            proc = subprocess.run(proxy_cmd(video, proxy, ws, we), capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg proxy failed for {video.name}: {proc.stderr[-500:]}")
            mb = proxy.stat().st_size / (1024 * 1024)
            print(f"  {video.stem} [{format_clock(ws)}–{format_clock(we)}] uploading proxy "
                  f"({mb:.1f} MB)", flush=True)

            file = upload_file(proxy, api_key)
            try:
                prompt = PROMPT.format(clip_desc=clip_description(i, len(windows), ws, we),
                                       context=extra)
                payload = generate_notes(file, prompt, api_key, model, hi_res)
            finally:
                delete_file(file["name"], api_key)

            data = json.loads(response_text(payload))
            moments.extend(offset_moments(data.get("moments", []), ws, we))
            if data.get("summary"):
                summaries.append(data["summary"].strip())
            usage.append(payload.get("usageMetadata", {}))

    moments.sort(key=lambda m: (m["start"], m["end"]))
    result = {
        "source": video.stem,
        "path": str(video),
        "duration": round(duration, 2),
        "model": model,
        "windows": [[ws, we] for ws, we in windows],
        "summaries": summaries,
        "moments": moments,
        "usage": usage,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    tokens = sum(u.get("totalTokenCount", 0) for u in usage)
    print(f"  saved: {out_path.name} — {len(moments)} notes, {tokens:,} tokens, "
          f"{time.time() - t0:.1f}s")
    return out_path


def collect_sources(paths: list[Path]) -> list[Path]:
    videos: list[Path] = []
    for p in paths:
        p = p.resolve()
        if p.is_dir():
            videos.extend(find_videos(p))
        elif p.is_file() and p.suffix in VIDEO_EXTS:
            videos.append(p)
        else:
            sys.exit(f"not a video or directory: {p}")
    return list(dict.fromkeys(videos))


def write_markdown(edit_dir: Path) -> Path | None:
    files = sorted((edit_dir / "visual").glob("*.json"))
    if not files:
        return None
    entries = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    out = edit_dir / "visual_notes.md"
    out.write_text(render_markdown(entries), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Log what raw footage shows, with Gemini")
    ap.add_argument("paths", type=Path, nargs="*", help="Videos and/or directories of videos")
    ap.add_argument("--edit-dir", type=Path, default=None,
                    help="Edit output directory (default: <first source's folder>/edit)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Gemini model (default: {DEFAULT_MODEL})")
    ap.add_argument("--window", type=float, default=DEFAULT_WINDOW,
                    help="Seconds of footage per request (default: 900)")
    ap.add_argument("--context", default=None,
                    help="One or two sentences about the project, passed to Gemini")
    ap.add_argument("--hi-res", action="store_true",
                    help="Medium media resolution: ~4x the tokens, for reading small on-screen text")
    ap.add_argument("--force", action="store_true", help="Re-log sources that are already cached")
    ap.add_argument("--workers", type=int, default=2, help="Sources processed in parallel (default: 2)")
    args = ap.parse_args()

    if not args.paths and not args.edit_dir:
        ap.error("give at least one video or directory, or --edit-dir to rebuild the markdown")

    videos = collect_sources(args.paths)
    if args.edit_dir:
        edit_dir = args.edit_dir.resolve()
    elif args.paths[0].resolve().is_dir():
        edit_dir = args.paths[0].resolve() / "edit"
    else:
        edit_dir = videos[0].parent / "edit"

    failed: list[str] = []
    if videos:
        api_key = load_api_key("GEMINI_API_KEY")
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {
                pool.submit(notes_one, v, edit_dir, api_key, args.model, args.window,
                            args.context, args.hi_res, args.force): v
                for v in videos
            }
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as exc:
                    failed.append(futures[fut].name)
                    print(f"  FAILED {futures[fut].name}: {exc}", file=sys.stderr)

    out = write_markdown(edit_dir)
    if out:
        print(f"wrote {out}")
    if failed:
        sys.exit(f"{len(failed)} source(s) failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
