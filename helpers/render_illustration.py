"""Render Penrose or CeTZ source into a deterministic illustration asset.

Penrose's pinned Roger CLI is cached outside the edit directory on first use.
CeTZ is compiled by Typst, which downloads the pinned @preview package declared
inside the .typ source. Both engines produce vector assets that can be imported
into Manim or used as high-resolution stills in the video pipeline.

Usage:
    python helpers/render_illustration.py penrose diagram.trio.json -o diagram.svg
    python helpers/render_illustration.py cetz diagram.typ -o diagram.svg
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


PENROSE_ROGER_VERSION = "3.3.1"
PENROSE_PACKAGE = f"@penrose/roger@{PENROSE_ROGER_VERSION}"


# run a subprocess and raise with the tail of its output when it fails
def run(command: list[str], *, cwd: Path | None = None) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise RuntimeError(f"command failed ({result.returncode}): {detail[-2500:]}")
    if result.stdout.strip():
        print(result.stdout.strip())


# pick the tool cache directory from the environment or fall back to the home cache
def default_tool_cache() -> Path:
    explicit = os.environ.get("VIDEO_USE_TOOL_CACHE")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path.home() / ".cache" / "video-use" / "tools"


# find a roger executable or install the pinned version into the cache with npm
def ensure_roger(cache_root: Path) -> Path:
    installed = shutil.which("roger")
    if installed:
        return Path(installed)
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("Penrose rendering requires Node.js and npm")
    prefix = cache_root / f"penrose-roger-{PENROSE_ROGER_VERSION}"
    executable = prefix / "node_modules" / ".bin" / "roger"
    if executable.exists():
        return executable
    # install into a private prefix so the pinned version never touches global node modules
    prefix.mkdir(parents=True, exist_ok=True)
    print(f"installing pinned Penrose renderer {PENROSE_ROGER_VERSION} in {prefix}")
    run(
        [
            npm,
            "install",
            "--no-audit",
            "--no-fund",
            "--no-save",
            "--prefix",
            str(prefix),
            PENROSE_PACKAGE,
        ]
    )
    if not executable.exists():
        raise RuntimeError("Penrose install completed without creating the roger CLI")
    return executable


# validate the trio and output paths then run roger and optionally dump optimizer steps
def render_penrose(
    trio: Path,
    output: Path,
    *,
    variation: str | None,
    dump_steps: bool,
    cache_root: Path,
) -> Path:
    if trio.suffixes[-2:] != [".trio", ".json"]:
        raise ValueError("Penrose input must end in .trio.json")
    if output.suffix.casefold() != ".svg":
        raise ValueError("Penrose output must be an .svg file")
    if not trio.exists():
        raise ValueError(f"Penrose trio does not exist: {trio}")
    output.parent.mkdir(parents=True, exist_ok=True)
    roger = ensure_roger(cache_root)
    command = [str(roger), "trio", str(trio), "--out", str(output)]
    if variation:
        command.extend(["--variation", variation])
    # dump intermediate svgs every ten steps into a sibling folder for debugging the layout solve
    if dump_steps:
        dump_dir = output.parent / f"{output.stem}_steps"
        dump_dir.mkdir(parents=True, exist_ok=True)
        command.extend(
            [
                "--dump-svgs",
                "--dump-interval",
                "10",
                "--dump-prefix",
                str(dump_dir / "step-"),
            ]
        )
    run(command, cwd=trio.parent)
    if not output.exists():
        raise RuntimeError("Penrose completed without writing the requested SVG")
    print(f"rendered Penrose illustration: {output}")
    return output


# compile a typ source with typst into svg png or pdf
def render_cetz(source: Path, output: Path) -> Path:
    if source.suffix.casefold() != ".typ":
        raise ValueError("CeTZ input must be a .typ file")
    if output.suffix.casefold() not in {".svg", ".png", ".pdf"}:
        raise ValueError("CeTZ output must be .svg, .png, or .pdf")
    if not source.exists():
        raise ValueError(f"CeTZ source does not exist: {source}")
    typst = shutil.which("typst")
    if typst is None:
        raise RuntimeError(
            "CeTZ rendering requires the Typst CLI; on macOS run 'brew install typst', "
            "or install it from https://github.com/typst/typst/releases"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    run([typst, "compile", str(source), str(output)], cwd=source.parent)
    if not output.exists():
        raise RuntimeError("Typst completed without writing the requested CeTZ asset")
    print(f"rendered CeTZ illustration: {output}")
    return output


# build the argparse parser with one subcommand per engine
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render Penrose constraint diagrams or CeTZ STEM illustrations"
    )
    subparsers = parser.add_subparsers(dest="engine", required=True)

    penrose = subparsers.add_parser("penrose")
    penrose.add_argument("source", type=Path)
    penrose.add_argument("-o", "--output", type=Path, required=True)
    penrose.add_argument("--variation", default=None)
    penrose.add_argument("--dump-steps", action="store_true")
    penrose.add_argument("--tool-cache", type=Path, default=default_tool_cache())

    cetz = subparsers.add_parser("cetz")
    cetz.add_argument("source", type=Path)
    cetz.add_argument("-o", "--output", type=Path, required=True)
    return parser


# command line entry that resolves paths and dispatches to the chosen engine
def main() -> None:
    args = build_parser().parse_args()
    try:
        source = args.source.expanduser().resolve()
        output = args.output.expanduser().resolve()
        if args.engine == "penrose":
            render_penrose(
                source,
                output,
                variation=args.variation,
                dump_steps=args.dump_steps,
                cache_root=args.tool_cache.expanduser().resolve(),
            )
        else:
            render_cetz(source, output)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()
