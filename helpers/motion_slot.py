"""Create, inspect and render isolated browser-native motion-design slots.

Usage:
    python helpers/motion_slot.py init --edit-dir /path/edit --name launch
    python helpers/motion_slot.py asset /path/edit/animations/launch logo.svg --source-url URL --license NOTE
    python helpers/motion_slot.py check /path/edit/animations/launch
    python helpers/motion_slot.py render /path/edit/animations/launch

HyperFrames and GSAP are optional, pinned slot-local npm dependencies. This
helper never installs packages, changes an EDL, or modifies the core renderer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skills" / "motion-design" / "assets"


# Validate a filename-sized identifier before constructing any output paths
def safe_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError("name must be 1–80 letters, digits, underscores or hyphens")
    return value


# Normalize an explicit render clock and require a whole number of frames
def validate_spec(width: int, height: int, fps: str, duration: float) -> dict:
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 64 or v > 8192 or v % 2 for v in (width, height)):
        raise ValueError("width/height must be even integers between 64 and 8192")
    try:
        rate = Fraction(fps)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("fps must be a positive number or rational") from exc
    if not 1 <= rate <= 120 or not math.isfinite(duration) or not 1 <= duration <= 600:
        raise ValueError("fps must be 1–120 and duration 1–600 seconds")
    if rate.denominator != 1:
        raise ValueError("this HyperFrames slot requires an integer fps")
    frames = duration * float(rate)
    if abs(frames - round(frames)) > 1e-5:
        raise ValueError("duration must land on an exact frame at the selected fps")
    return {"schema_version": 1, "width": width, "height": height, "fps": str(rate),
            "duration": duration, "frame_count": round(frames), "engine": "hyperframes",
            "hyperframes_version": "0.8.30", "gsap_version": "3.15.0"}


# Publish small metadata files without exposing a partially written JSON document
def atomic_json(path: Path, value: object) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2)
        handle.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


# Hash assets incrementally so provenance does not depend on filenames
def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# Create a new slot atomically and refuse existing projects or repository outputs
def init_slot(edit_dir: Path, name: str, spec: dict) -> Path:
    safe_name(name)
    normalized = validate_spec(spec["width"], spec["height"], spec["fps"], spec["duration"])
    if spec != normalized:
        raise ValueError("slot specification must match the validated engine and clock")
    edit_dir = edit_dir.expanduser().resolve()
    if edit_dir == ROOT or ROOT in edit_dir.parents:
        raise ValueError("session outputs must live outside the video-use repository")
    parent = edit_dir / "animations"
    if parent.is_symlink():
        raise ValueError("animations directory must not be a symlink")
    target = parent / name
    if target.exists() or target.is_symlink():
        raise ValueError(f"slot already exists; choose a new name: {target}")
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".motion-init-", dir=parent) as raw:
        staging = Path(raw) / "slot"
        staging.mkdir()
        for filename in ("motion-kit.js", "motion-kit.css", "package.json"):
            shutil.copyfile(ASSETS / filename, staging / filename)
        html = (ASSETS / "index.html").read_text(encoding="utf-8")
        values = {**spec, "title_size": round(spec["width"] * .058), "body_size": round(spec["width"] * .022)}
        for key, value in values.items():
            html = html.replace(f"__{key.upper()}__", str(value))
        (staging / "index.html").write_text(html, encoding="utf-8")
        (staging / "assets").mkdir()
        atomic_json(staging / "motion.json", spec)
        atomic_json(staging / "assets.json", {"schema_version": 1, "assets": []})
        atomic_json(staging / "index.motion.json", {
            "version": 1, "duration": spec["duration"], "assertions": [
                {"kind": "appearsBy", "selector": "#headline .motion-text-part:last-child", "bySec": spec["duration"] * .5},
                {"kind": "before", "a": "#headline .motion-text-part:first-child", "b": "#status .motion-text-part:first-child"},
            ],
        })
        # A container can share preinstalled packages without installing anything here.
        preinstalled = os.environ.get("VIDEO_USE_MOTION_NODE_MODULES")
        if preinstalled:
            modules = Path(preinstalled).resolve(strict=True)
            if not (modules / "hyperframes" / "package.json").is_file():
                raise ValueError("VIDEO_USE_MOTION_NODE_MODULES must contain hyperframes")
            (staging / "node_modules").symlink_to(modules, target_is_directory=True)
        staging.rename(target)
    return target


# Copy an explicitly supplied asset and retain source rights note and content hash
def add_asset(slot: Path, source: Path, source_url: str, license_note: str) -> dict:
    slot = slot.resolve(strict=True)
    if not (slot / "motion.json").is_file():
        raise ValueError("not a motion slot: missing motion.json")
    source = source.resolve(strict=True)
    if not source.is_file() or not source_url.strip() or not license_note.strip():
        raise ValueError("asset needs a file, source URL and explicit rights/license note")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,150}", source.name):
        raise ValueError("asset filename contains unsupported characters")
    folder = slot / "assets"
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("assets must be a real directory inside the slot")
    destination = folder / source.name
    if destination.exists() or destination.is_symlink():
        raise ValueError("asset already exists; use a distinct filename")
    manifest_path = slot / "assets.json"
    if manifest_path.is_symlink():
        raise ValueError("asset manifest must not be a symbolic link")
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest.get("assets"), list):
        raise ValueError("asset manifest requires an assets list")
    item = {"path": f"assets/{source.name}", "source_url": source_url,
            "license_note": license_note, "sha256": fingerprint(source), "size": source.stat().st_size}
    with destination.open("xb") as out:
        try:
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, out)
        except Exception:
            out.close()
            destination.unlink(missing_ok=True)
            raise
    try:
        item["sha256"] = fingerprint(destination)
        item["size"] = destination.stat().st_size
        manifest["assets"].append(item)
        atomic_json(manifest_path, manifest)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return item


# Require the exact installed engine so rerenders do not silently upgrade
def require_hyperframes(slot: Path) -> Path:
    manifest = slot / "node_modules" / "hyperframes" / "package.json"
    gsap = slot / "node_modules" / "gsap" / "package.json"
    binary = slot / "node_modules" / ".bin" / "hyperframes"
    if not manifest.is_file() or not gsap.is_file() or not binary.is_file():
        raise ValueError(f"missing optional motion dependencies; run in {slot}:\n"
                         "HYPERFRAMES_SKIP_SKILLS=1 HYPERFRAMES_NO_UPDATE_CHECK=1 npm install\n"
                         "Then run npx hyperframes browser ensure")
    if json.loads(manifest.read_text())["version"] != "0.8.30":
        raise ValueError("this slot requires hyperframes 0.8.30; restore its package-lock.json with npm ci")
    if json.loads(gsap.read_text())["version"] != "3.15.0":
        raise ValueError("this slot requires gsap 3.15.0; restore its package-lock.json with npm ci")
    return binary


# Execute the installed CLI without npx downloads or global skill auto-installation
def engine_command(slot: Path, arguments: list[str]) -> None:
    binary = require_hyperframes(slot)
    env = {**os.environ, "HYPERFRAMES_SKIP_SKILLS": "1", "HYPERFRAMES_NO_UPDATE_CHECK": "1"}
    subprocess.run([str(binary), *arguments], cwd=slot, env=env, check=True)


# Check structure and seeked motion before production capture
def check_slot(slot: Path) -> None:
    slot = slot.resolve(strict=True)
    engine_command(slot, ["lint", "."])
    engine_command(slot, ["check", "."])


# Verify the actual encoded stream not just a successful renderer exit code
def verify_video(path: Path, spec: dict) -> dict:
    probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(path)],
                           check=True, capture_output=True, text=True)
    data = json.loads(probe.stdout)
    video = next((stream for stream in data["streams"] if stream["codec_type"] == "video"), None)
    if video is None:
        raise ValueError("render has no video stream")
    if (video["width"], video["height"]) != (spec["width"], spec["height"]):
        raise ValueError("render dimensions differ from motion.json")
    if Fraction(video["avg_frame_rate"]) != Fraction(spec["fps"]):
        raise ValueError("render frame rate differs from motion.json")
    if int(video["nb_read_frames"]) != spec["frame_count"]:
        raise ValueError("render frame count differs from motion.json")
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"], check=True, capture_output=True)
    return data


# Render to a temporary sibling validate it and publish only a complete video
def render_slot(slot: Path, output: Path | None = None) -> Path:
    slot = slot.resolve(strict=True)
    spec = json.loads((slot / "motion.json").read_text())
    normalized = validate_spec(spec["width"], spec["height"], spec["fps"], spec["duration"])
    if normalized["frame_count"] != spec["frame_count"]:
        raise ValueError("motion.json contains an inconsistent frame count")
    output = (output or slot / "render.mp4").absolute()
    if output.suffix.lower() != ".mp4" or output.is_symlink() or output.exists():
        raise ValueError("output must be a new .mp4 file; use a versioned name for revisions")
    sidecar = output.with_suffix(".verify.json")
    if sidecar.exists() or sidecar.is_symlink():
        raise ValueError("verification sidecar already exists; use a versioned output name")
    if ROOT == output.resolve() or ROOT in output.resolve().parents:
        raise ValueError("render outputs must live outside the framework repository")
    output.parent.mkdir(parents=True, exist_ok=True)
    require_hyperframes(slot)
    with tempfile.TemporaryDirectory(prefix=".motion-render-", dir=output.parent) as raw:
        temporary = Path(raw) / "render.mp4"
        engine_command(slot, ["render", ".", "--output", str(temporary), "--fps", spec["fps"],
                              "--no-best-effort", "--strict"])
        metadata = verify_video(temporary, spec)
        report = Path(raw) / "verify.json"
        atomic_json(report, {"spec": spec, "sha256": fingerprint(temporary), "probe": metadata})
        created = []
        try:
            for source, target in ((temporary, output), (report, sidecar)):
                os.link(source, target)
                created.append(target)
        except Exception:
            for target in created:
                target.unlink(missing_ok=True)
            raise
    return output


# Parse the small standalone CLI and surface actionable failures without a traceback
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--edit-dir", type=Path, required=True)
    init.add_argument("--name", required=True)
    init.add_argument("--width", type=int, default=1920)
    init.add_argument("--height", type=int, default=1080)
    init.add_argument("--fps", default="30")
    init.add_argument("--duration", type=float, default=8)
    asset = commands.add_parser("asset")
    asset.add_argument("slot", type=Path)
    asset.add_argument("source", type=Path)
    asset.add_argument("--source-url", required=True)
    asset.add_argument("--license", required=True, dest="license_note")
    check = commands.add_parser("check")
    check.add_argument("slot", type=Path)
    render = commands.add_parser("render")
    render.add_argument("slot", type=Path)
    render.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(init_slot(args.edit_dir, args.name, validate_spec(args.width, args.height, args.fps, args.duration)))
        elif args.command == "asset":
            print(json.dumps(add_asset(args.slot, args.source, args.source_url, args.license_note), indent=2))
        elif args.command == "check":
            check_slot(args.slot)
        else:
            print(render_slot(args.slot, args.output))
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"motion_slot: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
