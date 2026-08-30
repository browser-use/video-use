"""Transcribe a video with Alibaba DashScope Paraformer-v2 (realtime, file mode).

Extracts mono 16kHz PCM audio via ffmpeg, streams it to DashScope's
paraformer-realtime-v2 model via the official `dashscope` SDK, and
writes a Scribe-compatible JSON transcript so the downstream
recommend_edit_plan helper keeps working without changes.

Output schema (intentionally Scribe-shaped):
    {
      "language_code": "auto" | "<lang>",
      "_source": "dashscope-paraformer-realtime-v2",
      "words": [
        {"text": "你好", "start": 1.234, "end": 1.567, "type": "word"},
        ...
      ]
    }

Tradeoffs vs the previous ElevenLabs Scribe integration:
  - No speaker diarization — paraformer does not segment speakers,
    so `speaker_id` is omitted from every word record.
  - No audio events — Scribe's "(laughter)" / "(applause)" entries
    with `"type": "audio_event"` are simply absent.
  - The `--num-speakers` flag is accepted by transcribe_one for
    backward compatibility with transcribe_batch but ignored.

Cached: if the output transcript already exists, the API call is skipped.

API key:
    DASHSCOPE_API_KEY in <repo>/.env or in the environment.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --language zh
    python helpers/transcribe.py <video_path> --edit-dir /custom/edit
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


DASHSCOPE_MODEL = "paraformer-realtime-v2"
ENV_VAR = "DASHSCOPE_API_KEY"


def load_api_key() -> str:
    """Read DASHSCOPE_API_KEY from <repo>/.env, ./.env, or the environment."""
    for candidate in [Path(__file__).resolve().parent.parent / ".env", Path(".env")]:
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == ENV_VAR:
                    return v.strip().strip('"').strip("'")
    v = os.environ.get(ENV_VAR, "")
    if not v:
        sys.exit(
            f"{ENV_VAR} not found in .env or environment. "
            f"Generate one at https://dashscope.console.aliyun.com/ "
            f"and put `{ENV_VAR}=...` in <repo>/.env."
        )
    return v


def extract_audio(video_path: Path, dest: Path) -> None:
    """Extract mono 16kHz PCM WAV — the format paraformer-v2 expects."""
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _convert_dashscope_to_scribe(
    sentences: list[dict],
    language_hint: str | None,
) -> dict:
    """Flatten DashScope sentence/word structure into Scribe-compatible shape.

    DashScope returns:
        sentence: [
          {begin_time, end_time, text,
           words: [{begin_time, end_time, text, punctuation}, ...]}
        ]

    recommend_edit_plan.load_transcript_words wants a flat words[] with
    seconds-based start/end and a 'word' type marker. Convert here so the
    consumer stays Scribe-shaped and we don't need to touch recommender code.

    Punctuation tokens that DashScope splits onto their own word entry are
    folded into the preceding word's text — closer to how Scribe formatted
    them. Empty / whitespace-only text entries are dropped.
    """
    words: list[dict] = []
    for sent in sentences or []:
        for w in (sent.get("words") or []):
            text = (w.get("text") or "").strip()
            if not text:
                continue
            punct = (w.get("punctuation") or "").strip()
            try:
                start_ms = float(w.get("begin_time") or 0)
                end_ms = float(w.get("end_time") or 0)
            except (TypeError, ValueError):
                continue
            words.append({
                "text": text + punct,
                "start": start_ms / 1000.0,
                "end": end_ms / 1000.0,
                "type": "word",
            })
    return {
        "language_code": language_hint or "auto",
        "_source": f"dashscope-{DASHSCOPE_MODEL}",
        "words": words,
    }


def call_dashscope(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
) -> dict:
    """Call paraformer-realtime-v2 in file mode. Returns Scribe-shaped dict.

    The dashscope SDK handles WebSocket framing internally when given a
    local file path — no manual chunking required. Defensive against
    minor SDK shape variations: tolerates both `output.sentence` and
    `output.sentences` (the docs and the wire format have shifted).
    """
    try:
        import dashscope
        from dashscope.audio.asr import Recognition
    except ImportError:
        raise SystemExit(
            "dashscope package not installed. Install with:\n"
            "  pip install dashscope\n"
            "(or `pip install -e .` from the repo root once dashscope is in "
            "your project deps)."
        )

    dashscope.api_key = api_key
    # Pin to the Mainland China endpoints explicitly. Both URLs are the SDK
    # defaults, but stale DASHSCOPE_HTTP_BASE_URL / DASHSCOPE_WEBSOCKET_BASE_URL
    # env vars (left over from an international account) would otherwise route
    # us to the wrong region and produce a misleading 401 from the intl host
    # even when the key is valid on the domestic side.
    dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"
    dashscope.base_websocket_api_url = (
        "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    )

    language_hints = [language] if language else None

    recognition = Recognition(
        model=DASHSCOPE_MODEL,
        format="wav",
        sample_rate=16000,
        language_hints=language_hints,
        callback=None,
    )
    response = recognition.call(file=str(audio_path))

    status = getattr(response, "status_code", None)
    if status != 200:
        msg = getattr(response, "message", None) or str(response)
        request_id = getattr(response, "request_id", "")
        raise RuntimeError(
            f"DashScope {DASHSCOPE_MODEL} returned status={status} "
            f"request_id={request_id}: {msg}"
        )

    output = getattr(response, "output", None) or {}
    # Both shapes seen in the wild; honour either.
    sentences = output.get("sentence") or output.get("sentences") or []
    return _convert_dashscope_to_scribe(sentences, language)


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
    verbose: bool = True,
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    `num_speakers` is accepted for backward compatibility with the previous
    ElevenLabs Scribe interface (and with transcribe_batch.py's call site)
    but is ignored — paraformer does not perform speaker diarization. A
    one-line note is printed when a non-None value is supplied in verbose mode.

    Cached: returns existing path immediately if the transcript already exists.
    """
    if num_speakers is not None and verbose:
        print(
            f"  (note: --num-speakers={num_speakers} ignored — DashScope "
            f"{DASHSCOPE_MODEL} has no speaker diarization)"
        )

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
            print(
                f"  streaming {video.stem}.wav ({size_mb:.1f} MB) "
                f"to DashScope {DASHSCOPE_MODEL}",
                flush=True,
            )
        payload = call_dashscope(audio, api_key, language)

    # ensure_ascii=False so CJK characters are stored as-is (smaller file +
    # human-readable when inspecting transcripts).
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        words_count = len(payload.get("words", []))
        print(f"  saved: {out_path.name} ({kb:.1f} KB, {words_count} words) in {dt:.1f}s")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(
        description=f"Transcribe a video with DashScope {DASHSCOPE_MODEL}"
    )
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
        help="Language hint (e.g. 'zh', 'en', 'ja'). Omit to auto-detect.",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    api_key = load_api_key()

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        api_key=api_key,
        language=args.language,
    )


if __name__ == "__main__":
    main()
