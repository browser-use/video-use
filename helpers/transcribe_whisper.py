"""Transcribe a video with local OpenAI Whisper (word-level timestamps).

Extracts mono 16kHz audio via ffmpeg, runs Whisper locally, writes output
to <edit_dir>/transcripts/<video_stem>.json in the same format as the
ElevenLabs Scribe transcribe.py helper so the rest of the pipeline works
unchanged.

Cached: if the output file already exists, transcription is skipped.

Usage:
    python helpers/transcribe_whisper.py <video_path>
    python helpers/transcribe_whisper.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe_whisper.py <video_path> --model medium
    python helpers/transcribe_whisper.py <video_path> --language en

Models (speed vs accuracy tradeoff):
    tiny   — fastest, least accurate
    base   — good balance for English
    small  — better accuracy, still fast
    medium — recommended for production (default)
    large  — most accurate, slow
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def extract_audio(video_path: Path, wav_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path),
            "-ar", "16000", "-ac", "1", "-f", "wav", str(wav_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def transcribe(video_path: Path, edit_dir: Path, model_name: str, language: str | None) -> Path:
    import whisper

    out_dir = edit_dir / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{video_path.stem}.json"

    if out_file.exists():
        print(f"Cached transcript found: {out_file}")
        return out_file

    print(f"Loading Whisper model '{model_name}' (first run downloads ~{_model_size(model_name)})...")
    model = whisper.load_model(model_name)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)

    print("Extracting audio...")
    extract_audio(video_path, wav_path)

    print("Transcribing (this may take a minute)...")
    opts: dict = {"word_timestamps": True, "verbose": False}
    if language:
        opts["language"] = language

    result = model.transcribe(str(wav_path), **opts)
    wav_path.unlink(missing_ok=True)

    # Convert to the same shape as ElevenLabs Scribe output so the
    # rest of the pipeline (pack_transcripts.py, render.py) works unchanged.
    words = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            words.append({
                "text": w["word"].strip(),
                "start": round(w["start"], 3),
                "end": round(w["end"], 3),
                "type": "word",
                "speaker_id": "S0",
            })

    payload = {
        "language_code": result.get("language", language or "en"),
        "text": result["text"].strip(),
        "words": words,
        # Scribe-compatible envelope fields
        "alignment": {"words": words},
    }

    out_file.write_text(json.dumps(payload, indent=2))
    print(f"Transcript saved: {out_file}  ({len(words)} words)")
    return out_file


def _model_size(name: str) -> str:
    sizes = {"tiny": "75 MB", "base": "140 MB", "small": "460 MB",
             "medium": "1.5 GB", "large": "3 GB"}
    return sizes.get(name, "unknown size")


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcribe video with local Whisper.")
    parser.add_argument("video", help="Path to video file")
    parser.add_argument("--edit-dir", help="Output directory (default: <video_dir>/edit)")
    parser.add_argument("--model", default="medium", help="Whisper model (default: medium)")
    parser.add_argument("--language", default=None, help="Language code e.g. 'en'")
    args = parser.parse_args()

    video_path = Path(args.video).resolve()
    if not video_path.exists():
        print(f"Error: file not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    edit_dir = Path(args.edit_dir).resolve() if args.edit_dir else video_path.parent / "edit"
    transcribe(video_path, edit_dir, args.model, args.language)


if __name__ == "__main__":
    main()
