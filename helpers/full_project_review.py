"""Build an offline, cue-to-frame review packet for a rendered EDL project."""

from __future__ import annotations

import html
import json
import math
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote


_TIMESTAMP = re.compile(
    r"(?P<start>(?:(?:\d{1,2}):)?\d{2}:\d{2}[,.]\d{3})"
    r"\s*-->\s*"
    r"(?P<end>(?:(?:\d{1,2}):)?\d{2}:\d{2}[,.]\d{3})"
)


def _time_seconds(value: str) -> float:
    value = value.replace(",", ".")
    parts = value.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"invalid subtitle timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def read_subtitles(path: Path) -> list[dict]:
    """Read SRT or WebVTT cues without modifying source timing."""
    raw = path.read_text(encoding="utf-8-sig")
    cues: list[dict] = []
    for block in re.split(r"\r?\n\s*\r?\n", raw.strip()):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            match = _TIMESTAMP.search(line)
            if not match:
                continue
            start = _time_seconds(match.group("start"))
            end = _time_seconds(match.group("end"))
            text = "\n".join(lines[index + 1 :]).strip()
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            if end > start and text:
                cues.append({"start": start, "end": end, "text": text})
            break
    return sorted(cues, key=lambda cue: (cue["start"], cue["end"]))


def probe_duration(video: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    duration = float(result.stdout.strip())
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("ffprobe returned an invalid video duration")
    return duration


def schedule_scenes(edl: dict) -> tuple[list[dict], list[str]]:
    ranges = edl.get("ranges")
    if not isinstance(ranges, list) or not ranges:
        raise ValueError("EDL must contain a non-empty 'ranges' list")
    sources = edl.get("sources") or {}
    scenes: list[dict] = []
    notes: list[str] = []
    cursor = 0.0
    for index, item in enumerate(ranges, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"EDL range {index} must be an object")
        try:
            source_start = float(item["start"])
            source_end = float(item["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"EDL range {index} needs numeric start/end values") from exc
        duration = source_end - source_start
        if not math.isfinite(duration) or duration <= 0 or source_start < 0:
            raise ValueError(f"EDL range {index} has an invalid source interval")
        source = str(item.get("source", ""))
        if source and sources and source not in sources:
            notes.append(f"Scene {index} refers to source '{source}', which is not listed in EDL sources.")
        scenes.append(
            {
                "index": index,
                "source": source,
                "source_start": source_start,
                "source_end": source_end,
                "output_start": cursor,
                "output_end": cursor + duration,
                "duration": duration,
                "beat": str(item.get("beat", "")).strip(),
                "quote": str(item.get("quote", "")).strip(),
                "reason": str(item.get("reason", "")).strip(),
            }
        )
        cursor += duration

    declared = edl.get("total_duration_s")
    if declared is not None:
        try:
            declared_duration = float(declared)
        except (TypeError, ValueError):
            notes.append("EDL total_duration_s is not numeric; the displayed total uses the sum of ranges.")
        else:
            if not math.isfinite(declared_duration) or declared_duration < 0:
                notes.append("EDL total_duration_s is invalid; the displayed total uses the sum of ranges.")
            elif abs(declared_duration - cursor) > 0.25:
                notes.append(
                    f"EDL total_duration_s ({declared_duration:.2f}s) differs from summed range duration ({cursor:.2f}s)."
                )
    return scenes, notes


def _sample_times(scene: dict, count: int, video_duration: float) -> list[float]:
    start = scene["output_start"]
    end = min(scene["output_end"], video_duration)
    if end <= start:
        return []
    if count == 1:
        return [(start + end) / 2]
    return [start + (end - start) * ((i + 0.5) / count) for i in range(count)]


def _extract_frame(video: Path, timestamp: float, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            "-vf",
            "scale=640:-2",
            str(target),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _cue_display(cue: dict) -> str:
    return f"{cue['start']:.3f}–{cue['end']:.3f}  {cue['text']}"


def _build_findings(scenes: list[dict], cues: list[dict], video_duration: float, notes: list[str]) -> list[dict]:
    findings = [{"kind": "note", "text": note} for note in notes]
    for scene in scenes:
        if scene["output_start"] >= video_duration + 0.05:
            findings.append(
                {
                    "kind": "timing",
                    "scene": scene["index"],
                    "text": f"Scene {scene['index']} starts beyond the {video_duration:.2f}s video.",
                }
            )
        elif scene["output_end"] > video_duration + 0.05:
            findings.append(
                {
                    "kind": "timing",
                    "scene": scene["index"],
                    "text": f"Scene {scene['index']} extends beyond the {video_duration:.2f}s video.",
                }
            )
    for index, cue in enumerate(cues, start=1):
        if cue["start"] < -0.05:
            findings.append(
                {
                    "kind": "timing",
                    "text": f"Subtitle cue {index} starts at {cue['start']:.2f}s, before the video begins.",
                }
            )
        if cue["end"] > video_duration + 0.05:
            findings.append(
                {
                    "kind": "timing",
                    "text": f"Subtitle cue {index} ends at {cue['end']:.2f}s, beyond the {video_duration:.2f}s video.",
                }
            )
    for scene in scenes:
        matches = [
            cue
            for cue in cues
            if cue["end"] > scene["output_start"] and cue["start"] < scene["output_end"]
        ]
        scene["captions"] = matches
        scene["caption_count"] = len(matches)
        if not matches:
            findings.append(
                {
                    "kind": "review",
                    "scene": scene["index"],
                    "text": f"Scene {scene['index']} has no subtitle cue in its output-time range.",
                }
            )
    for index, cue in enumerate(cues, start=1):
        crossing = [
            scene["index"]
            for scene in scenes[:-1]
            if cue["start"] < scene["output_end"] < cue["end"]
        ]
        if crossing:
            cuts = ", ".join(str(scene_index) for scene_index in crossing)
            findings.append(
                {
                    "kind": "review",
                    "text": f"Subtitle cue {index} crosses the end of scene {cuts}; inspect the cut with the displayed frames.",
                }
            )
    return findings


def _format_clock(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole:02d}.{millis:03d}"


def _html_report(video: Path, video_href: str, manifest: dict) -> str:
    findings = manifest["findings"]
    finding_html = "".join(
        f'<li class="finding finding-{html.escape(item["kind"])}">{html.escape(item["text"])}</li>'
        for item in findings
    ) or '<li class="finding finding-clear">No objective timing notes were found. Review the frames against the narration.</li>'

    scene_html: list[str] = []
    for scene in manifest["scenes"]:
        label = scene["beat"] or f"Scene {scene['index']:02d}"
        frame_html = "".join(
            "<figure>"
            f'<img src="{html.escape(quote(frame["path"]))}" alt="Output frame at {_format_clock(frame["time"])}" loading="lazy">'
            f'<figcaption>{_format_clock(frame["time"])}</figcaption>'
            "</figure>"
            for frame in scene["frames"]
        ) or '<p class="empty">No frames fell inside this scene.</p>'
        captions = "".join(
            f'<li><time>{_format_clock(cue["start"])}–{_format_clock(cue["end"])}</time><span>{html.escape(cue["text"])}</span></li>'
            for cue in scene["captions"]
        ) or '<li class="empty">No subtitle cues overlap this scene.</li>'
        planned = (
            f'<p class="planned"><span>EDL quote</span>{html.escape(scene["quote"])}</p>'
            if scene["quote"]
            else ""
        )
        source_label = scene["source"] or "source"
        source_span = (
            f'{html.escape(source_label)} {scene["source_start"]:.2f}–{scene["source_end"]:.2f}s'
        )
        scene_html.append(
            f'<article class="scene" id="scene-{scene["index"]}">'
            '<div class="scene-heading">'
            f'<span class="scene-index">{scene["index"]:02d}</span>'
            f'<div><h2>{html.escape(label)}</h2><p class="source">{source_span}</p></div>'
            f'<time class="output-range">{_format_clock(scene["output_start"])}–{_format_clock(scene["output_end"])}</time>'
            "</div>"
            f"{planned}"
            f'<div class="frames">{frame_html}</div>'
            f'<details class="voice" open><summary>Subtitle cues <span>{scene["caption_count"]}</span></summary><ol>{captions}</ol></details>'
            "</article>"
        )

    video_name = html.escape(video.name)
    transcript_name = html.escape(manifest["transcript_file"] or "Not provided")
    scene_count = len(manifest["scenes"])
    cue_count = manifest["subtitle_cue_count"]
    duration = manifest["video_duration_s"]
    expected = manifest["edl_duration_s"]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Full project review · {video_name}</title>
  <style>
    :root {{ color-scheme: dark; --desk:#17191c; --surface:#202329; --line:#3a3e45; --ink:#f2f0e8; --muted:#a4a7ad; --cut:#ff9656; --voice:#9dbbff; --note:#e8c77c; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--desk); color:var(--ink); font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ max-width:1320px; margin:0 auto; padding:34px 28px 72px; }}
    header {{ display:grid; grid-template-columns:minmax(0,1fr) auto; gap:24px; align-items:end; border-bottom:1px solid var(--line); padding:0 0 22px; }}
    h1 {{ margin:0; font-size:clamp(26px,4vw,42px); line-height:1.08; letter-spacing:-.035em; font-weight:650; }}
    .subhead {{ color:var(--muted); max-width:700px; margin:12px 0 0; }}
    a {{ color:var(--voice); }}
    .source-link {{ border:1px solid var(--line); border-radius:3px; padding:9px 12px; text-decoration:none; white-space:nowrap; }}
    .facts {{ display:flex; flex-wrap:wrap; gap:24px; padding:18px 0; color:var(--muted); border-bottom:1px solid var(--line); }}
    .facts strong {{ display:block; color:var(--ink); font-size:18px; font-weight:600; font-variant-numeric:tabular-nums; }}
    .facts span {{ font-size:12px; }}
    .findings {{ margin:20px 0 30px; padding:0; list-style:none; }}
    .finding {{ border-left:3px solid var(--note); background:var(--surface); padding:10px 14px; margin:6px 0; color:#e8e4d7; }}
    .finding-review {{ border-color:var(--cut); }} .finding-timing {{ border-color:#e87575; }} .finding-clear {{ border-color:var(--voice); }}
    .scene {{ border-top:1px solid var(--line); padding:24px 0 28px; }}
    .scene-heading {{ display:grid; grid-template-columns:42px minmax(0,1fr) auto; align-items:center; gap:14px; }}
    .scene-index {{ color:var(--cut); font:600 13px/1 ui-monospace,SFMono-Regular,Menlo,monospace; font-variant-numeric:tabular-nums; }}
    h2 {{ margin:0; font-size:19px; font-weight:600; letter-spacing:-.01em; }}
    .source {{ margin:4px 0 0; color:var(--muted); font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace; }}
    .output-range {{ color:var(--cut); font:13px ui-monospace,SFMono-Regular,Menlo,monospace; font-variant-numeric:tabular-nums; white-space:nowrap; }}
    .planned {{ margin:18px 0 12px 56px; color:var(--muted); }}
    .planned span {{ display:block; color:var(--cut); font-size:12px; margin-bottom:3px; }}
    .frames {{ margin:14px 0 14px 56px; display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:8px; }}
    figure {{ margin:0; min-width:0; background:#111316; border-bottom:2px solid var(--cut); }}
    figure img {{ display:block; width:100%; aspect-ratio:16/9; object-fit:cover; background:#101114; }}
    figcaption {{ padding:6px 9px; color:var(--voice); font:12px ui-monospace,SFMono-Regular,Menlo,monospace; font-variant-numeric:tabular-nums; }}
    .voice {{ margin-left:56px; border-top:1px solid var(--line); padding-top:10px; }}
    summary {{ cursor:pointer; color:var(--voice); font-weight:550; }}
    summary span {{ color:var(--muted); font:12px ui-monospace,SFMono-Regular,Menlo,monospace; margin-left:5px; }}
    .voice ol {{ margin:10px 0 0; padding:0; list-style:none; display:grid; gap:5px; }}
    .voice li {{ display:grid; grid-template-columns:158px minmax(0,1fr); gap:12px; padding:6px 8px; border-left:1px solid #6d87bd; background:rgba(157,187,255,.055); }}
    .voice li time {{ color:#b6c9f5; font:12px ui-monospace,SFMono-Regular,Menlo,monospace; font-variant-numeric:tabular-nums; }}
    .empty {{ color:var(--muted); font-style:italic; }}
    .footer {{ border-top:1px solid var(--line); padding-top:20px; color:var(--muted); font-size:13px; }}
    @media (max-width:700px) {{ main {{ padding:24px 16px 48px; }} header {{ grid-template-columns:1fr; align-items:start; }} .source-link {{ justify-self:start; }} .facts {{ gap:14px 20px; }} .scene-heading {{ grid-template-columns:30px minmax(0,1fr); }} .output-range {{ grid-column:2; }} .planned,.frames,.voice {{ margin-left:0; }} .frames {{ grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); }} .voice li {{ grid-template-columns:1fr; gap:3px; }} }}
    @media (prefers-reduced-motion:reduce) {{ *,*::before,*::after {{ scroll-behavior:auto !important; }} }}
  </style>
</head>
<body><main>
  <header><div><h1>Full project review</h1><p class="subhead">Check whether the frames follow the narration at each point in the edit. Timing notes are measurable; topic matching remains a reviewer decision.</p></div><a class="source-link" href="{html.escape(video_href, quote=True)}">Open {video_name}</a></header>
  <section class="facts" aria-label="Project details">
    <div><strong>{scene_count}</strong><span>EDL scenes</span></div>
    <div><strong>{_format_clock(duration)}</strong><span>video duration</span></div>
    <div><strong>{_format_clock(expected)}</strong><span>EDL range sum</span></div>
    <div><strong>{cue_count}</strong><span>subtitle cues · {transcript_name}</span></div>
  </section>
  <ul class="findings" aria-label="Timing review notes">{finding_html}</ul>
  <section aria-label="Scenes in output order">{''.join(scene_html)}</section>
  <footer class="footer">This packet records timings and sampled frames. A clean timing report does not certify semantic or editorial alignment.</footer>
</main></body></html>"""


def _fresh_report_dir(video: Path) -> Path:
    root = video.parent / "verify"
    stem = f"{video.stem}_full_project_review"
    candidate = root / stem
    suffix = 2
    while candidate.exists():
        candidate = root / f"{stem}_{suffix}"
        suffix += 1
    return candidate


def _resolve_edl_subtitles(value: object, edl_path: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("EDL 'subtitles' must be a non-empty path")
    subtitle_path = Path(value.strip())
    candidates = (
        [subtitle_path]
        if subtitle_path.is_absolute()
        else [edl_path.parent / subtitle_path, Path.cwd() / subtitle_path]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    tried = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"subtitle file in EDL not found (tried {tried})")


def create_full_project_review(
    edl_path: Path,
    video_path: Path,
    report_dir: Path | None = None,
    transcript_path: Path | None = None,
    frames_per_scene: int = 3,
) -> Path:
    """Write an HTML + JSON review packet and sampled output frames."""
    edl_path = edl_path.resolve()
    video_path = video_path.resolve()
    if not edl_path.is_file():
        raise FileNotFoundError(f"EDL not found: {edl_path}")
    if not video_path.is_file():
        raise FileNotFoundError(f"video not found: {video_path}")
    if frames_per_scene < 1 or frames_per_scene > 9:
        raise ValueError("frames per scene must be between 1 and 9")

    try:
        edl = json.loads(edl_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read EDL JSON: {exc}") from exc
    if not isinstance(edl, dict):
        raise ValueError("EDL must be a JSON object")
    scenes, notes = schedule_scenes(edl)
    video_duration = probe_duration(video_path)
    edl_duration = sum(scene["duration"] for scene in scenes)
    if abs(edl_duration - video_duration) > 0.25:
        notes.append(
            f"Summed EDL range duration ({edl_duration:.2f}s) differs from video duration ({video_duration:.2f}s)."
        )

    if transcript_path is None:
        if edl.get("subtitles"):
            transcript_path = _resolve_edl_subtitles(edl["subtitles"], edl_path)
        else:
            possible = edl_path.parent / "master.srt"
            transcript_path = possible if possible.is_file() else None
    elif not transcript_path.is_file():
        raise FileNotFoundError(f"subtitle file not found: {transcript_path}")
    if transcript_path is not None:
        if transcript_path.suffix.lower() not in {".srt", ".vtt"}:
            raise ValueError("full-project review expects an output-time .srt or .vtt transcript")
        cues = read_subtitles(transcript_path)
    else:
        cues = []
        notes.append("No output-time .srt/.vtt found; the report uses EDL scene notes without subtitle cues.")

    report_dir = report_dir.resolve() if report_dir else _fresh_report_dir(video_path)
    if report_dir.exists():
        raise FileExistsError(f"review output already exists: {report_dir}; choose a new --report-dir")
    report_dir.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True)

    findings = _build_findings(scenes, cues, video_duration, notes)
    for scene in scenes:
        scene["frames"] = []
        sample_times = _sample_times(scene, frames_per_scene, video_duration)
        for frame_index, timestamp in enumerate(sample_times, start=1):
            relative = Path("frames") / f"scene-{scene['index']:03d}" / f"frame-{frame_index:02d}.jpg"
            _extract_frame(video_path, timestamp, report_dir / relative)
            scene["frames"].append({"time": timestamp, "path": relative.as_posix()})

    manifest = {
        "format": "video-use-full-project-review-v1",
        "review_kind": "human_review_packet",
        "video_file": video_path.name,
        "edl_file": edl_path.name,
        "transcript_file": transcript_path.name if transcript_path else None,
        "video_duration_s": video_duration,
        "edl_duration_s": edl_duration,
        "subtitle_cue_count": len(cues),
        "findings": findings,
        "scenes": scenes,
    }
    manifest_path = report_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        video_href = quote(os.path.relpath(video_path, report_dir).replace(os.sep, "/"))
    except ValueError:
        # Windows cannot form a relative path between different drive letters.
        video_href = video_path.as_uri()
    report_html = _html_report(video_path, video_href, manifest)
    (report_dir / "index.html").write_text(report_html, encoding="utf-8")
    return report_dir
