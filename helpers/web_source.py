"""Find and inspect public web-video candidates with yt-dlp.

Search returns a compact candidate manifest, inspect saves captions/metadata
and renders a filmstrip for one promising source interval, select records the
human-style visual review, and acquire downloads an approved source at high
quality. Final selections retain exact source intervals so duplicate downloads
and accidental repeated footage can be detected before rendering.

Usage:
    python helpers/web_source.py search "GPU data center" --edit-dir /path/edit
    python helpers/web_source.py inspect URL --edit-dir /path/edit
    python helpers/web_source.py inspect URL --edit-dir /path/edit --start 42 --end 50
    python helpers/web_source.py select URL --edit-dir /path/edit --start 42 --end 50 \
      --beat-id beat_03 --decision keep --purpose physical-demo \
      --shot-type demonstration --visible-subject "the accelerator board" \
      --visible-action "an engineer points to its memory modules" \
      --why-footage "the physical hardware cannot be shown honestly as a diagram"
    python helpers/web_source.py acquire URL --edit-dir /path/edit
"""

from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import math
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


MEDIA_SUFFIXES = {
    ".3gp",
    ".avi",
    ".flv",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ts",
    ".webm",
}

SELECTION_PURPOSES = {
    "person-identity",
    "physical-object",
    "animal",
    "place",
    "physical-demo",
    "source-evidence",
    "screen-evidence",
}

SHOT_TYPES = {
    "talking-head",
    "demonstration",
    "object-detail",
    "location",
    "screen-evidence",
    "other",
}


# current utc time as an iso string for manifest timestamps
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# reduce a string to a short lowercase filename safe token
def slug(value: str, *, fallback: str = "source", limit: int = 64) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return (cleaned or fallback)[:limit].rstrip("-")


# trim a string to a limit with an ellipsis and return none for non strings
def short_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


# locate the yt-dlp executable or fail with an install hint
def require_yt_dlp() -> str:
    executable = shutil.which("yt-dlp")
    if executable is None:
        raise RuntimeError("yt-dlp is required; install it before searching web sources")
    return executable


# accept only http or https urls that point at public hosts
def validate_public_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("source URL must be a public http or https URL")
    hostname = parsed.hostname or ""
    if hostname.casefold() == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("local URLs are not valid public sources")
    # when the host is a literal ip reject anything that is not globally routable
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("private, loopback, and link-local URLs are not valid public sources")
        return value
    # a public looking name can still resolve to an internal address so every resolved address is checked
    for resolved in resolved_addresses(hostname):
        if not resolved.is_global:
            raise ValueError(f"{hostname} resolves to a non public address and is not a valid source")
    return value


