"""Transcribe a video — WhisperX (local, default) or ElevenLabs Scribe (cloud).

Backend selection:
  - If ELEVENLABS_API_KEY is set → uses ElevenLabs Scribe (cloud, fast, diarization + audio events).
  - Otherwise → uses WhisperX (local, no API key needed).

Both backends produce the same JSON format (top-level ``words`` list with
``word``, ``spacing``, and optionally ``audio_event`` entries), so
``pack_transcripts.py`` works unchanged regardless of backend.

Cached: if the output file already exists, transcription is skipped.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe.py <video_path> --language en
    python helpers/transcribe.py <video_path> --num-speakers 2
    python helpers/transcribe.py <video_path> --backend whisperx
    python helpers/transcribe.py <video_path> --backend elevenlabs
    python helpers/transcribe.py <video_path> --model base  # WhisperX model size
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


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

def _load_env_var(name: str) -> str:
    """Try .env files then the environment. Returns empty string if missing."""
    for candidate in [Path(__file__).resolve().parent.parent / ".env", Path(".env")]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == name:
                    return v.strip().strip('"').strip("'")
    return os.environ.get(name, "")


def detect_backend() -> str:
    """Return 'elevenlabs' if an API key is available, else 'whisperx'."""
    if _load_env_var("ELEVENLABS_API_KEY"):
        return "elevenlabs"
    return "whisperx"


def extract_audio(video_path: Path, dest: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------------------
# ElevenLabs Scribe backend
# ---------------------------------------------------------------------------

SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"


def _call_scribe(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
) -> dict:
    import requests

    data: dict[str, str] = {
        "model_id": "scribe_v1",
        "diarize": "true",
        "tag_audio_events": "true",
        "timestamps_granularity": "word",
    }
    if language:
        data["language_code"] = language
    if num_speakers:
        data["num_speakers"] = str(num_speakers)

    with open(audio_path, "rb") as f:
        resp = requests.post(
            SCRIBE_URL,
            headers={"xi-api-key": api_key},
            files={"file": (audio_path.name, f, "audio/wav")},
            data=data,
            timeout=1800,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Scribe returned {resp.status_code}: {resp.text[:500]}")

    return resp.json()


# ---------------------------------------------------------------------------
# WhisperX backend
# ---------------------------------------------------------------------------

def _get_device() -> tuple[str, str]:
    """Return (device, compute_type) for WhisperX."""
    import torch

    if torch.cuda.is_available():
        return "cuda", "float16"
    return "cpu", "int8"


def _default_whisperx_model(device: str) -> str:
    """Pick model size: large-v3 on GPU, small on CPU."""
    return "large-v3" if device == "cuda" else "small"


def load_whisperx_model(
    model_name: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
):
    """Load the WhisperX model. Reuse across files in batch mode."""
    import whisperx

    if device is None or compute_type is None:
        device, compute_type = _get_device()
    if model_name is None:
        model_name = _default_whisperx_model(device)
    return whisperx.load_model(model_name, device, compute_type=compute_type)


def _convert_whisperx_to_scribe_format(result: dict) -> dict:
    """Convert WhisperX output to the same JSON format as ElevenLabs Scribe.

    The format has a top-level ``words`` list with entries of type ``word``
    (with ``text``, ``start``, ``end``, ``speaker_id``) and ``spacing``
    (gap between words).  ``pack_transcripts.py`` reads this format.
    """
    words: list[dict] = []
    raw_words = result.get("word_segments", [])

    prev_end: float | None = None
    for w in raw_words:
        start = w.get("start")
        end = w.get("end")
        text = w.get("word", "").strip()
        if start is None or end is None or not text:
            continue

        if prev_end is not None and start > prev_end:
            words.append({"type": "spacing", "start": prev_end, "end": start})

        speaker = w.get("speaker")
        speaker_id = None
        if speaker and speaker.startswith("SPEAKER_"):
            speaker_id = f"speaker_{int(speaker[len('SPEAKER_'):])}"

        entry: dict = {
            "type": "word",
            "text": text,
            "start": round(start, 3),
            "end": round(end, 3),
        }
        if speaker_id is not None:
            entry["speaker_id"] = speaker_id
        words.append(entry)
        prev_end = end

    return {"words": words}


def _run_whisperx(
    audio_path: Path,
    model=None,
    language: str | None = None,
    num_speakers: int | None = None,
) -> dict:
    """Run WhisperX transcription + alignment + optional diarization."""
    import whisperx

    device, compute_type = _get_device()

    if model is None:
        model = load_whisperx_model(device=device, compute_type=compute_type)

    audio = whisperx.load_audio(str(audio_path))

    # 1. Transcribe
    transcribe_kwargs: dict = {"batch_size": 16}
    if language:
        transcribe_kwargs["language"] = language
    result = model.transcribe(audio, **transcribe_kwargs)

    detected_lang = result.get("language", language or "en")

    # 2. Align for word-level timestamps
    align_model, metadata = whisperx.load_align_model(
        language_code=detected_lang, device=device,
    )
    result = whisperx.align(
        result["segments"], align_model, metadata, audio, device,
        return_char_alignments=False,
    )

    # 3. Diarize (optional — needs HF_TOKEN for pyannote)
    hf_token = os.environ.get("HF_TOKEN", "")
    if hf_token:
        diarize_model = whisperx.DiarizationPipeline(
            use_auth_token=hf_token, device=device,
        )
        diarize_kwargs: dict = {}
        if num_speakers:
            diarize_kwargs["num_speakers"] = num_speakers
        diarize_segments = diarize_model(str(audio_path), **diarize_kwargs)
        result = whisperx.assign_word_speakers(diarize_segments, result)

    return _convert_whisperx_to_scribe_format(result)


# ---------------------------------------------------------------------------
# Unified interface
# ---------------------------------------------------------------------------

def transcribe_one(
    video: Path,
    edit_dir: Path,
    *,
    backend: str | None = None,
    api_key: str | None = None,
    whisperx_model=None,
    language: str | None = None,
    num_speakers: int | None = None,
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

    if backend is None:
        backend = detect_backend()

    if verbose:
        print(f"  extracting audio from {video.name} (backend: {backend})", flush=True)

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.wav"
        extract_audio(video, audio)
        size_mb = audio.stat().st_size / (1024 * 1024)
        if verbose:
            action = "uploading" if backend == "elevenlabs" else "transcribing"
            print(f"  {action} {video.stem}.wav ({size_mb:.1f} MB)", flush=True)

        if backend == "elevenlabs":
            if not api_key:
                api_key = _load_env_var("ELEVENLABS_API_KEY")
            if not api_key:
                sys.exit("ELEVENLABS_API_KEY not found in .env or environment")
            payload = _call_scribe(audio, api_key, language, num_speakers)
        else:
            payload = _run_whisperx(audio, whisperx_model, language, num_speakers)

    out_path.write_text(json.dumps(payload, indent=2))
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        if isinstance(payload, dict) and "words" in payload:
            print(f"    words: {len(payload['words'])}")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Transcribe a video (WhisperX local or ElevenLabs Scribe cloud)",
    )
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument(
        "--edit-dir", type=Path, default=None,
        help="Edit output directory (default: <video_parent>/edit)",
    )
    ap.add_argument(
        "--language", type=str, default=None,
        help="Optional ISO language code (e.g., 'en'). Omit to auto-detect.",
    )
    ap.add_argument(
        "--num-speakers", type=int, default=None,
        help="Optional number of speakers when known. Improves diarization accuracy.",
    )
    ap.add_argument(
        "--backend", type=str, default=None, choices=["whisperx", "elevenlabs"],
        help="Transcription backend (default: elevenlabs if API key set, else whisperx).",
    )
    ap.add_argument(
        "--model", type=str, default=None,
        help="WhisperX model name (default: large-v3 on GPU, small on CPU). Ignored for elevenlabs.",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    backend = args.backend or detect_backend()

    whisperx_model = None
    if backend == "whisperx" and args.model:
        whisperx_model = load_whisperx_model(model_name=args.model)

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        backend=backend,
        whisperx_model=whisperx_model,
        language=args.language,
        num_speakers=args.num_speakers,
    )


if __name__ == "__main__":
    main()
