"""Run srt_driven_edit across every episode subdirectory under a root.

Discovery convention (flat per-episode layout):
    <root>/<ep>/source.mp4       required
    <root>/<ep>/script.srt       required
    <root>/<ep>/edit_plan.json   required  (Form A or B)
    <root>/<ep>/voice.wav        optional  (global voice for this ep)

Outputs:
    <root>/<ep>/final.mp4
    <root>/<ep>/edit/...         (EDL, QC report, cache — managed by srt_driven_edit)
    <root>/run_episodes_summary.json

Usage:
    python helpers/run_episodes.py batch/
    python helpers/run_episodes.py batch/ --bg-volume 0.1 --style cjk-natural
    python helpers/run_episodes.py batch/ --continue-on-error
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from srt_driven_edit import (
        Job, run_job, preflight, safe_ascii_name,
        make_failure_record,
    )
except Exception as e:
    raise SystemExit(
        "run_episodes: failed to import from srt_driven_edit.py. "
        f"Both files must be importable from the same helpers/ dir. ({e})"
    )


REQUIRED_FILES = ("source.mp4", "script.srt", "edit_plan.json")
OPTIONAL_VOICE = "voice.wav"


@dataclass
class EpisodeJob:
    name: str
    root: Path
    source: Path
    srt: Path
    plan: Path
    voice: Path | None


def discover_episodes(root: Path) -> list[EpisodeJob]:
    """Return episode dirs under `root` that have the required file set.

    Dirs missing a required file are skipped with a printed reason — never
    cause a hard failure here, so a partial batch is still actionable.
    Hard-fails only if NO usable dir is found.
    """
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")

    eps: list[EpisodeJob] = []
    skipped: list[tuple[str, list[str]]] = []
    for sub in sorted(root.iterdir(), key=lambda p: p.name):
        if not sub.is_dir():
            continue
        missing = [f for f in REQUIRED_FILES if not (sub / f).is_file()]
        if missing:
            skipped.append((sub.name, missing))
            continue
        voice = sub / OPTIONAL_VOICE
        eps.append(EpisodeJob(
            name=sub.name,
            root=sub.resolve(),
            source=(sub / "source.mp4").resolve(),
            srt=(sub / "script.srt").resolve(),
            plan=(sub / "edit_plan.json").resolve(),
            voice=voice.resolve() if voice.is_file() else None,
        ))

    if skipped:
        print(f"skipped {len(skipped)} dir(s) missing required files:")
        for name, miss in skipped:
            print(f"  {name}: missing {', '.join(miss)}")
    if not eps:
        raise SystemExit(
            f"no usable episode dirs under {root}. Each ep dir needs: "
            f"{list(REQUIRED_FILES)}"
        )
    return eps


def _make_job(ep: EpisodeJob, opts: dict) -> Job:
    return Job(
        source=ep.source,
        srt=ep.srt,
        plan=ep.plan,
        voice=ep.voice,
        bg_volume=opts["bg_volume"],
        tolerance=opts["tolerance"],
        trim_direction=opts["trim_direction"],
        on_short=opts["on_short"],
        style=opts["style"],
        fontsdir=opts["fontsdir"],
        output=ep.root / "final.mp4",
        name=ep.name,
        no_cache=opts["no_cache"],
        keep_intermediates=opts["keep_intermediates"],
        no_overwrite=opts["no_overwrite"],
        mode=opts.get("mode", "full"),
    )


def run_episodes(
    root: Path,
    *,
    ffmpeg_version: str,
    bg_volume: float = 0.0,
    tolerance: float = 0.5,
    trim_direction: str = "tail",
    on_short: str = "error",
    style: str = "auto",
    fontsdir: Path | None = None,
    no_cache: bool = False,
    no_overwrite: bool = False,
    keep_intermediates: bool = False,
    continue_on_error: bool = False,
    mode: str = "full",
) -> dict:
    """Discover + run every episode under `root`. Returns a summary dict and
    also writes it to `<root>/run_episodes_summary.json`."""
    root = root.resolve()
    eps = discover_episodes(root)
    print(f"\ndiscovered {len(eps)} episode(s) under {root}:")
    for ep in eps:
        print(f"  {ep.name}  voice={'yes' if ep.voice else 'no'}")

    opts = {
        "bg_volume": bg_volume,
        "tolerance": tolerance,
        "trim_direction": trim_direction,
        "on_short": on_short,
        "style": style,
        "fontsdir": fontsdir,
        "no_cache": no_cache,
        "no_overwrite": no_overwrite,
        "keep_intermediates": keep_intermediates,
        "mode": mode,
    }

    results: list[dict] = []
    t0 = time.time()
    for i, ep in enumerate(eps):
        print(f"\n[{i + 1}/{len(eps)}] === {ep.name} ===")
        job = _make_job(ep, opts)
        try:
            qc = run_job(job, ffmpeg_version)
            results.append(qc)
        except (SystemExit, Exception) as e:
            if continue_on_error:
                print(f"[{i + 1}/{len(eps)}] FAILED: "
                      f"{type(e).__name__}: {e}")
                results.append(make_failure_record(
                    index=i, name=ep.name, error=e, job=job,
                ))
                continue
            raise

    ok = sum(1 for r in results if r.get("ok"))
    summary = {
        "root": str(root),
        "episodes_total": len(eps),
        "ok": ok,
        "elapsed_s": round(time.time() - t0, 2),
        "results": results,
    }
    summary_path = root / "run_episodes_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n{ok}/{len(results)} episodes ok ({summary['elapsed_s']}s)")
    print(f"summary → {summary_path}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run srt_driven_edit across every ep*/ subdirectory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Per-episode layout:\n"
            "  <root>/<ep>/source.mp4        required\n"
            "  <root>/<ep>/script.srt        required\n"
            "  <root>/<ep>/edit_plan.json    required (Form A or B)\n"
            "  <root>/<ep>/voice.wav         optional\n\n"
            "Outputs land at <root>/<ep>/final.mp4 with edit/ artifacts."
        ),
    )
    ap.add_argument("root", type=Path,
                    help="directory whose immediate subdirs are episodes")
    ap.add_argument("--bg-volume", type=float, default=0.0)
    ap.add_argument("--tolerance", type=float, default=0.5)
    ap.add_argument("--trim-direction", choices=["tail", "head", "center"], default="tail")
    ap.add_argument("--on-short", choices=["error", "pad"], default="error")
    ap.add_argument("--style", default="auto")
    ap.add_argument("--fontsdir", type=Path, default=None)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-overwrite", action="store_true")
    ap.add_argument("--keep-intermediates", action="store_true")
    ap.add_argument("--continue-on-error", action="store_true",
                    help="skip episodes that fail instead of aborting")
    ap.add_argument(
        "--mode", choices=["full", "extract"], default="full",
        help="'full' (default) runs the complete pipeline per episode. "
             "'extract' stops after segment extraction and saves clips "
             "under each ep's edit/ dir; gap clips, voice mixing, "
             "subtitle burn, and QC report are skipped.",
    )
    args = ap.parse_args()

    versions = preflight()
    print(f"== ffmpeg {versions['ffmpeg']} / ffprobe {versions['ffprobe']} ==")

    summary = run_episodes(
        args.root,
        ffmpeg_version=versions["ffmpeg"],
        bg_volume=args.bg_volume,
        tolerance=args.tolerance,
        trim_direction=args.trim_direction,
        on_short=args.on_short,
        style=args.style,
        fontsdir=args.fontsdir.resolve() if args.fontsdir else None,
        no_cache=args.no_cache,
        no_overwrite=args.no_overwrite,
        keep_intermediates=args.keep_intermediates,
        continue_on_error=args.continue_on_error,
        mode=args.mode,
    )
    # Exit nonzero if any episode failed (even with --continue-on-error,
    # the caller probably wants to know).
    if summary["ok"] < summary["episodes_total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