# resolve a hostname to ip addresses and return nothing when resolution is unavailable
def resolved_addresses(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    return addresses


# turn optional start and end values into a validated float pair or none
def validate_range(start: float | None, end: float | None) -> tuple[float, float] | None:
    if start is None and end is None:
        return None
    if start is None or end is None:
        raise ValueError("--start and --end must be provided together")
    # nan and inf compare as ordered so they need an explicit finite check
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise ValueError("source range must be finite and satisfy 0 <= start < end")
    return float(start), float(end)


# run a subprocess and raise with the tail of its stderr when it fails
def run_command(command: list[str], *, capture_output: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise RuntimeError(f"command failed ({result.returncode}): {detail[-1500:]}")
    return result


# run yt-dlp with the given arguments and parse its single json document
def yt_dlp_json(arguments: list[str]) -> dict[str, Any]:
    executable = require_yt_dlp()
    result = run_command(
        [
            executable,
            "--ignore-config",
            "--no-warnings",
            *arguments,
        ]
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp did not return valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("yt-dlp returned an unexpected JSON value")
    return payload


# write json atomically by going through a temp file and renaming it
def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


# load a json object from disk or return the default when the file is missing
def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


# derive a usable page url from a flat playlist entry with youtube fallbacks
def search_result_url(entry: dict[str, Any]) -> str | None:
    webpage_url = entry.get("webpage_url")
    if isinstance(webpage_url, str) and webpage_url.startswith(("http://", "https://")):
        return webpage_url
    video_id = entry.get("id")
    extractor = str(entry.get("extractor_key") or entry.get("extractor") or "").casefold()
    if video_id and "youtube" in extractor:
        return f"https://www.youtube.com/watch?v={video_id}"
    raw_url = entry.get("url")
    if isinstance(raw_url, str) and raw_url.startswith(("http://", "https://")):
        return raw_url
    return None


# reduce a raw yt-dlp search entry to the fields a reviewer needs
def compact_search_entry(entry: dict[str, Any], rank: int) -> dict[str, Any]:
    thumbnails = entry.get("thumbnails")
    thumbnail = entry.get("thumbnail")
    # fall back to the last thumbnail in the list which is usually the largest
    if not thumbnail and isinstance(thumbnails, list) and thumbnails:
        last_thumbnail = thumbnails[-1]
        if isinstance(last_thumbnail, dict):
            thumbnail = last_thumbnail.get("url")
    return {
        "rank": rank,
        "id": entry.get("id"),
        "url": search_result_url(entry),
        "title": entry.get("title"),
        "channel": entry.get("channel") or entry.get("uploader"),
        "channel_id": entry.get("channel_id") or entry.get("uploader_id"),
        "duration_s": entry.get("duration"),
        "description": short_text(entry.get("description"), 500),
        "thumbnail": thumbnail,
        "view_count": entry.get("view_count"),
        "upload_date": entry.get("upload_date"),
        "live_status": entry.get("live_status"),
        "availability": entry.get("availability"),
        "license": entry.get("license"),
        "rights_status": "needs-review",
    }


# run a youtube search through yt-dlp and save a candidate manifest in the edit directory
def search(query: str, edit_dir: Path, limit: int, target_videos: int) -> Path:
    query = query.strip()
    if not query:
        raise ValueError("search query cannot be empty")
    if not 1 <= limit <= 25:
        raise ValueError("search limit must be between 1 and 25")
    if not 1 <= target_videos <= 3:
        raise ValueError("target video count must be between 1 and 3")
    if target_videos > limit:
        raise ValueError("target video count cannot exceed the search limit")

    # flat playlist mode lists results without resolving each video
    payload = yt_dlp_json(
        [
            "--flat-playlist",
            "--skip-download",
            "--dump-single-json",
            f"ytsearch{limit}:{query}",
        ]
    )
    raw_entries = payload.get("entries") or []
    candidates = [
        compact_search_entry(entry, rank)
        for rank, entry in enumerate(raw_entries, start=1)
        if isinstance(entry, dict)
    ]
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]
    output_path = edit_dir / "downloads" / f"candidates_{slug(query)}_{query_hash}.json"
    write_json(
        output_path,
        {
            "schema_version": 1,
            "provider": "yt-dlp-youtube-search",
            "query": query,
            "generated_at": utc_now(),
            "target_video_count": target_videos,
            "candidate_count": len(candidates),
            "candidates": candidates,
        },
    )
    print(f"saved {len(candidates)} candidates: {output_path}")
    return output_path


# reduce full yt-dlp video info to a compact metadata record
def compact_metadata(info: dict[str, Any], requested_url: str) -> dict[str, Any]:
    subtitles = info.get("subtitles")
    automatic_captions = info.get("automatic_captions")
    automatic_languages = (
        sorted(automatic_captions) if isinstance(automatic_captions, dict) else []
    )
    # the orig suffix marks the spoken language so strip it to list real source languages
    automatic_source_languages = [
        language.removesuffix("-orig")
        for language in automatic_languages
        if language.endswith("-orig")
    ]
    return {
        "schema_version": 1,
        "inspected_at": utc_now(),
        "requested_url": requested_url,
        "id": info.get("id"),
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "webpage_url": info.get("webpage_url") or requested_url,
        "title": info.get("title"),
        "description": short_text(info.get("description"), 8000),
        "channel": info.get("channel") or info.get("uploader"),
        "channel_id": info.get("channel_id") or info.get("uploader_id"),
        "duration_s": info.get("duration"),
        "width": info.get("width"),
        "height": info.get("height"),
        "fps": info.get("fps"),
        "upload_date": info.get("upload_date"),
        "timestamp": info.get("timestamp"),
        "thumbnail": info.get("thumbnail"),
        "tags": (info.get("tags") or [])[:100],
        "categories": info.get("categories") or [],
        "chapters": info.get("chapters") or [],
        "subtitle_languages": sorted(subtitles) if isinstance(subtitles, dict) else [],
        "automatic_caption_source_languages": automatic_source_languages,
        "automatic_caption_language_count": len(automatic_languages),
        "license": info.get("license"),
        "availability": info.get("availability"),
        "age_limit": info.get("age_limit"),
        "live_status": info.get("live_status"),
        "rights_status": "needs-review",
    }


# create and return the per source folder under downloads keyed by extractor and id
def source_directory(edit_dir: Path, info: dict[str, Any]) -> Path:
    extractor = str(info.get("extractor_key") or info.get("extractor") or "web")
    source_id = str(info.get("id") or hashlib.sha256(str(info).encode("utf-8")).hexdigest()[:12])
    folder = edit_dir / "downloads" / slug(f"{extractor}-{source_id}")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# stable identity for a source built from extractor and id or a url hash
def source_key(metadata: dict[str, Any]) -> str:
    """Return a stable source identity without relying on a mutable title."""
    extractor = slug(str(metadata.get("extractor") or "web"))
    source_id = str(metadata.get("id") or "").strip()
    if source_id:
        return f"{extractor}:{source_id}"
    url = str(metadata.get("webpage_url") or metadata.get("requested_url") or "")
    return f"{extractor}:{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}"


# short hash naming one exact source interval so repeats can be detected
def selection_key(source_id: str, source_range: tuple[float, float]) -> str:
    start, end = source_range
    raw = f"{source_id}|{start:.3f}|{end:.3f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# load or initialise the shared selections manifest
def selections_record(edit_dir: Path) -> tuple[Path, dict[str, Any]]:
    path = edit_dir / "downloads" / "web_selections.json"
    payload = read_json(
        path,
        {"schema_version": 1, "updated_at": utc_now(), "selections": []},
    )
    payload.setdefault("selections", [])
    return path, payload


# fraction of the shorter interval that is covered by the other one
def interval_overlap_ratio(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    """Return overlap divided by the shorter interval's duration."""
    overlap = max(0.0, min(first[1], second[1]) - max(first[0], second[0]))
    shorter = min(first[1] - first[0], second[1] - second[0])
    return overlap / shorter if shorter > 0 else 0.0


# lowercase and strip punctuation so descriptions compare loosely
def normalized_description(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


# fetch video info with yt-dlp and write compact metadata into the source folder
def inspect_metadata(url: str, edit_dir: Path) -> tuple[dict[str, Any], Path]:
    info = yt_dlp_json(
        [
            "--no-playlist",
            "--skip-download",
            "--dump-single-json",
            url,
        ]
    )
    folder = source_directory(edit_dir, info)
    write_json(folder / "metadata.json", compact_metadata(info, url))
    return info, folder


# download thumbnail and subtitle sidecars without the video itself
def download_sidecars(url: str, folder: Path, subtitle_languages: str, force: bool) -> None:
    executable = require_yt_dlp()
    overwrite_option = "--force-overwrites" if force else "--no-overwrites"
    run_command(
        [
            executable,
            "--ignore-config",
            "--no-warnings",
            "--no-playlist",
            "--skip-download",
            "--write-thumbnail",
            "--convert-thumbnails",
            "jpg",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            subtitle_languages,
            "--sub-format",
            "vtt",
            overwrite_option,
            "--output",
            str(folder / "asset.%(ext)s"),
            url,
        ]
    )


# choose the best vtt file preferring original english then any english then anything
def choose_caption_file(folder: Path) -> Path | None:
    caption_files = sorted(folder.glob("*.vtt"))
    if not caption_files:
        return None
    preferred_names = ("asset.en-orig.vtt", "asset.en.vtt")
    for name in preferred_names:
        preferred = folder / name
        if preferred in caption_files:
            return preferred
    english = [path for path in caption_files if ".en" in path.name.casefold()]
    return english[0] if english else caption_files[0]


# turn a vtt timestamp into seconds so cues can be compared numerically
def parse_vtt_time(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"invalid VTT time: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


# strip tags and entities and collapse whitespace in caption text
def clean_caption_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value).replace("\u200b", " ")
    return re.sub(r"\s+", " ", value).strip()


# parse a vtt file into start end text cues
def parse_vtt(path: Path) -> list[tuple[float, float, str]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    cues: list[tuple[float, float, str]] = []
    cursor = 0
    timing_pattern = re.compile(
        r"^(?P<start>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+"
        r"(?P<end>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})"
    )
    # scan for timing lines and gather the text lines that follow until a blank line
    while cursor < len(lines):
        match = timing_pattern.match(lines[cursor].strip())
        if match is None:
            cursor += 1
            continue
        start = parse_vtt_time(match.group("start"))
        end = parse_vtt_time(match.group("end"))
        cursor += 1
        text_lines: list[str] = []
        while cursor < len(lines) and lines[cursor].strip():
            text_lines.append(lines[cursor])
            cursor += 1
        text = clean_caption_text(" ".join(text_lines))
        if text:
            cues.append((start, end, text))
    return cues


# normalise a word for overlap comparison
def word_key(value: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", value.casefold())


# drop the leading words of a cue that repeat the tail of the previous cues
def new_caption_words(history: list[str], current: list[str]) -> list[str]:
    if not history:
        return current
    comparable_history = [word_key(word) for word in history]
    comparable_current = [word_key(word) for word in current]
    maximum = min(len(comparable_history), len(comparable_current), 30)
    # try the longest possible overlap first so rolling captions collapse cleanly
    for overlap in range(maximum, 0, -1):
        if comparable_history[-overlap:] == comparable_current[:overlap]:
            return current[overlap:]
    return current


# merge deduplicated cues into readable phrases and write a markdown transcript
def pack_transcript(caption_file: Path, output: Path) -> Path:
    packed: list[tuple[float, float, str]] = []
    history: list[str] = []
    for start, end, text in parse_vtt(caption_file):
        words = text.split()
        additions = new_caption_words(history, words)
        if not additions:
            continue
        history.extend(additions)
        history = history[-60:]
        addition_text = " ".join(additions)
        # extend the previous phrase when the gap is short and it is still open ended
        if (
            packed
            and start - packed[-1][1] <= 2.5
            and len(packed[-1][2].split()) < 14
            and not packed[-1][2].endswith((".", "?", "!"))
        ):
            previous_start, _, previous_text = packed[-1]
            packed[-1] = (previous_start, end, f"{previous_text} {addition_text}")
        else:
            packed.append((start, end, addition_text))

    lines = [
        "# Packed source transcript",
        "",
        f"Source captions: `{caption_file.name}`",
        f"Phrases: {len(packed)}",
        "",
    ]
    lines.extend(f"[{start:07.2f}-{end:07.2f}] {text}" for start, end, text in packed)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


# record transcript availability and file names in the source metadata
def update_transcript_status(
    folder: Path,
    status: str,
    caption_file: Path | None = None,
    packed_file: Path | None = None,
) -> None:
    metadata_path = folder / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["transcript_status"] = status
    metadata["caption_file"] = caption_file.name if caption_file else None
    metadata["packed_transcript_file"] = packed_file.name if packed_file else None
    write_json(metadata_path, metadata)


# load or initialise the per source inspection manifest
def inspection_record(folder: Path) -> tuple[Path, dict[str, Any]]:
    path = folder / "inspection.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = {
            "schema_version": 1,
            "max_source_windows": 3,
            "windows": [],
        }
    return path, payload


# return whether a range is new and refuse when three windows already exist
def assert_window_available(folder: Path, source_range: tuple[float, float]) -> bool:
    _, payload = inspection_record(folder)
    tag = range_tag(*source_range)
    existing_tags = {window.get("tag") for window in payload.get("windows", [])}
    if tag in existing_tags:
        return False
    if len(existing_tags) >= 3:
        raise ValueError(
            "candidate already has three inspected source windows; skip it or choose from those"
        )
    return True


# append an inspected window to the manifest unless its tag is already there
def record_window(
    folder: Path,
    source_range: tuple[float, float],
    proxy: Path,
    filmstrip: Path,
) -> None:
    path, payload = inspection_record(folder)
    tag = range_tag(*source_range)
    if any(window.get("tag") == tag for window in payload.get("windows", [])):
        return
    payload.setdefault("windows", []).append(
        {
            "tag": tag,
            "source_start": source_range[0],
            "source_end": source_range[1],
            "proxy": proxy.name,
            "filmstrip": filmstrip.name,
            "inspected_at": utc_now(),
        }
    )
    write_json(path, payload)


# record a keep or reject decision for an inspected window after checking reuse and repetition contracts
def select_window(
    url: str,
    edit_dir: Path,
    source_range: tuple[float, float],
    *,
    beat_id: str,
    decision: str,
    purpose: str,
    shot_type: str,
    visible_subject: str,
    visible_action: str,
    why_footage: str,
    rights_status: str,
    reuse_of: str | None,
) -> Path:
    """Record the editorial decision made after inspecting actual frames.

    This is intentionally judgment-driven. The helper checks provenance and
    repetition contracts; it does not pretend that a numeric relevance score
    can replace looking at the start, middle, and end of the proposed cut.
    """
    if decision not in {"keep", "reject"}:
        raise ValueError("selection decision must be 'keep' or 'reject'")
    if purpose not in SELECTION_PURPOSES:
        raise ValueError(f"unknown purpose '{purpose}'")
    if shot_type not in SHOT_TYPES:
        raise ValueError(f"unknown shot type '{shot_type}'")
    beat_id = beat_id.strip()
    if not beat_id:
        raise ValueError("beat id cannot be empty")
    required_descriptions = {
        "visible subject": visible_subject,
        "visible action": visible_action,
        "why footage": why_footage,
    }
    for label, value in required_descriptions.items():
        if not value.strip():
            raise ValueError(f"{label} cannot be empty")
    # talking heads only earn a keep when the person or their authority is the point
    if decision == "keep" and shot_type == "talking-head" and purpose not in {
        "person-identity",
        "source-evidence",
    }:
        raise ValueError(
            "generic talking-head footage is not a valid visual refresh; keep it only "
            "when the person's identity or source authority is the visual purpose"
        )

    _, folder = inspect_metadata(url, edit_dir)
    metadata = read_json(folder / "metadata.json", {})
    source_id = source_key(metadata)
    tag = range_tag(*source_range)
    _, inspection = inspection_record(folder)
    window = next(
        (item for item in inspection.get("windows", []) if item.get("tag") == tag),
        None,
    )
    if window is None:
        raise ValueError(
            "source window has not been visually inspected; run inspect with this exact "
            "--start/--end range first"
        )

    path, manifest = selections_record(edit_dir)
    selections = manifest.get("selections", [])
    asset_id = selection_key(source_id, source_range)
    existing_beat = next(
        (item for item in selections if item.get("beat_id") == beat_id), None
    )
    if existing_beat is not None:
        raise ValueError(f"beat id '{beat_id}' already exists in {path}")

    # the same exact interval may only appear again when it is declared as a reuse of the first beat
    kept = [item for item in selections if item.get("decision") == "keep"]
    exact = next((item for item in kept if item.get("asset_id") == asset_id), None)
    if exact is not None and reuse_of != exact.get("beat_id"):
        raise ValueError(
            f"exact source interval already belongs to {exact.get('beat_id')}; use "
            f"--reuse-of {exact.get('beat_id')} and reference the same prepared overlay file"
        )
    if reuse_of:
        original = next(
            (item for item in kept if item.get("beat_id") == reuse_of), None
        )
        if original is None:
            raise ValueError(f"--reuse-of does not name an existing kept beat: {reuse_of}")
        if original.get("asset_id") != asset_id:
            raise ValueError("intentional reuse must use the original beat's exact source range")

    # warn about heavy interval overlap or repeated subject and action within the same source
    overlap_warnings: list[str] = []
    semantic_warnings: list[str] = []
    this_range = source_range
    this_subject = normalized_description(visible_subject)
    this_action = normalized_description(visible_action)
    for item in kept:
        if item.get("source_key") != source_id:
            continue
        prior_range = (float(item["source_start"]), float(item["source_end"]))
        ratio = interval_overlap_ratio(this_range, prior_range)
        if ratio >= 0.5 and item.get("asset_id") != asset_id:
            overlap_warnings.append(
                f"overlaps {item.get('beat_id')} by {ratio:.0%} of the shorter source range"
            )
        if (
            this_subject == normalized_description(str(item.get("visible_subject") or ""))
            and this_action == normalized_description(str(item.get("visible_action") or ""))
        ):
            semantic_warnings.append(
                f"describes the same visible subject and action as {item.get('beat_id')}"
            )

    record = {
        "beat_id": beat_id,
        "asset_id": asset_id,
        "decision": decision,
        "source_key": source_id,
        "source_url": metadata.get("webpage_url") or url,
        "source_title": metadata.get("title"),
        "source_start": source_range[0],
        "source_end": source_range[1],
        "purpose": purpose,
        "shot_type": shot_type,
        "visible_subject": visible_subject.strip(),
        "visible_action": visible_action.strip(),
        "why_footage": why_footage.strip(),
        "rights_status": rights_status.strip() or "needs-review",
        "inspection_proxy": str(folder / str(window.get("proxy"))),
        "inspection_filmstrip": str(folder / str(window.get("filmstrip"))),
        "reuse_of": reuse_of,
        "overlap_warnings": overlap_warnings,
        "semantic_warnings": semantic_warnings,
        "selected_at": utc_now(),
    }
    selections.append(record)
    manifest["updated_at"] = utc_now()
    manifest["selections"] = selections
    write_json(path, manifest)

    print(f"recorded {decision} decision: {beat_id} -> {path}")
    for warning in [*overlap_warnings, *semantic_warnings]:
        print(f"warning: {warning}")
    if reuse_of:
        print(f"reuse: reference the prepared overlay from {reuse_of}; do not cut it again")
    selection_summary(edit_dir)
    return path


# print and return a report on source diversity and reused assets with an optional one source reason
def selection_summary(
    edit_dir: Path, one_source_reason: str | None = None
) -> dict[str, Any]:
    """Print and return source diversity and repetition signals."""
    path, manifest = selections_record(edit_dir)
    if one_source_reason is not None:
        one_source_reason = one_source_reason.strip()
        if not one_source_reason:
            raise ValueError("one-source reason cannot be empty")
        manifest["one_source_reason"] = one_source_reason
        manifest["updated_at"] = utc_now()
        write_json(path, manifest)
    kept = [item for item in manifest.get("selections", []) if item.get("decision") == "keep"]
    unique_sources = {item.get("source_key") for item in kept}
    unique_assets = {item.get("asset_id") for item in kept}
    warnings: list[str] = []
    documented_reason = str(manifest.get("one_source_reason") or "").strip()
    # four or more kept beats from one source need a written justification
    if len(kept) >= 4 and len(unique_sources) < 2 and not documented_reason:
        warnings.append(
            "four or more web beats use one source; prefer a second strong source or "
            "run summary --one-source-reason to document why one source is necessary"
        )
    reused = len(kept) - len(unique_assets)
    if reused:
        warnings.append(
            f"{reused} beat(s) intentionally reuse an existing asset; every reuse should "
            "reference the same prepared overlay file"
        )
    report = {
        "manifest": str(path),
        "kept_beats": len(kept),
        "unique_sources": len(unique_sources),
        "unique_source_intervals": len(unique_assets),
        "one_source_reason": documented_reason or None,
        "warnings": warnings,
    }
    print(json.dumps(report, indent=2))
    return report


# format a range as a filename safe tag with p in place of dots
def range_tag(start: float, end: float) -> str:
    return f"{start:.3f}-{end:.3f}".replace(".", "p")


# find the newest complete media file in a folder that matches the stem
def find_media(folder: Path, stem_prefix: str) -> Path | None:
    candidates = [
        candidate
        for candidate in folder.glob(f"{stem_prefix}.*")
        if candidate.is_file()
        and candidate.suffix.casefold() in MEDIA_SUFFIXES
        and not candidate.name.endswith(".part")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate.stat().st_mtime)


# read a media duration in seconds with ffprobe
def probe_duration(video: Path) -> float:
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ]
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"could not read proxy duration: {video}") from exc
    if duration <= 0:
        raise RuntimeError(f"proxy has invalid duration: {video}")
    return duration


# download a low resolution proxy for one source range with a full download fallback
def download_proxy(
    url: str,
    folder: Path,
    source_range: tuple[float, float],
    height: int,
    force: bool,
) -> Path:
    if height < 144 or height > 1080:
        raise ValueError("proxy height must be between 144 and 1080")
    start, end = source_range
    stem = f"preview_{range_tag(start, end)}"
    existing = find_media(folder, stem)
    if existing is not None and not force:
        return existing

    executable = require_yt_dlp()
    overwrite_option = "--force-overwrites" if force else "--no-overwrites"
    # prefer separate video and audio under the height limit and fall back to anything
    format_selector = (
        f"bestvideo*[height<={height}]+bestaudio/"
        f"best[height<={height}]/worst"
    )
    try:
        run_command(
            [
                executable,
                "--ignore-config",
                "--no-warnings",
                "--no-playlist",
                "--download-sections",
                f"*{start:.3f}-{end:.3f}",
                "--force-keyframes-at-cuts",
                "--format",
                format_selector,
                "--recode-video",
                "mp4",
                overwrite_option,
                "--output",
                str(folder / f"{stem}.%(ext)s"),
                url,
            ]
        )
    # when section download fails cache one whole low resolution copy and cut the range locally with ffmpeg
    except RuntimeError as direct_error:
        inspection_source = find_media(folder, "inspection_source")
        if inspection_source is None or force:
            print("direct range download failed; caching one low-resolution inspection source")
            run_command(
                [
                    executable,
                    "--ignore-config",
                    "--no-warnings",
                    "--no-playlist",
                    "--format",
                    format_selector,
                    overwrite_option,
                    "--output",
                    str(folder / "inspection_source.%(ext)s"),
                    url,
                ]
            )
            inspection_source = find_media(folder, "inspection_source")
        if inspection_source is None:
            raise direct_error
        run_command(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(inspection_source),
                "-t",
                f"{end - start:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "24",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(folder / f"{stem}.mp4"),
            ]
        )
    proxy = find_media(folder, stem)
    if proxy is None:
        raise RuntimeError("yt-dlp completed without producing a proxy video")
    return proxy


# render a filmstrip png of the proxy through timeline_view
def render_proxy_filmstrip(proxy: Path, source_range: tuple[float, float], frames: int) -> Path:
    if not 2 <= frames <= 30:
        raise ValueError("filmstrip frame count must be between 2 and 30")
    duration = probe_duration(proxy)
    # stop slightly before the end so the last frame is not a black tail
    visual_end = max(0.01, duration - min(0.05, duration / 10))
    output = proxy.with_name(f"inspection_{range_tag(*source_range)}.png")
    timeline_view = Path(__file__).resolve().with_name("timeline_view.py")
    run_command(
        [
            sys.executable,
            str(timeline_view),
            str(proxy),
            "0",
            f"{visual_end:.3f}",
            "--n-frames",
            str(frames),
            "--output",
            str(output),
        ]
    )
    return output


# save metadata captions and a packed transcript and optionally inspect one range visually
def inspect(
    url: str,
    edit_dir: Path,
    source_range: tuple[float, float] | None,
    subtitle_languages: str,
    proxy_height: int,
    frames: int,
    force: bool,
) -> Path:
    _, folder = inspect_metadata(url, edit_dir)
    download_sidecars(url, folder, subtitle_languages, force)
    print(f"saved candidate metadata and sidecars: {folder}")

    caption_file = choose_caption_file(folder)
    if caption_file is None:
        update_transcript_status(folder, "skip-no-transcript")
        print("SKIP: no captions were available; move to the next candidate")
        return folder

    packed_file = pack_transcript(caption_file, folder / "transcript_packed.md")
    update_transcript_status(folder, "ready", caption_file, packed_file)
    print(f"packed transcript: {packed_file}")

    if source_range is not None:
        is_new_window = assert_window_available(folder, source_range)
        proxy = download_proxy(url, folder, source_range, proxy_height, force)
        filmstrip = render_proxy_filmstrip(proxy, source_range, frames)
        if is_new_window:
            record_window(folder, source_range, proxy, filmstrip)
        print(f"saved source-window proxy: {proxy}")
        print(f"saved visual inspection: {filmstrip}")
    else:
        caption_files = sorted(folder.glob("*.vtt"))
        print("captions: " + ", ".join(str(path) for path in caption_files))
    return folder


# download an approved source at best quality and write an acquisition manifest
def acquire(
    url: str,
    edit_dir: Path,
    source_range: tuple[float, float] | None,
    force: bool,
) -> Path:
    _, folder = inspect_metadata(url, edit_dir)
    metadata = read_json(folder / "metadata.json", {})
    require_kept_selection(edit_dir, source_key(metadata), source_range)
    stem = "source" if source_range is None else f"source_{range_tag(*source_range)}"
    existing = find_media(folder, stem)
    if existing is not None and not force:
        print(f"cached source: {existing}")
        return existing

    executable = require_yt_dlp()
    overwrite_option = "--force-overwrites" if force else "--no-overwrites"
    arguments = [
        executable,
        "--ignore-config",
        "--no-warnings",
        "--no-playlist",
        "--format",
        "bestvideo*+bestaudio/best",
        overwrite_option,
    ]
    if source_range is not None:
        start, end = source_range
        arguments.extend(["--download-sections", f"*{start:.3f}-{end:.3f}"])
    arguments.extend(["--output", str(folder / f"{stem}.%(ext)s"), url])
    run_command(arguments)

    source = find_media(folder, stem)
    if source is None:
        raise RuntimeError("yt-dlp completed without producing a source video")
    write_json(
        folder / "acquisition.json",
        {
            "schema_version": 1,
            "source_key": source_key(metadata),
            "asset_id": (
                selection_key(source_key(metadata), source_range)
                if source_range is not None
                else None
            ),
            "source_url": url,
            "acquired_at": utc_now(),
            "requested_source_range": (
                {"start": source_range[0], "end": source_range[1]}
                if source_range is not None
                else None
            ),
            "file": source.name,
        },
    )
    print(f"saved approved source: {source}")
    return source


# refuse to download a source or range that was never inspected and kept through select
def require_kept_selection(
    edit_dir: Path, source_id: str, source_range: tuple[float, float] | None
) -> None:
    _, manifest = selections_record(edit_dir)
    kept = [
        item
        for item in manifest.get("selections", [])
        if item.get("decision") == "keep" and item.get("source_key") == source_id
    ]
    if source_range is not None:
        asset_id = selection_key(source_id, source_range)
        kept = [item for item in kept if item.get("asset_id") == asset_id]
    if not kept:
        raise ValueError(
            "no kept selection matches this source and range; inspect the window and "
            "record a keep decision with select before acquiring it"
        )


# build the argparse parser with search inspect select summary and acquire subcommands
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Search, visually inspect, select, and acquire public web-video "
            "candidates with yt-dlp"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Find YouTube candidates")
    search_parser.add_argument("query", help="Concrete visual search phrase")
    search_parser.add_argument("--edit-dir", type=Path, required=True)
    search_parser.add_argument("--limit", type=int, default=8)
    search_parser.add_argument(
        "--target-videos",
        type=int,
        default=2,
        help="Number of source videos intended for the output (1-3)",
    )

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Save candidate metadata/captions and optionally inspect one time range",
    )
    inspect_parser.add_argument("url")
    inspect_parser.add_argument("--edit-dir", type=Path, required=True)
    inspect_parser.add_argument("--start", type=float, default=None)
    inspect_parser.add_argument("--end", type=float, default=None)
    inspect_parser.add_argument("--subtitle-languages", default="en.*,en")
    inspect_parser.add_argument("--proxy-height", type=int, default=720)
    inspect_parser.add_argument("--frames", type=int, default=10)
    inspect_parser.add_argument("--force", action="store_true")

    select_parser = subparsers.add_parser(
        "select",
        help="Record a keep/reject decision for an already inspected source window",
    )
    select_parser.add_argument("url")
    select_parser.add_argument("--edit-dir", type=Path, required=True)
    select_parser.add_argument("--start", type=float, required=True)
    select_parser.add_argument("--end", type=float, required=True)
    select_parser.add_argument("--beat-id", required=True)
    select_parser.add_argument("--decision", choices=("keep", "reject"), required=True)
    select_parser.add_argument("--purpose", choices=sorted(SELECTION_PURPOSES), required=True)
    select_parser.add_argument("--shot-type", choices=sorted(SHOT_TYPES), required=True)
    select_parser.add_argument("--visible-subject", required=True)
    select_parser.add_argument("--visible-action", required=True)
    select_parser.add_argument("--why-footage", required=True)
    select_parser.add_argument("--rights-status", default="needs-review")
    select_parser.add_argument(
        "--reuse-of",
        default=None,
        help="Existing kept beat with the exact same source range; reuse its overlay file",
    )

    summary_parser = subparsers.add_parser(
        "summary", help="Report final source diversity and repeated-asset signals"
    )
    summary_parser.add_argument("--edit-dir", type=Path, required=True)
    summary_parser.add_argument(
        "--one-source-reason",
        default=None,
        help="Document why one source is necessary when four or more web beats are kept",
    )

    acquire_parser = subparsers.add_parser(
        "acquire",
        help="Download an approved candidate at the best available quality",
    )
    acquire_parser.add_argument("url")
    acquire_parser.add_argument("--edit-dir", type=Path, required=True)
    acquire_parser.add_argument("--start", type=float, default=None)
    acquire_parser.add_argument("--end", type=float, default=None)
    acquire_parser.add_argument("--force", action="store_true")
    return parser


# command line entry that validates inputs and dispatches to the chosen subcommand
def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        edit_dir = args.edit_dir.resolve()
        if args.command == "search":
            search(args.query, edit_dir, args.limit, args.target_videos)
            return

        if args.command == "summary":
            selection_summary(edit_dir, args.one_source_reason)
            return

        url = validate_public_url(args.url)
        source_range = validate_range(args.start, args.end)
        if args.command == "inspect":
            inspect(
                url=url,
                edit_dir=edit_dir,
                source_range=source_range,
                subtitle_languages=args.subtitle_languages,
                proxy_height=args.proxy_height,
                frames=args.frames,
                force=args.force,
            )
            return
        if args.command == "select":
            if source_range is None:
                raise ValueError("select requires --start and --end")
            select_window(
                url=url,
                edit_dir=edit_dir,
                source_range=source_range,
                beat_id=args.beat_id,
                decision=args.decision,
                purpose=args.purpose,
                shot_type=args.shot_type,
                visible_subject=args.visible_subject,
                visible_action=args.visible_action,
                why_footage=args.why_footage,
                rights_status=args.rights_status,
                reuse_of=args.reuse_of,
            )
            return
        if args.command == "acquire":
            acquire(url, edit_dir, source_range, args.force)
            return
        parser.error(f"unknown command: {args.command}")
    except (RuntimeError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
