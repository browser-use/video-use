"""Batch-transcribe every video in a directory with parallel workers.

Walks <videos_dir> for common video extensions, runs ElevenLabs Scribe or the
local engine on each, writes transcripts to <videos_dir>/edit/transcripts/<name>.json.

Cached per-file: any source that already has a transcript is skipped.
The local engine runs one file at a time because it loads a single model
into this process; Scribe uploads run four at a time by default.

Usage:
    python helpers/transcribe_batch.py <videos_dir>
    python helpers/transcribe_batch.py <videos_dir> --workers 4
    python helpers/transcribe_batch.py <videos_dir> --num-speakers 2
    python helpers/transcribe_batch.py <videos_dir> --edit-dir /custom/edit
    python helpers/transcribe_batch.py <videos_dir> --engine local
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from transcribe import (
    add_engine_arguments,
    announce_engine,
    load_env,
    local_options_from,
    preflight_local,
    resolve_engine,
    transcribe_one,
    transcript_path,
)


VIDEO_EXTS = {".mp4", ".MP4", ".mov", ".MOV", ".mkv", ".MKV", ".avi", ".AVI", ".m4v"}


# list files in the directory whose suffix is a known video extension in sorted order
def find_videos(videos_dir: Path) -> list[Path]:
    videos = sorted(
        p for p in videos_dir.iterdir()
        if p.is_file() and p.suffix in VIDEO_EXTS
    )
    return videos


# the local engine holds one model per process so more than one worker only contends
def worker_count(engine: str, requested: int) -> int:
    return 1 if engine == "local" else requested


# cli entry point that splits videos into cached and pending sets then transcribes the pending ones in parallel
def main() -> None:
    ap = argparse.ArgumentParser(description="Parallel batch transcription of a videos directory")
    ap.add_argument("videos_dir", type=Path, help="Directory containing source videos")
    ap.add_argument(
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <videos_dir>/edit)",
    )
    ap.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4, local engine always 1)")
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help="Optional ISO language code. Omit to auto-detect per file.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Optional number of speakers. Improves diarization when known (elevenlabs only).",
    )
    ap.add_argument(
        "--audio-track",
        type=int,
        default=0,
        help="Zero-based audio track to transcribe (OBS: 0 = game, 1 = mic).",
    )
    add_engine_arguments(ap)
    args = ap.parse_args()

    videos_dir = args.videos_dir.resolve()
    if not videos_dir.is_dir():
        sys.exit(f"not a directory: {videos_dir}")

    # the engine is settled before any work so even a fully cached run reports it
    env = load_env()
    engine, source = resolve_engine(args.engine, env)
    announce_engine(engine, source, env)
    api_key = env["ELEVENLABS_API_KEY"][0] if "ELEVENLABS_API_KEY" in env else None
    if engine == "elevenlabs" and not api_key:
        sys.exit("ELEVENLABS_API_KEY not found in .env or environment")

    edit_dir = (args.edit_dir or (videos_dir / "edit")).resolve()
    (edit_dir / "transcripts").mkdir(parents=True, exist_ok=True)

    videos = find_videos(videos_dir)
    if not videos:
        sys.exit(f"no videos found in {videos_dir}")

    already_cached = [v for v in videos
                      if transcript_path(edit_dir, v, args.audio_track).exists()]
    pending = videos if args.force else [v for v in videos if v not in already_cached]

    print(f"found {len(videos)} videos ({len(already_cached)} cached, {len(pending)} to transcribe)")
    if not pending:
        print("nothing to do")
        return

    # settle the local library once here so a missing install exits with its message instead of failing every worker
    local_options = local_options_from(args)
    if engine == "local":
        preflight_local(local_options)

    workers = worker_count(engine, args.workers)
    if workers != args.workers:
        print(f"local engine runs one file at a time (requested {args.workers} workers)")
    print(f"transcribing {len(pending)} files with {workers} parallel workers")
    t0 = time.time()

    errors: list[tuple[Path, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                transcribe_one,
                video=v,
                edit_dir=edit_dir,
                engine=engine,
                api_key=api_key,
                language=args.language,
                num_speakers=args.num_speakers,
                verbose=False,
                audio_track=args.audio_track,
                force=args.force,
                local_options=local_options,
            ): v
            for v in pending
        }
        for fut in as_completed(futures):
            v = futures[fut]
            try:
                out = fut.result()
                print(f"  + {v.stem}  →  {out.name}")
            except Exception as e:
                errors.append((v, str(e)))
                print(f"  x {v.stem}  FAILED: {e}")

    dt = time.time() - t0
    print(f"\ndone in {dt:.1f}s")
    if errors:
        print(f"{len(errors)} failures:")
        for v, msg in errors:
            print(f"  {v.name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
