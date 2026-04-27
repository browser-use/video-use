"""Transcribe a video with OpenAI Whisper (local, no API key required).

Extracts mono 16kHz audio via ffmpeg, runs Whisper with word-level timestamps,
writes the full response to <edit_dir>/transcripts/<video_stem>.json in the
same schema that the rest of video-use expects from ElevenLabs Scribe:

    {"words": [{"type": "word", "text": str, "start": float, "end": float,
                "speaker_id": "S0"}, ...]}

Cached: if the output file already exists, transcription is skipped.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe.py <video_path> --language en
    python helpers/transcribe.py <video_path> --model medium
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Allow callers to override the ffmpeg binary path via env var
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", str(Path.home() / ".local/bin/ffmpeg"))


def extract_audio(video_path: Path, dest: Path) -> None:
    cmd = [
        FFMPEG_BIN, "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def call_whisper(
    audio_path: Path,
    language: str | None = None,
    model_name: str = "base",
) -> dict:
    import whisper  # imported here so the module is usable without whisper installed globally

    model = whisper.load_model(model_name)
    result = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        verbose=False,
    )

    words = []
    for segment in result.get("segments", []):
        for w in segment.get("words", []):
            text = w["word"].strip()
            if not text:
                continue
            words.append({
                "type": "word",
                "text": text,
                "start": round(w["start"], 3),
                "end": round(w["end"], 3),
                "speaker_id": "S0",
            })

    return {"words": words, "text": result.get("text", "").strip()}


def transcribe_one(
    video: Path,
    edit_dir: Path,
    language: str | None = None,
    model_name: str = "base",
    verbose: bool = True,
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached: returns existing path immediately if the transcript already exists.
    """
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcripts_dir / f"{video.stem}.json"

    if out_path.exists():
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.wav"
        if verbose:
            print(f"  extracting audio from {video.name}", flush=True)
        extract_audio(video, audio)
        size_mb = audio.stat().st_size / (1024 * 1024)
        if verbose:
            print(f"  transcribing {video.stem}.wav ({size_mb:.1f} MB) with whisper:{model_name}", flush=True)
        payload = call_whisper(audio, language=language, model_name=model_name)

    out_path.write_text(json.dumps(payload, indent=2))
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        print(f"    words: {len(payload['words'])}")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video with OpenAI Whisper")
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument(
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <video_parent>/edit)",
    )
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help="Optional ISO language code (e.g., 'en'). Omit to auto-detect.",
    )
    ap.add_argument(
        "--model",
        type=str,
        default="base",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size (default: base). Larger = more accurate but slower.",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        language=args.language,
        model_name=args.model,
    )


if __name__ == "__main__":
    main()
