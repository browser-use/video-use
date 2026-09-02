"""Validation and normalization for the public video-use EDL contract.

This module deliberately has no renderer dependencies. Local tools and
third-party integrations can all reject an incomplete edit
before spending time uploading media or starting an ffmpeg render.
"""

from __future__ import annotations

import copy
import json
import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Any


# error type raised when an edl cannot be rendered deterministically
class EDLValidationError(ValueError):
    """Raised when an EDL cannot produce a deterministic video output."""


_DELIVERABLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_RESOLUTION = re.compile(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*")
_CAPTION_PROVENANCE_KINDS = {
    "source_transcript",
    "narration_alignment",
    "provided_transcript",
}


# resolve a path relative to the edit directory unless it is already absolute
def _resolve_path(value: str, edit_dir: Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (edit_dir / path).resolve()


# coerce a value to float and raise a validation error with the field label on failure
def _number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise EDLValidationError(f"{label} must be numeric") from exc
    return result


# check whether transcript or alignment json contains at least one word with valid timing
def _has_timed_speech(payload: Any) -> bool:
    """Return whether JSON evidence contains at least one timestamped word."""

    if not isinstance(payload, dict):
        return False
    raw_words = payload.get("words")
    if isinstance(raw_words, list):
        for item in raw_words:
            if not isinstance(item, dict) or item.get("type") not in (None, "word"):
                continue
            text = str(item.get("text") or item.get("word") or "").strip()
            try:
                start = float(item["start"])
                end = float(item["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if text and end > start >= 0:
                return True

    # fall back to elevenlabs character alignment when no words array has timing
    for key in ("normalized_alignment", "alignment"):
        alignment = payload.get(key)
        if not isinstance(alignment, dict):
            continue
        characters = alignment.get("characters")
        starts = alignment.get("character_start_times_seconds")
        ends = alignment.get("character_end_times_seconds")
        if not all(isinstance(value, list) for value in (characters, starts, ends)):
            continue
        if not (len(characters) == len(starts) == len(ends)):
            continue
        for character, start_value, end_value in zip(characters, starts, ends):
            if not str(character).strip():
                continue
            try:
                start = float(start_value)
                end = float(end_value)
            except (TypeError, ValueError):
                continue
            if end > start >= 0:
                return True
    return False


# collect problems with the captions block such as missing provenance or evidence without spoken words
def _caption_contract_problems(
    edl: dict[str, Any],
    edit_dir: Path,
    *,
    check_files: bool,
) -> list[str]:
    """Validate that captions are backed by timestamped audible speech."""

    subtitles = edl.get("subtitles")
    captions = edl.get("captions")
    if not subtitles and captions is None:
        return []

    problems: list[str] = []
    if subtitles is not None and (not isinstance(subtitles, str) or not subtitles.strip()):
        problems.append("subtitles must be a non-empty file path")
        subtitles = None
    if not isinstance(captions, dict):
        problems.append(
            "subtitles require a captions object with captions.provenance backed by "
            "timestamped audible speech"
        )
        return problems

    provenance = captions.get("provenance")
    if not isinstance(provenance, dict):
        problems.append(
            "captions.provenance is required; captions may only represent audible speech"
        )
        return problems

    kind = str(provenance.get("kind") or "")
    if kind not in _CAPTION_PROVENANCE_KINDS:
        choices = ", ".join(sorted(_CAPTION_PROVENANCE_KINDS))
        problems.append(f"captions.provenance.kind must be one of: {choices}")

    raw_files = provenance.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        problems.append(
            "captions.provenance.files must list timestamped transcript or alignment JSON"
        )
        return problems

    subtitle_path = _resolve_path(subtitles, edit_dir) if isinstance(subtitles, str) else None
    for index, value in enumerate(raw_files):
        label = f"captions.provenance.files[{index}]"
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{label} must be a non-empty JSON file path")
            continue
        evidence_path = _resolve_path(value, edit_dir)
        if evidence_path.suffix.casefold() != ".json":
            problems.append(f"{label} must reference timestamped JSON evidence")
            continue
        # the rendered subtitle file cannot vouch for itself
        if subtitle_path is not None and evidence_path == subtitle_path:
            problems.append(f"{label} cannot reuse the rendered subtitle file as evidence")
            continue
        if not check_files:
            continue
        if not evidence_path.is_file():
            problems.append(f"caption evidence file does not exist: {evidence_path}")
            continue
        try:
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"caption evidence could not be read: {evidence_path}: {exc}")
            continue
        if not _has_timed_speech(payload):
            problems.append(
                f"caption evidence contains no timestamped spoken words: {evidence_path}"
            )

    if check_files and subtitle_path is not None and not subtitle_path.is_file():
        problems.append(f"subtitles file does not exist: {subtitle_path}")
    return problems


# read width and height from explicit fields or a resolution string and enforce even sizes
def _dimensions(item: dict[str, Any], label: str) -> tuple[int, int]:
    width = item.get("width")
    height = item.get("height")
    # accept a resolution string such as 1080x1920 when explicit fields are missing
    if width is None or height is None:
        match = _RESOLUTION.fullmatch(str(item.get("resolution") or ""))
        if match:
            width, height = match.groups()
    try:
        width_i, height_i = int(width), int(height)
    except (TypeError, ValueError) as exc:
        raise EDLValidationError(
            f"{label} requires width and height or a resolution such as 1080x1920"
        ) from exc
    if width_i < 2 or height_i < 2 or width_i > 8192 or height_i > 8192:
        raise EDLValidationError(f"{label} dimensions must be between 2 and 8192 pixels")
    if width_i % 2 or height_i % 2:
        raise EDLValidationError(f"{label} width and height must be even for yuv420p output")
    return width_i, height_i


# parse an fps value into a canonical ffmpeg rational string
def _frame_rate(value: Any, label: str) -> str:
    text = str(value)
    try:
        rate = Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise EDLValidationError(f"{label} fps must be a positive number or rational") from exc
    if rate <= 0 or rate.numerator > 2_147_483_647 or rate.denominator > 2_147_483_647:
        raise EDLValidationError(f"{label} fps must be a positive ffmpeg-compatible rate")
    return f"{rate.numerator}/{rate.denominator}"


# read loudness targets from several accepted aliases and validate their ranges
def _loudness(item: dict[str, Any], label: str) -> dict[str, float]:
    raw = item.get("loudness")
    audio = item.get("audio") if isinstance(item.get("audio"), dict) else {}
    if raw is None:
        raw = audio
    if isinstance(raw, (int, float)):
        raw = {"integrated_lufs": raw}
    if not isinstance(raw, dict):
        raise EDLValidationError(f"{label} loudness must be an object or LUFS number")
    integrated = _number(
        raw.get(
            "integrated_lufs",
            raw.get(
                "integrated_loudness_lufs",
                item.get("loudness_lufs", item.get("target_lufs", -14.0)),
            ),
        ),
        f"{label} integrated_lufs",
    )
    true_peak = _number(
        raw.get("true_peak_dbtp", item.get("true_peak_dbtp", -1.0)),
        f"{label} true_peak_dbtp",
    )
    lra = _number(raw.get("lra", raw.get("loudness_range_lu", 11.0)), f"{label} lra")
    if not -70.0 <= integrated <= -5.0:
        raise EDLValidationError(f"{label} integrated_lufs must be between -70 and -5")
    if not -9.0 <= true_peak <= 0.0:
        raise EDLValidationError(f"{label} true_peak_dbtp must be between -9 and 0")
    if not 1.0 <= lra <= 50.0:
        raise EDLValidationError(f"{label} lra must be between 1 and 50")
    return {
        "integrated_lufs": integrated,
        "true_peak_dbtp": true_peak,
        "lra": lra,
    }


# resolve the reframe settings for one deliverable from its own block or the shared edl block
def _reframe_for(edl: dict[str, Any], item: dict[str, Any], deliverable_id: str) -> dict[str, Any]:
    raw = item.get("reframe")
    if raw is None:
        # a shared reframe block without a mode is keyed by deliverable id
        shared = edl.get("reframe")
        if isinstance(shared, dict) and "mode" not in shared:
            raw = shared.get(deliverable_id)
        else:
            raw = shared
    # a reframe_track alias on the deliverable selects a named track
    if raw is not None and item.get("reframe_track"):
        raw = copy.deepcopy(raw)
        if isinstance(raw, dict):
            raw["track_id"] = item["reframe_track"]
    if raw is None:
        raw = {"mode": "cover"}
    if isinstance(raw, str):
        raw = {"mode": raw}
    if not isinstance(raw, dict):
        raise EDLValidationError(f"deliverable '{deliverable_id}' reframe must be an object")
    result = copy.deepcopy(raw)
    # map legacy mode and asset aliases onto the canonical names
    mode = str(result.get("mode") or result.get("strategy") or "cover").casefold()
    if mode == "center":
        mode = "cover"
    if mode in {"tracked", "tracked_subject", "subject_tracking", "smart_crop"}:
        mode = "track"
    if result.get("tracking_asset") and not (result.get("track") or result.get("track_file")):
        result["track_file"] = result["tracking_asset"]
    if mode not in {"cover", "contain", "track"}:
        raise EDLValidationError(
            f"deliverable '{deliverable_id}' reframe mode must be cover, contain, or track"
        )
    result["mode"] = mode
    interpolation = str(result.get("interpolation") or "linear").casefold()
    if interpolation not in {"linear", "smooth", "hold"}:
        raise EDLValidationError(
            f"deliverable '{deliverable_id}' reframe interpolation must be "
            "linear, smooth, or hold"
        )
    result["interpolation"] = interpolation
    if mode == "track" and not (
        result.get("keyframes") or result.get("track") or result.get("track_file")
    ):
        raise EDLValidationError(
            f"deliverable '{deliverable_id}' requests tracked reframing but has no "
            "keyframes or track file"
        )
    return result


# turn the deliverables list or object into validated entries with canonical fields and output paths
def normalize_deliverables(
    edl: dict[str, Any],
    edit_dir: Path,
    *,
    output_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Return validated deliverables in one canonical list representation.

    Both a list and an id-keyed object are accepted so older agent-authored
    EDLs remain easy to migrate. The returned dictionaries contain canonical
    dimensions, fps, loudness, reframe settings, and an absolute ``output_path``.
    """
    raw = edl.get("deliverables")
    if not raw:
        return []
    if isinstance(raw, dict):
        entries: list[dict[str, Any]] = []
        for deliverable_id, value in raw.items():
            if not isinstance(value, dict):
                raise EDLValidationError(
                    f"deliverable '{deliverable_id}' must be an object"
                )
            entries.append({"id": str(deliverable_id), **value})
    elif isinstance(raw, list):
        entries = raw
    else:
        raise EDLValidationError("deliverables must be a list or id-keyed object")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(entries):
        if not isinstance(value, dict):
            raise EDLValidationError(f"deliverable {index} must be an object")
        item = copy.deepcopy(value)
        deliverable_id = str(item.get("id") or item.get("name") or "")
        label = f"deliverable '{deliverable_id or index}'"
        if not _DELIVERABLE_ID.fullmatch(deliverable_id):
            raise EDLValidationError(
                f"{label} id must contain only letters, numbers, underscores, or hyphens"
            )
        if deliverable_id in seen:
            raise EDLValidationError(f"duplicate deliverable id '{deliverable_id}'")
        seen.add(deliverable_id)
        width, height = _dimensions(item, label)
        fps = _frame_rate(item.get("fps", "30"), label)
        video_codec = str(item.get("video_codec") or "h264").casefold()
        if video_codec not in {"h264", "libx264", "avc"}:
            raise EDLValidationError(f"{label} currently supports only H.264 video")
        pixel_format = str(item.get("pixel_format") or "yuv420p").casefold()
        if pixel_format != "yuv420p":
            raise EDLValidationError(f"{label} currently supports only yuv420p output")
        audio = item.get("audio") if isinstance(item.get("audio"), dict) else {}
        audio_codec = str(audio.get("codec") or "aac").casefold()
        if audio_codec != "aac":
            raise EDLValidationError(f"{label} currently supports only AAC audio")
        try:
            sample_rate = int(audio.get("sample_rate_hz", 48_000))
        except (TypeError, ValueError) as exc:
            raise EDLValidationError(f"{label} audio sample_rate_hz must be an integer") from exc
        if sample_rate != 48_000:
            raise EDLValidationError(f"{label} currently supports only 48000 Hz audio")
        declared_file = str(item.get("file") or f"deliverables/{deliverable_id}.mp4")
        # an output directory override replaces the declared file name
        output_path = (
            (output_dir / f"{deliverable_id}.mp4").resolve()
            if output_dir is not None
            else _resolve_path(declared_file, edit_dir)
        )
        if output_path.suffix.casefold() != ".mp4":
            raise EDLValidationError(f"{label} file must use the .mp4 extension")
        item.update(
            {
                "id": deliverable_id,
                "width": width,
                "height": height,
                "fps": fps,
                "video_codec": "h264",
                "pixel_format": "yuv420p",
                "file": declared_file,
                "output_path": output_path,
                "loudness": _loudness(item, label),
                "reframe": _reframe_for(edl, item, deliverable_id),
            }
        )
        normalized.append(item)
    # two deliverables writing the same file would silently overwrite each other
    seen: dict[Path, str] = {}
    for item in normalized:
        previous = seen.setdefault(item["output_path"], item["id"])
        if previous != item["id"]:
            raise EDLValidationError(
                f"deliverables '{previous}' and '{item['id']}' resolve to the same output file"
            )
    return normalized


# load just enough of a track reference to tell whether the named track has keyframes
def _track_keyframes(reframe: dict[str, Any], edit_dir: Path) -> list[Any] | None:
    """Read enough of a track reference to reject missing or empty named tracks."""
    raw = reframe.get("keyframes")
    if raw is None and isinstance(reframe.get("track"), list):
        raw = reframe["track"]
    if raw is not None:
        return raw if isinstance(raw, list) else None
    track_value = reframe.get("track_file") or reframe.get("track")
    if not isinstance(track_value, str):
        return None
    track_path = _resolve_path(track_value, edit_dir)
    payload = json.loads(track_path.read_text(encoding="utf-8"))
    # named track files need a track_id to pick one track
    if isinstance(payload, dict) and isinstance(payload.get("tracks"), dict):
        track_id = reframe.get("track_id")
        return payload["tracks"].get(str(track_id)) if track_id else None
    if isinstance(payload, dict):
        raw = payload.get("keyframes")
    else:
        raw = payload
    return raw if isinstance(raw, list) else None


# run every structural check on an edl and raise one error listing all problems
def validate_edl(
    edl: dict[str, Any],
    edit_dir: Path,
    *,
    check_files: bool = True,
    require_caption_provenance: bool | None = None,
) -> None:
    """Reject blocked, incomplete, or internally inconsistent edit decisions.

    EDL version 2 and newer require timestamped speech evidence for captions.
    Callers accepting freshly agent-authored handoffs may opt into the same
    strict check for legacy-version EDLs with ``require_caption_provenance``.
    """
    problems: list[str] = []
    # a non object root cannot be inspected so it is reported instead of crashing below
    if not isinstance(edl, dict):
        raise EDLValidationError("EDL is not renderable:\n- edl must be a JSON object")
    raw_version = edl.get("version", 1)
    # a fractional version is malformed rather than a legacy version so it must not truncate
    if isinstance(raw_version, bool) or not (
        isinstance(raw_version, int) or (isinstance(raw_version, float) and raw_version.is_integer())
    ):
        version = 0
    else:
        version = int(raw_version)
    if version < 1:
        problems.append("version must be a positive integer")
    # version two and newer enforce caption provenance unless the caller overrides
    strict_captions = (
        version >= 2
        if require_caption_provenance is None
        else require_caption_provenance
    )
    if strict_captions:
        problems.extend(
            _caption_contract_problems(edl, edit_dir, check_files=check_files)
        )
    status = str(edl.get("status") or "").casefold()
    if status.startswith("blocked") or status in {"not_ready", "incomplete"}:
        problems.append(f"EDL status is '{edl.get('status')}'")
    handoff = edl.get("handoff")
    if isinstance(handoff, dict) and handoff.get("render_ready") is False:
        reason = (
            handoff.get("reason")
            or handoff.get("blocked_reason")
            or handoff.get("blocking_reason")
        )
        problems.append(
            "handoff marks render_ready as false" + (f": {reason}" if reason else "")
        )

    sources = edl.get("sources")
    ranges = edl.get("ranges")
    if not isinstance(sources, dict) or not sources:
        problems.append("sources must contain at least one source video")
        sources = {}
    if not isinstance(ranges, list) or not ranges:
        problems.append("ranges must contain at least one playable edit range")
        ranges = []

    for source_id, value in sources.items():
        if not isinstance(value, str) or not value.strip():
            problems.append(f"source '{source_id}' has no file path")
            continue
        if check_files:
            path = _resolve_path(value, edit_dir)
            if not path.is_file():
                problems.append(f"source '{source_id}' file does not exist: {path}")

    for index, value in enumerate(ranges):
        if not isinstance(value, dict):
            problems.append(f"range {index} must be an object")
            continue
        source_id = value.get("source")
        # only a string can name a source so anything else is reported instead of crashing the lookup
        if not isinstance(source_id, str) or source_id not in sources:
            problems.append(f"range {index} references unknown source '{source_id}'")
        try:
            start = float(value["start"])
            end = float(value["end"])
            # nan and inf compare as ordered so they need an explicit finite check
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            problems.append(f"range {index} requires finite start >= 0 and end > start")

    try:
        deliverables = normalize_deliverables(edl, edit_dir)
        for deliverable in deliverables:
            reframe = deliverable["reframe"]
            track_value = reframe.get("track_file") or reframe.get("track")
            # tracked deliverables must point at readable non empty keyframes
            if check_files and reframe["mode"] == "track":
                if isinstance(track_value, str) and not _resolve_path(
                    track_value, edit_dir
                ).is_file():
                    problems.append(
                        f"deliverable '{deliverable['id']}' track file does not exist: "
                        f"{_resolve_path(track_value, edit_dir)}"
                    )
                    continue
                try:
                    keyframes = _track_keyframes(reframe, edit_dir)
                except (OSError, json.JSONDecodeError) as exc:
                    problems.append(
                        f"deliverable '{deliverable['id']}' track data could not be read: {exc}"
                    )
                    continue
                if not keyframes:
                    track_id = reframe.get("track_id")
                    suffix = f" '{track_id}'" if track_id else ""
                    problems.append(
                        f"deliverable '{deliverable['id']}' tracking keyframes{suffix} are empty or missing"
                    )
    except EDLValidationError as exc:
        problems.append(str(exc))

    if problems:
        formatted = "\n".join(f"- {problem}" for problem in problems)
        raise EDLValidationError(f"EDL is not renderable:\n{formatted}")
