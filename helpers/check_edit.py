"""Check a planned legacy edit before extracting clips or writing delivery files."""

import argparse
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile

try:
    from .check_env import check_media_tool, check_writable_directory
except ImportError:
    from check_env import check_media_tool, check_writable_directory


# Run bounded probes and retain the useful part of failed command output
def command(args, *, cwd=None):
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{args[0]} check exceeded 30 seconds") from exc
    except OSError as exc:
        raise ValueError(f"Cannot run {args[0]}: {exc}") from exc
    if result.returncode:
        raise ValueError(f"{args[0]} check failed: {result.stderr[-2000:].strip()}")
    return result


# Reject booleans and nonfinite or negative timeline numbers
def seconds(value, name):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return value


# Match the renderer path convention without interpreting paths as shell commands
def local_path(value, root, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} needs a file path")
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


# Validate only the edit format implemented by the installed legacy renderer
def inputs_for(edl, root, *, build_subtitles=False, no_subtitles=False):
    if not isinstance(edl, dict) or type(edl.get("version", 1)) is not int or edl.get("version", 1) != 1:
        raise ValueError("This checker supports EDL version 1 only")
    sources, ranges = edl.get("sources"), edl.get("ranges")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("sources must be a nonempty object")
    if not isinstance(ranges, list) or not ranges:
        raise ValueError("ranges must be a nonempty list")
    media, cuts, transcripts = {}, [], set()
    total = 0.0
    for i, row in enumerate(ranges):
        if not isinstance(row, dict):
            raise ValueError(f"range {i} must be an object")
        ident = row.get("source")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", ident):
            raise ValueError(f"range {i} needs a source ID using letters numbers underscores or hyphens")
        if ident not in sources:
            raise ValueError(f"range {i} refers to missing source {ident}")
        path = local_path(sources[ident], root, f"source {ident}")
        start = seconds(row.get("start"), f"range {i} start")
        end = seconds(row.get("end"), f"range {i} end")
        if end <= start:
            raise ValueError(f"range {i} end must be after start")
        cuts.append((path, end, f"range {i}"))
        media[path] = "source"
        total += end - start
        if not math.isfinite(total):
            raise ValueError("Total edit duration must be finite")
        if build_subtitles and not no_subtitles:
            transcripts.add(root / "transcripts" / f"{ident}.json")
    overlays = edl.get("overlays") or []
    if not isinstance(overlays, list):
        raise ValueError("overlays must be a list")
    for i, row in enumerate(overlays):
        if not isinstance(row, dict):
            raise ValueError(f"overlay {i} must be an object")
        path = local_path(row.get("file"), root, f"overlay {i}")
        start = seconds(row.get("start_in_output"), f"overlay {i} start")
        duration = seconds(row.get("duration"), f"overlay {i} duration")
        if duration == 0 or start + duration > total + 0.001:
            raise ValueError(f"overlay {i} must have a positive duration inside the output timeline")
        media.setdefault(path, "overlay")
        cuts.append((path, duration, f"overlay {i}"))
    subtitles = None
    if not no_subtitles and not build_subtitles and edl.get("subtitles"):
        subtitles = local_path(edl["subtitles"], root, "subtitles")
        if not subtitles.exists() and not Path(edl["subtitles"]).is_absolute():
            alternate = Path(edl["subtitles"]).resolve()
            if alternate.exists():
                subtitles = alternate
    grade = edl.get("grade")
    if grade is not None and not isinstance(grade, str):
        raise ValueError("grade must be a preset name or an FFmpeg filter string")
    return media, cuts, transcripts, subtitles


