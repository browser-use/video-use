"""Preflight checks for video-use's external tools and session directories."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

MIN_FFMPEG_MAJOR = 4


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Backend:
    command: str
    args: tuple[str, ...]
    description: str


BACKENDS = {
    "hyperframes": Backend(
        command="npx",
        args=("--no-install", "hyperframes", "--version"),
        description="HyperFrames via the local npm project",
    ),
    "remotion": Backend(
        command="npx",
        args=("--no-install", "remotion", "--version"),
        description="Remotion via the local npm project",
    ),
    "manim": Backend(
        command="manim",
        args=("--version",),
        description="Manim Community Edition",
    ),
}


_VERSION_RE = re.compile(r"\bversion\s+([0-9]+(?:\.[0-9]+)*)\b", re.IGNORECASE)
_V_VERSION_RE = re.compile(r"\bv([0-9]+(?:\.[0-9]+)*)\b", re.IGNORECASE)
_BARE_VERSION_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)+)\s*$")
_GIT_VERSION_RE = re.compile(r"\bversion\s+(N-\d+-g[0-9a-f]+)\b", re.IGNORECASE)


def _version_text(executable: str, args: Sequence[str], *, cwd: Path | None = None) -> tuple[str | None, str]:
    try:
        completed = subprocess.run(
            [executable, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)

    output = (completed.stdout or "") + (completed.stderr or "")
    match = None
    for line in output.splitlines()[:5]:
        match = (_VERSION_RE.search(line) or _V_VERSION_RE.search(line)
                 or _BARE_VERSION_RE.search(line) or _GIT_VERSION_RE.search(line))
        if match:
            break
    if not match:
        first_line = output.strip().splitlines()[0] if output.strip() else "no version output"
        return None, first_line
    if completed.returncode != 0:
        return None, f"command exited {completed.returncode}"
    return match.group(1), ""


def check_media_tool(name: str) -> CheckResult:
    executable = shutil.which(name)
    if executable is None:
        return CheckResult(name, False, "not found on PATH")

    version, error = _version_text(executable, ("-version",))
    if version is None:
        return CheckResult(name, False, f"could not read version ({error})")
    if not version.upper().startswith("N-") and int(version.split(".", 1)[0]) < MIN_FFMPEG_MAJOR:
        return CheckResult(name, False, f"version {version} is older than {MIN_FFMPEG_MAJOR}.0")
    return CheckResult(name, True, f"{version} ({executable})")


def check_writable_directory(name: str, path: Path, *, create: bool = False) -> CheckResult:
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            return CheckResult(name, False, f"does not exist: {path}")
        if not path.is_dir():
            return CheckResult(name, False, f"is not a directory: {path}")
        with tempfile.NamedTemporaryFile(
            prefix=".video-use-doctor-",
            dir=path,
            delete=True,
        ):
            pass
    except OSError as exc:
        return CheckResult(name, False, f"not writable ({exc})")
    return CheckResult(name, True, str(path))


def check_backend(name: str, project_dir: Path | None = None) -> CheckResult:
    backend = BACKENDS[name]
    executable = shutil.which(backend.command)
    if executable is None:
        return CheckResult(name, False, f"{backend.command} not found on PATH")

    if backend.command == "npx" and project_dir is None:
        return CheckResult(name, False, "provide --backend-dir pointing to the npm project")
    version, error = _version_text(executable, backend.args, cwd=project_dir if backend.command == "npx" else None)
    if version is None:
        return CheckResult(name, False, f"{backend.description} unavailable ({error})")
    return CheckResult(name, True, f"{version} ({backend.description})")


def run_checks(videos_dir: Path, required_backends: Sequence[str], *, backend_dir: Path | None = None) -> list[CheckResult]:
    results = [check_media_tool(name) for name in ("ffmpeg", "ffprobe")]
    footage_result = check_writable_directory("footage", videos_dir)
    if footage_result.ok:
        try:
            with os.scandir(videos_dir) as entries:
                next(entries, None)
        except OSError as exc:
            footage_result = CheckResult("footage", False, f"not readable ({exc})")
    results.append(footage_result)
    results.append(
        check_writable_directory(
            "edit output",
            videos_dir / "edit",
            create=footage_result.ok,
        )
    )
    results.extend(check_backend(name, backend_dir or videos_dir) for name in required_backends)
    return results


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify video-use dependencies and session directories before editing."
    )
    parser.add_argument(
        "--videos-dir",
        type=Path,
        default=Path.cwd(),
        help="Footage directory; defaults to the current directory.",
    )
    parser.add_argument(
        "--backend-dir", type=Path,
        help="Local npm project for optional backends; defaults to --videos-dir.",
    )
    parser.add_argument(
        "--require",
        dest="required_backends",
        action="append",
        choices=sorted(BACKENDS),
        metavar="BACKEND",
        help="Also verify an optional backend (repeat for multiple backends).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    results = run_checks(args.videos_dir.expanduser().resolve(), args.required_backends or [],
                         backend_dir=args.backend_dir.expanduser().resolve() if args.backend_dir else None)
    passed = 0
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.name}: {result.detail}")
        passed += result.ok
    failed = len(results) - passed
    print(f"Summary: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
