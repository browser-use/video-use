"""Transcribe a video with FunASR (local, no API key required).

Replaces the original ElevenLabs Scribe backend with FunASR (ModelScope's
open-source ASR toolkit). Runs fully on-device, supports Chinese + English
mixed speech, and emits the same JSON schema the rest of the pipeline
expects:

    {
      "language_code": "zh" | "en" | ...,
      "words": [
        {"type": "word",        "text": "...", "start": s, "end": s, "speaker_id": "speaker_0"},
        {"type": "spacing",     "text": " ",   "start": s, "end": s},
        {"type": "audio_event", "text": "(laughter)", "start": s, "end": s, "speaker_id": "speaker_0"}
      ]
    }

Pipeline: ffmpeg extracts mono 16kHz WAV → FunASR AutoModel runs ASR
(paraformer-zh) + VAD (fsmn-vad) + punctuation (ct-punc) + speaker
diarization (cam++) → character-level timestamps are flattened into the
word array above. Silence gaps detected by VAD become `spacing` entries.

Cached: if the output file already exists, transcription is skipped.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe.py <video_path> --language zh
    python helpers/transcribe.py <video_path> --num-speakers 2
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# FunASR is heavy; import lazily so --help / imports elsewhere stay fast.
_MODEL_CACHE: dict = {}

# Chinese + common punctuation to strip when aligning chars with timestamps.
_PUNCT = set("，。！？、；：“”‘’（）《》【】「」『』〈〉…—·,.!?;:\"'()[]<>~ \t\n")


def extract_audio(video_path: Path, dest: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _get_model(language: str | None = None):
    """Load and cache the FunASR AutoModel.

    Defaults to a Chinese-optimised stack (paraformer-zh). For English-only
    input, pass language='en' → uses the multilingual Whisper bundle.
    """
    key = (language or "zh").lower()
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]

    from funasr import AutoModel  # lazy import; pulls in torch the first time

    if key.startswith("en"):
        # English / multilingual path. Whisper handles non-Chinese well.
        model = AutoModel(
            model="iic/Whisper-large-v3",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            spk_model="cam++",
            disable_update=True,
        )
    else:
        # Chinese (or mixed) path. Paraformer is faster and stronger on zh.
        model = AutoModel(
            model="paraformer-zh",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            spk_model="cam++",
            disable_update=True,
        )

    _MODEL_CACHE[key] = model
    return model


def _is_punct(ch: str) -> bool:
    return ch in _PUNCT


def _strip_chars(text: str) -> list[str]:
    """Flatten sentence text into the list of speakable characters that
    should line up 1:1 with the per-char timestamps returned by Paraformer.

    Punctuation, whitespace, and newlines are removed — they have no
    timestamp in the paraformer output.
    """
    return [c for c in text if not _is_punct(c)]


def _sentence_to_words(sent: dict, prev_end: float | None) -> tuple[list[dict], float]:
    """Convert one FunASR sentence_info entry into word/spacing dicts."""
    out: list[dict] = []
    start_s = float(sent.get("start", 0)) / 1000.0
    end_s = float(sent.get("end", 0)) / 1000.0
    spk = sent.get("spk", 0)
    speaker_id = f"speaker_{int(spk)}"
    text = sent.get("text", "") or ""
    ts = sent.get("timestamp") or []

    if prev_end is not None and start_s > prev_end + 0.01:
        out.append({
            "type": "spacing",
            "text": " ",
            "start": round(prev_end, 3),
            "end": round(start_s, 3),
        })

    chars = _strip_chars(text)

    if ts and len(chars) == len(ts):
        # Happy path: per-character timestamps align exactly.
        for ch, pair in zip(chars, ts):
            s_ms, e_ms = pair
            out.append({
                "type": "word",
                "text": ch,
                "start": round(float(s_ms) / 1000.0, 3),
                "end": round(float(e_ms) / 1000.0, 3),
                "speaker_id": speaker_id,
            })
    else:
        # Fallback: emit whole sentence as one entry. Keeps the pipeline
        # working even when punctuation or tagging breaks alignment.
        out.append({
            "type": "word",
            "text": text.strip(),
            "start": round(start_s, 3),
            "end": round(end_s, 3),
            "speaker_id": speaker_id,
        })

    return out, end_s


def call_funasr(
    audio_path: Path,
    language: str | None = None,
    num_speakers: int | None = None,
) -> dict:
    """Run FunASR on a mono 16kHz WAV file and return an ElevenLabs-shaped dict."""
    model = _get_model(language)

    kwargs: dict = {
        "input": str(audio_path),
        "batch_size_s": 300,
        "return_spk_res": True,
    }
    # FunASR accepts a hint for expected speaker count on CAM++.
    if num_speakers:
        kwargs["preset_spk_num"] = int(num_speakers)

    res = model.generate(**kwargs)

    if not res:
        return {"language_code": language or "auto", "words": []}

    record = res[0] if isinstance(res, list) else res
    sentences = record.get("sentence_info") or []

    words: list[dict] = []
    prev_end: float | None = None
    for sent in sentences:
        chunk, prev_end = _sentence_to_words(sent, prev_end)
        words.extend(chunk)

    # Safety net: if diarization+VAD produced nothing, fall back to the
    # flat text + word-level timestamps directly on the record.
    if not words:
        flat_ts = record.get("timestamp") or []
        flat_text = record.get("text", "") or ""
        chars = _strip_chars(flat_text)
        if flat_ts and len(chars) == len(flat_ts):
            for ch, pair in zip(chars, flat_ts):
                s_ms, e_ms = pair
                words.append({
                    "type": "word",
                    "text": ch,
                    "start": round(float(s_ms) / 1000.0, 3),
                    "end": round(float(e_ms) / 1000.0, 3),
                    "speaker_id": "speaker_0",
                })

    return {
        "language_code": language or record.get("language") or "auto",
        "text": record.get("text", ""),
        "words": words,
    }


def transcribe_one(
    video: Path,
    edit_dir: Path,
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

    if verbose:
        print(f"  extracting audio from {video.name}", flush=True)

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.wav"
        extract_audio(video, audio)
        size_mb = audio.stat().st_size / (1024 * 1024)
        if verbose:
            print(f"  transcribing {video.stem}.wav ({size_mb:.1f} MB) with FunASR", flush=True)
        payload = call_funasr(audio, language, num_speakers)

    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        if isinstance(payload, dict) and "words" in payload:
            print(f"    words: {len(payload['words'])}")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video with FunASR (local)")
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
        help="Optional ISO language code ('zh' or 'en'). Omit for Chinese default.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Optional expected number of speakers. Improves diarization accuracy.",
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
        num_speakers=args.num_speakers,
    )


if __name__ == "__main__":
    main()
