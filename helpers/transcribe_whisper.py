"""Transcribe a video with local faster-whisper.

Drop-in alternative to `transcribe.py` (ElevenLabs Scribe) for offline,
no-API-cost use. Produces the same JSON shape that `pack_transcripts.py`
and downstream helpers expect:

    {
      "text": "<full transcript>",
      "language_code": "en",
      "words": [
        {"type": "word",    "text": "hello", "start": 0.12, "end": 0.38, "speaker_id": "speaker_0"},
        {"type": "spacing", "text": " ",     "start": 0.38, "end": 0.41, "speaker_id": null},
        ...
      ]
    }

Caveats vs Scribe:
- No diarization. All words get `speaker_id = "speaker_0"`. If --num-speakers
  is passed, it's accepted but ignored (parity with the Scribe CLI).
- No audio-event tagging (laughter, applause, etc.) — only "word" and "spacing".
- Filler words ("um", "uh") are transcribed as regular words; downstream
  filler detection still works because it operates on text.

Usage:
    python helpers/transcribe_whisper.py <video_path>
    python helpers/transcribe_whisper.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe_whisper.py <video_path> --language en
    python helpers/transcribe_whisper.py <video_path> --model large-v3
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from faster_whisper import WhisperModel


DEFAULT_MODEL = "large-v3"
DEFAULT_COMPUTE_TYPE = "int8"


def extract_audio(video_path: Path, dest: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_whisper(
    audio_path: Path,
    model_name: str,
    compute_type: str,
    language: str | None,
) -> tuple[list[dict], str, str]:
    model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
    )

    words_out: list[dict] = []
    full_text_parts: list[str] = []
    prev_end: float | None = None

    for seg in segments_iter:
        for w in (seg.words or []):
            if w.start is None or w.end is None:
                continue
            text = w.word
            stripped = text.strip()

            if prev_end is not None and w.start > prev_end:
                words_out.append({
                    "type": "spacing",
                    "text": " ",
                    "start": prev_end,
                    "end": w.start,
                    "speaker_id": None,
                })

            words_out.append({
                "type": "word",
                "text": stripped if stripped else text,
                "start": w.start,
                "end": w.end,
                "speaker_id": "speaker_0",
            })
            full_text_parts.append(stripped if stripped else text)
            prev_end = w.end

    return words_out, " ".join(full_text_parts).strip(), info.language or (language or "en")


def transcribe_one(
    video: Path,
    edit_dir: Path,
    model_name: str,
    compute_type: str,
    language: str | None = None,
    verbose: bool = True,
) -> Path:
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcripts_dir / f"{video.stem}.json"

    if out_path.exists():
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    if verbose:
        print(f"  extracting audio from {video.name}", flush=True)

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.wav"
        extract_audio(video, audio)
        if verbose:
            size_mb = audio.stat().st_size / (1024 * 1024)
            print(f"  transcribing {video.stem}.wav ({size_mb:.1f} MB) with {model_name} [{compute_type}]", flush=True)
        words, full_text, detected_lang = run_whisper(audio, model_name, compute_type, language)

    payload = {
        "language_code": detected_lang,
        "text": full_text,
        "words": words,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        word_count = sum(1 for w in words if w["type"] == "word")
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        print(f"    words: {word_count}  lang: {detected_lang}")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video with local faster-whisper")
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument("--edit-dir", type=Path, default=None,
                    help="Edit output directory (default: <video_parent>/edit)")
    ap.add_argument("--language", type=str, default=None,
                    help="Optional ISO language code (e.g., 'en'). Omit to auto-detect.")
    ap.add_argument("--num-speakers", type=int, default=None,
                    help="Accepted for CLI parity with transcribe.py; ignored (no diarization).")
    ap.add_argument("--model", type=str, default=DEFAULT_MODEL,
                    help=f"faster-whisper model (default: {DEFAULT_MODEL}). "
                         "Options: tiny, base, small, medium, large-v2, large-v3, distil-large-v3.")
    ap.add_argument("--compute-type", type=str, default=DEFAULT_COMPUTE_TYPE,
                    help=f"ctranslate2 compute type (default: {DEFAULT_COMPUTE_TYPE}). "
                         "int8 is fastest on CPU; float16 on GPU.")
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        model_name=args.model,
        compute_type=args.compute_type,
        language=args.language,
    )


if __name__ == "__main__":
    main()
