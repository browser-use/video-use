"""Batch-transcribe every video in a directory with local whisper.cpp.

Walks <videos_dir> for common video extensions, runs the local whisper.cpp
transcriber on each, writes transcripts to <videos_dir>/edit/transcripts/<name>.json.

Cached per-file: any source that already has a transcript is skipped.

Usage:
    python helpers/transcribe_batch_local.py <videos_dir>
    python helpers/transcribe_batch_local.py <videos_dir> --workers 2
    python helpers/transcribe_batch_local.py <videos_dir> --language pt
    python helpers/transcribe_batch_local.py <videos_dir> --edit-dir /custom/edit
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from transcribe_local import transcribe_one, transcript_path

VIDEO_EXTS = {".mp4", ".MP4", ".mov", ".MOV", ".mkv", ".MKV", ".avi", ".AVI", ".m4v"}


def find_videos(videos_dir: Path) -> list[Path]:
    return sorted(p for p in videos_dir.iterdir() if p.is_file() and p.suffix in VIDEO_EXTS)


def main() -> None:
    ap = argparse.ArgumentParser(description="Parallel batch transcription (local whisper.cpp)")
    ap.add_argument("videos_dir", type=Path, help="Directory containing source videos")
    ap.add_argument("--edit-dir", type=Path, default=None, help="Edit output directory (default: <videos_dir>/edit)")
    ap.add_argument("--workers", type=int, default=2, help="Parallel workers (default: 2; whisper.cpp is CPU-bound)")
    ap.add_argument("--language", type=str, default="auto", help="Language code or 'auto'")
    ap.add_argument("--model", type=str, default=None, help="Path to ggml whisper model")
    ap.add_argument("--audio-track", type=int, default=0, help="Zero-based audio track")
    ap.add_argument("--force", action="store_true", help="Re-transcribe cached files")
    args = ap.parse_args()

    videos_dir = args.videos_dir.resolve()
    if not videos_dir.is_dir():
        sys.exit(f"not a directory: {videos_dir}")

    edit_dir = (args.edit_dir or (videos_dir / "edit")).resolve()
    (edit_dir / "transcripts").mkdir(parents=True, exist_ok=True)

    videos = find_videos(videos_dir)
    if not videos:
        sys.exit(f"no videos found in {videos_dir}")

    already_cached = [v for v in videos if transcript_path(edit_dir, v, args.audio_track).exists() and not args.force]
    pending = [v for v in videos if v not in already_cached]

    print(f"found {len(videos)} videos ({len(already_cached)} cached, {len(pending)} to transcribe)")
    if not pending:
        print("nothing to do")
        return

    print(f"transcribing {len(pending)} files with {args.workers} parallel workers (local whisper.cpp)")
    t0 = time.time()

    errors: list[tuple[Path, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                transcribe_one,
                video=v,
                edit_dir=edit_dir,
                language=args.language,
                model=args.model,
                audio_track=args.audio_track,
                verbose=False,
                force=args.force,
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
PY