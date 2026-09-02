#!/usr/bin/env python3
"""Render selected Manim chapters and build small authoring-review artifacts."""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Sequence

from PIL import Image


# a clear user actionable preview failure
class PreviewError(RuntimeError):
    """A clear, user-actionable preview failure."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


# parse the script and return the names of every top level class
def scene_classes(script: Path) -> set[str]:
    try:
        source = script.read_text(encoding="utf-8")
    except OSError as exc:
        raise PreviewError(f"could not read Manim script {script}: {exc}") from exc
    try:
        tree = ast.parse(source, filename=str(script))
    except SyntaxError as exc:
        raise PreviewError(f"Manim script has invalid Python syntax: {exc}") from exc
    return {node.name for node in tree.body if isinstance(node, ast.ClassDef)}


# find the project edit directory from an explicit flag or by walking up from the script
def resolve_edit_dir(script: Path, explicit: Path | None = None) -> Path:
    script = script.resolve()
    if explicit is not None:
        edit_dir = explicit.resolve()
        if edit_dir.name != "edit":
            raise PreviewError("--edit-dir must point to the project's edit directory")
        return edit_dir
    for candidate in (script.parent, *script.parents):
        if candidate.name == "edit":
            return candidate
    raise PreviewError(
        "the Manim script must live under a project edit directory; "
        "otherwise pass --edit-dir /path/to/project/edit"
    )


# resolve an executable on the path or raise a clear error
def require_executable(value: str, *, purpose: str) -> str:
    path = shutil.which(value)
    if path is None:
        raise PreviewError(f"{purpose} is unavailable: executable {value!r} was not found")
    return path


# run a command through the injected runner and turn failures into PreviewError
def _run(
    command: Sequence[str],
    *,
    runner: Runner,
    cwd: Path,
    timeout_s: float,
    purpose: str,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(
            list(command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except FileNotFoundError as exc:
        raise PreviewError(f"{purpose} is unavailable: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PreviewError(f"{purpose} timed out after {timeout_s:g} seconds") from exc
    # surface the last line of stderr or stdout as the failure message
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        message = detail[-1] if detail else f"process exited with status {result.returncode}"
        raise PreviewError(f"{purpose} failed: {message}")
    return result


# read duration size and frame rate from a video with ffprobe
def probe_video(
    video: Path,
    *,
    ffprobe_bin: str,
    runner: Runner,
    timeout_s: float,
) -> dict[str, Any]:
    result = _run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,avg_frame_rate",
            "-of",
            "json",
            str(video),
        ],
        runner=runner,
        cwd=video.parent,
        timeout_s=timeout_s,
        purpose="ffprobe inspection",
    )
    # pull the first video stream and reject incomplete or malformed metadata
    try:
        payload = json.loads(result.stdout)
        stream = next(
            entry for entry in payload["streams"] if entry.get("codec_type") == "video"
        )
        duration = float(payload["format"]["duration"])
        frame_rate = float(Fraction(stream["avg_frame_rate"]))
        width = int(stream["width"])
        height = int(stream["height"])
    except (KeyError, StopIteration, TypeError, ValueError, ZeroDivisionError, json.JSONDecodeError) as exc:
        raise PreviewError(f"ffprobe returned incomplete metadata for {video}") from exc
    if duration <= 0 or width <= 0 or height <= 0 or frame_rate <= 0:
        raise PreviewError(f"rendered scene has invalid media metadata: {video}")
    return {
        "duration_s": duration,
        "width": width,
        "height": height,
        "frame_rate": frame_rate,
    }


# extract a single frame at a timestamp with ffmpeg and verify it was written
def _extract_frame(
    video: Path,
    output: Path,
    timestamp_s: float,
    *,
    ffmpeg_bin: str,
    runner: Runner,
    timeout_s: float,
) -> None:
    _run(
        [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{max(0.0, timestamp_s):.6f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(output),
        ],
        runner=runner,
        cwd=output.parent,
        timeout_s=timeout_s,
        purpose=f"frame extraction at {timestamp_s:.3f}s",
    )
    if not output.is_file() or output.stat().st_size == 0:
        raise PreviewError(f"expected frame artifact was not produced: {output}")


# extract the initial and final frames and a five frame contact sheet
def build_review_frames(
    video: Path,
    destination: Path,
    metadata: dict[str, Any],
    *,
    ffmpeg_bin: str,
    runner: Runner,
    timeout_s: float,
) -> dict[str, str]:
    duration = float(metadata["duration_s"])
    # the final frame sits one frame before the end so ffmpeg does not run past the stream
    final_time = max(0.0, duration - 1 / float(metadata["frame_rate"]))
    initial = destination / "initial.png"
    final = destination / "final.png"
    _extract_frame(
        video,
        initial,
        0.0,
        ffmpeg_bin=ffmpeg_bin,
        runner=runner,
        timeout_s=timeout_s,
    )
    _extract_frame(
        video,
        final,
        final_time,
        ffmpeg_bin=ffmpeg_bin,
        runner=runner,
        timeout_s=timeout_s,
    )

    # sample five evenly spaced frames and paste their thumbnails side by side into one sheet
    with tempfile.TemporaryDirectory(prefix="contact-", dir=destination) as temp_name:
        temp_dir = Path(temp_name)
        frame_paths: list[Path] = []
        for index, proportion in enumerate((0.0, 0.25, 0.5, 0.75, 1.0)):
            frame = temp_dir / f"frame_{index}.png"
            _extract_frame(
                video,
                frame,
                min(final_time, duration * proportion),
                ffmpeg_bin=ffmpeg_bin,
                runner=runner,
                timeout_s=timeout_s,
            )
            frame_paths.append(frame)
        images = [Image.open(path).convert("RGB") for path in frame_paths]
        thumbnail_width = 320
        thumbnail_height = max(1, round(images[0].height * thumbnail_width / images[0].width))
        sheet = Image.new("RGB", (thumbnail_width * 5, thumbnail_height), "black")
        for index, frame in enumerate(images):
            frame.thumbnail((thumbnail_width, thumbnail_height), Image.Resampling.LANCZOS)
            sheet.paste(frame, (index * thumbnail_width, 0))
        contact_sheet = destination / "contact_sheet.png"
        sheet.save(contact_sheet)
        for frame in images:
            frame.close()

    if not contact_sheet.is_file() or contact_sheet.stat().st_size == 0:
        raise PreviewError(f"expected contact sheet was not produced: {contact_sheet}")
    return {
        "initial_frame": str(initial),
        "final_frame": str(final),
        "contact_sheet": str(contact_sheet),
    }


# locate the newest rendered mp4 for a scene ignoring partial and section clips
def _find_scene_video(media_dir: Path, scene_name: str) -> Path:
    matches = [
        path
        for path in media_dir.rglob(f"{scene_name}.mp4")
        if "partial_movie_files" not in path.parts and "sections" not in path.parts
    ]
    if not matches:
        raise PreviewError(f"Manim reported success but produced no video for scene {scene_name!r}")
    return max(matches, key=lambda path: path.stat().st_mtime_ns)


# load the section json manim wrote for a scene or return an empty list
def _section_metadata(media_dir: Path, scene_name: str) -> list[dict[str, Any]]:
    candidates = [
        path
        for path in media_dir.rglob(f"{scene_name}.json")
        if "sections" in path.parts
    ]
    if not candidates:
        return []
    source = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [{"metadata_file": str(source), "status": "unreadable"}]
    sections = data if isinstance(data, list) else data.get("sections", [])
    return [dict(section) for section in sections if isinstance(section, dict)]


# render one scene and build its review artifacts and report under edit verify
def render_scene(
    script: Path,
    scene_name: str,
    *,
    edit_dir: Path | None = None,
    manim_bin: str = "manim",
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    runner: Runner = subprocess.run,
    timeout_s: float = 900.0,
) -> dict[str, Any]:
    script = script.resolve()
    if not script.is_file():
        raise PreviewError(f"Manim script does not exist: {script}")
    if scene_name not in scene_classes(script):
        raise PreviewError(f"scene class {scene_name!r} was not found in {script.name}")
    resolved_edit = resolve_edit_dir(script, edit_dir)
    verify_root = (resolved_edit / "verify").resolve()
    destination = verify_root / "manim_previews" / script.stem / scene_name
    destination.mkdir(parents=True, exist_ok=True)
    # refuse to write anywhere outside the verify folder
    if not destination.resolve().is_relative_to(verify_root):
        raise PreviewError("refusing to write preview artifacts outside edit/verify")

    manim = require_executable(manim_bin, purpose="Manim")
    ffmpeg = require_executable(ffmpeg_bin, purpose="ffmpeg")
    ffprobe = require_executable(ffprobe_bin, purpose="ffprobe")
    media_dir = destination / "media"
    command = [
        manim,
        "render",
        "-ql",
        "--save_sections",
        "--media_dir",
        str(media_dir),
        "--log_dir",
        str(destination / "logs"),
        "--output_file",
        scene_name,
        str(script),
        scene_name,
    ]
    _run(
        command,
        runner=runner,
        cwd=script.parent,
        timeout_s=timeout_s,
        purpose=f"Manim render for {scene_name}",
    )
    rendered = _find_scene_video(media_dir, scene_name)
    scene_video = destination / f"{scene_name}.mp4"
    shutil.copy2(rendered, scene_video)
    if not scene_video.is_file() or scene_video.stat().st_size == 0:
        raise PreviewError(f"expected scene video was not produced: {scene_video}")
    metadata = probe_video(
        scene_video,
        ffprobe_bin=ffprobe,
        runner=runner,
        timeout_s=timeout_s,
    )
    frames = build_review_frames(
        scene_video,
        destination,
        metadata,
        ffmpeg_bin=ffmpeg,
        runner=runner,
        timeout_s=timeout_s,
    )
    report = {
        "scene": scene_name,
        "status": "ok",
        "video": str(scene_video),
        **frames,
        **metadata,
        "sections": _section_metadata(media_dir, scene_name),
    }
    report_path = destination / "report.json"
    report["report"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


# render each requested scene and gather the reports
def preview_scenes(
    script: Path,
    scene_names: Sequence[str],
    **kwargs: Any,
) -> dict[str, Any]:
    if not scene_names:
        raise PreviewError("provide at least one scene class name")
    reports = []
    for scene_name in scene_names:
        reports.append(render_scene(script, scene_name, **kwargs))
    return {"status": "ok", "script": str(script.resolve()), "scenes": reports}


# define and parse the command line arguments
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render selected Manim scene classes into edit/verify review artifacts."
    )
    parser.add_argument("script", type=Path, help="Path to the authored Manim Python script")
    parser.add_argument("scenes", nargs="+", help="One or more scene class names")
    parser.add_argument("--edit-dir", type=Path, help="Explicit project edit directory")
    parser.add_argument("--manim-bin", default="manim", help="Manim executable name or path")
    parser.add_argument("--timeout-s", type=float, default=900.0, help="Per-command timeout")
    return parser.parse_args(argv)


# run the preview and print a json report or a clear failure
def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = preview_scenes(
            args.script,
            args.scenes,
            edit_dir=args.edit_dir,
            manim_bin=args.manim_bin,
            timeout_s=args.timeout_s,
        )
    except PreviewError as exc:
        print(f"preview failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