# Read stream metadata once per input without decoding the complete video
def probe(path):
    result = command(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    data = json.loads(result.stdout)
    if not isinstance(data, dict) or not isinstance(data.get("streams"), list):
        raise ValueError(f"ffprobe returned no stream list for {path}")
    return data


# Check that libass can actually select a font using one disposable synthetic frame
def caption_font():
    with tempfile.TemporaryDirectory(prefix="video-use-font-check-") as folder:
        Path(folder, "probe.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nVideo use\n", encoding="utf-8")
        result = command(["ffmpeg", "-hide_banner", "-v", "verbose", "-f", "lavfi", "-i",
                          "color=s=64x64:r=1:d=1", "-vf", "subtitles=probe.srt:force_style='FontName=Helvetica'",
                          "-frames:v", "1", "-f", "null", "-"], cwd=folder)
    selections = [line for line in result.stderr.splitlines() if "fontselect:" in line and " -> " in line]
    if not selections or "failed to find" in result.stderr.lower():
        raise ValueError("No usable caption font found; install a system font such as DejaVu Sans")
    return selections[0].split("fontselect:", 1)[1].strip()


# Collect actionable failures without creating edit outputs or running external services
def check_edit(edl_path, output, *, build_subtitles=False, no_subtitles=False, no_loudnorm=False):
    edl_path = Path(edl_path).resolve()
    output = Path(output).absolute()
    rows, warnings = [], []

    # Keep each check machine readable while allowing all independent failures to surface
    def add(name, ok, detail):
        rows.append({"name": name, "ok": bool(ok), "detail": str(detail)})

    try:
        edl = json.loads(edl_path.read_text(encoding="utf-8"))
        media, cuts, transcripts, subtitles = inputs_for(
            edl, edl_path.parent, build_subtitles=build_subtitles, no_subtitles=no_subtitles)
    except (OSError, ValueError, TypeError) as exc:
        add("edit", False, exc)
        return {"ok": False, "checks": rows, "warnings": warnings}
    add("edit", True, f"{len(cuts)} source and overlay intervals")
    for name in ("ffmpeg", "ffprobe"):
        result = check_media_tool(name)
        add(result.name, result.ok, result.detail)
    available = {row["name"]: row["ok"] for row in rows}
    inputs = set(media) | transcripts | {edl_path}
    if subtitles is not None:
        inputs.add(subtitles)
    if output.exists() or output.is_symlink():
        add("output", False, "Output already exists; choose a new output path")
    elif output.resolve() in {p.resolve() for p in inputs}:
        add("output", False, "Output must not replace an edit input")
    else:
        result = check_writable_directory("output directory", output.parent)
        add(result.name, result.ok, result.detail)
    result = check_writable_directory("edit directory", edl_path.parent)
    add(result.name, result.ok, result.detail)
    details = {}
    for path, role in media.items():
        if not path.is_file():
            add(str(path), False, "Input file is missing or is not a regular file")
            continue
        if not available["ffprobe"]:
            continue
        try:
            data = probe(path)
            video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
            if video is None:
                raise ValueError("Input has no video stream")
            if role == "source" and not any(s.get("codec_type") == "audio" for s in data["streams"]):
                raise ValueError("The installed legacy renderer requires source audio; prepare a compatible source first")
            details[path] = data
            add(str(path), True, "Video stream present" + (" with audio" if role == "source" else ""))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            add(str(path), False, exc)
    for path, end, name in cuts:
        if path not in details:
            continue
        data = details[path]
        stream = next(s for s in data["streams"] if s.get("codec_type") == "video")
        duration = None
        for value in (stream.get("duration"), data.get("format", {}).get("duration")):
            try:
                candidate = float(value)
                if math.isfinite(candidate) and candidate > 0:
                    duration = candidate
                    break
            except (TypeError, ValueError, OverflowError):
                pass
        if duration is None:
            warnings.append(f"{name}: duration is unknown; range bounds need a decode check")
        else:
            add(name, end <= duration + 0.001, f"Requested end {end:g}s; available duration {duration:g}s")
    for path in transcripts:
        try:
            words = json.loads(path.read_text(encoding="utf-8"))["words"]
            if not isinstance(words, list):
                raise ValueError("words must be a list")
            previous = -1.0
            for word in words:
                if not isinstance(word, dict):
                    raise ValueError("transcript entries must be objects")
                if word.get("type") != "word":
                    continue
                start, end = seconds(word.get("start"), "word start"), seconds(word.get("end"), "word end")
                if end < start or start < previous or not isinstance(word.get("text"), str):
                    raise ValueError("word text and ordered start/end times are required")
                previous = start
            add(str(path), True, "Transcript word timing is readable")
        except FileNotFoundError:
            warnings.append(f"Missing transcript: {path}; this source will have no generated captions")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            add(str(path), False, f"Cannot build subtitles: {exc}")
    if subtitles is not None:
        try:
            if not subtitles.is_file():
                raise ValueError(f"Missing subtitles: {subtitles}; fix the path or use --no-subtitles")
            with subtitles.open("rb") as stream:
                stream.read(1)
            add("subtitles", True, subtitles)
        except (OSError, ValueError) as exc:
            add("subtitles", False, f"Cannot read subtitles: {exc}")
    if available["ffmpeg"]:
        try:
            filters_text = command(["ffmpeg", "-hide_banner", "-filters"]).stdout
            encoders_text = command(["ffmpeg", "-hide_banner", "-encoders"]).stdout
            filters = {line.split()[1] for line in filters_text.splitlines() if len(line.split()) > 2}
            encoders = {line.split()[1] for line in encoders_text.splitlines() if len(line.split()) > 2}
            required = {"scale", "afade"}
            if not no_loudnorm:
                required.add("loudnorm")
            if edl.get("overlays"):
                required.update(("overlay", "setpts", "null"))
            needs_captions = not no_subtitles and (build_subtitles or subtitles is not None)
            if needs_captions:
                required.add("subtitles")
            if any(s.get("color_transfer") in ("arib-std-b67", "smpte2084") for d in details.values() for s in d["streams"]):
                required.update(("zscale", "tonemap", "format"))
            grade = edl.get("grade")
            if grade:
                try:
                    if __package__:
                        from .grade import PRESETS
                    else:
                        from grade import PRESETS
                    known = set(PRESETS)
                except ImportError:
                    known = set()
                if grade == "auto":
                    required.update(("eq", "fps", "signalstats", "metadata"))
                elif grade in known:
                    required.update(part.split("=", 1)[0] for part in PRESETS[grade].split(",") if part)
                else:
                    warnings.append("Raw grade filters and any files they reference need a separate render check")
            missing = sorted(required - filters)
            add("FFmpeg filters", not missing, "Available" if not missing else "Install an FFmpeg build with: " + ", ".join(missing))
            missing = sorted({"libx264", "aac"} - encoders)
            add("FFmpeg encoders", not missing, "Available" if not missing else "Install an FFmpeg build with: " + ", ".join(missing))
            if needs_captions and "subtitles" in filters:
                try:
                    add("caption font", True, caption_font())
                except ValueError as exc:
                    add("caption font", False, exc)
        except ValueError as exc:
            add("FFmpeg capabilities", False, exc)
    return {"ok": all(row["ok"] for row in rows), "checks": rows, "warnings": warnings}


# Present the same concise report in the standalone helper and the renderer
def print_report(report):
    for row in report["checks"]:
        print(f"[{'PASS' if row['ok'] else 'FAIL'}] {row['name']}: {row['detail']}")
    for warning in report["warnings"]:
        print(f"[NOTE] {warning}")


# Expose a check only command with the flags that affect planned render requirements
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("edl", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    parser.add_argument("--build-subtitles", action="store_true")
    parser.add_argument("--no-subtitles", action="store_true")
    parser.add_argument("--no-loudnorm", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON")
    args = parser.parse_args()
    report = check_edit(args.edl, args.output, build_subtitles=args.build_subtitles,
                        no_subtitles=args.no_subtitles, no_loudnorm=args.no_loudnorm)
    if args.json:
        print(json.dumps(report, indent=2, allow_nan=False))
    else:
        print_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
