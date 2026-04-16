"""Batch-transcribe every video in a directory.

Supports both WhisperX (local) and ElevenLabs Scribe (cloud) backends.
  - ElevenLabs: 4-worker parallel API calls (default if ELEVENLABS_API_KEY set).
  - WhisperX: model loaded once, files processed sequentially (GPU memory shared).

Cached per-file: any source that already has a transcript is skipped.

Usage:
    python helpers/transcribe_batch.py <videos_dir>
    python helpers/transcribe_batch.py <videos_dir> --backend whisperx
    python helpers/transcribe_batch.py <videos_dir> --backend whisperx --model base
    python helpers/transcribe_batch.py <videos_dir> --backend elevenlabs --workers 4
    python helpers/transcribe_batch.py <videos_dir> --num-speakers 2
    python helpers/transcribe_batch.py <videos_dir> --edit-dir /custom/edit
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from transcribe import detect_backend, load_whisperx_model, transcribe_one, _load_env_var


VIDEO_EXTS = {".mp4", ".MP4", ".mov", ".MOV", ".mkv", ".MKV", ".avi", ".AVI", ".m4v"}


def find_videos(videos_dir: Path) -> list[Path]:
    videos = sorted(
        p for p in videos_dir.iterdir()
        if p.is_file() and p.suffix in VIDEO_EXTS
    )
    return videos


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch transcription of a videos directory")
    ap.add_argument("videos_dir", type=Path, help="Directory containing source videos")
    ap.add_argument(
        "--edit-dir", type=Path, default=None,
        help="Edit output directory (default: <videos_dir>/edit)",
    )
    ap.add_argument(
        "--workers", type=int, default=4,
        help="Parallel workers for ElevenLabs backend (default: 4). Ignored for whisperx.",
    )
    ap.add_argument(
        "--language", type=str, default=None,
        help="Optional ISO language code. Omit to auto-detect per file.",
    )
    ap.add_argument(
        "--num-speakers", type=int, default=None,
        help="Optional number of speakers. Improves diarization when known.",
    )
    ap.add_argument(
        "--backend", type=str, default=None, choices=["whisperx", "elevenlabs"],
        help="Transcription backend (default: elevenlabs if API key set, else whisperx).",
    )
    ap.add_argument(
        "--model", type=str, default=None,
        help="WhisperX model name (default: large-v3 on GPU, small on CPU).",
    )
    args = ap.parse_args()

    videos_dir = args.videos_dir.resolve()
    if not videos_dir.is_dir():
        sys.exit(f"not a directory: {videos_dir}")

    edit_dir = (args.edit_dir or (videos_dir / "edit")).resolve()
    (edit_dir / "transcripts").mkdir(parents=True, exist_ok=True)

    videos = find_videos(videos_dir)
    if not videos:
        sys.exit(f"no videos found in {videos_dir}")

    already_cached = [v for v in videos if (edit_dir / "transcripts" / f"{v.stem}.json").exists()]
    pending = [v for v in videos if v not in already_cached]

    backend = args.backend or detect_backend()
    print(f"found {len(videos)} videos ({len(already_cached)} cached, {len(pending)} to transcribe)")
    print(f"backend: {backend}")
    if not pending:
        print("nothing to do")
        return

    t0 = time.time()
    errors: list[tuple[Path, str]] = []

    if backend == "elevenlabs":
        api_key = _load_env_var("ELEVENLABS_API_KEY")
        if not api_key:
            sys.exit("ELEVENLABS_API_KEY not found in .env or environment")

        print(f"transcribing {len(pending)} files with {args.workers} parallel workers")
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    transcribe_one,
                    video=v,
                    edit_dir=edit_dir,
                    backend="elevenlabs",
                    api_key=api_key,
                    language=args.language,
                    num_speakers=args.num_speakers,
                    verbose=False,
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
    else:
        print("loading WhisperX model…")
        model = load_whisperx_model(model_name=args.model)

        print(f"transcribing {len(pending)} files sequentially")
        for v in pending:
            try:
                out = transcribe_one(
                    video=v,
                    edit_dir=edit_dir,
                    backend="whisperx",
                    whisperx_model=model,
                    language=args.language,
                    num_speakers=args.num_speakers,
                )
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
