"""Transcribe a video with Deepgram — drop-in alternative to Scribe.

Emits the SAME on-disk schema as `transcribe.py`, so `pack_transcripts.py`,
`render.py --build-subtitles`, and `transcribe_batch.py` consume the output
without changes. The whole pipeline only ever reads five fields per token:

    {"words": [{"type", "text", "start", "end", "speaker_id"}]}

Mapping from Deepgram's `results.channels[0].alternatives[0].words[]`:

    punctuated_word (fallback: word) -> text
    start / end                      -> passthrough
    speaker: 0                       -> speaker_id: "speaker_0"
    (no discriminator)               -> type: "word"

`filler_words=true` keeps "um"/"uh" (Hard Rule 8 — verbatim, never
normalized). `smart_format` is deliberately OFF: it rewrites numbers and
dates, which is exactly the normalization Rule 8 forbids.

Deepgram has no `spacing` token, so we synthesize one per inter-word gap
>= --spacing-threshold. `pack_transcripts.py` would break phrases correctly
without them (it also flushes on `start - prev_end`), but emitting them
keeps this a faithful drop-in for any consumer that reads them.

Usage:
    python helpers/transcribe_deepgram.py <video_path>
    python helpers/transcribe_deepgram.py <video_path> --model nova-3
    python helpers/transcribe_deepgram.py <video_path> --language en
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import requests

# Reuse the audio front-end so the two transcribers cannot drift apart.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from transcribe import (  # noqa: E402
    count_audio_tracks,
    extract_audio,
    peak_dbfs,
    transcript_path,
)

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"

# Stamped into every transcript this module writes. The on-disk path is shared
# with transcribe.py (downstream expects exactly one transcripts/<stem>.json per
# source), so the provider has to live in the file rather than the filename.
PROVIDER = "deepgram"


def load_api_key() -> str:
    """Same resolution order as transcribe.py: .env at repo root, then env."""
    for candidate in [Path(__file__).resolve().parent.parent / ".env", Path(".env")]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "DEEPGRAM_API_KEY":
                    return v.strip().strip('"').strip("'")
    v = os.environ.get("DEEPGRAM_API_KEY", "")
    if not v:
        sys.exit("DEEPGRAM_API_KEY not found in .env or environment")
    return v


def call_deepgram(
    audio_path: Path,
    api_key: str,
    model: str = "nova-3",
    language: str | None = None,
) -> dict:
    params: dict[str, str] = {
        "model": model,
        "diarize": "true",
        "punctuate": "true",
        "filler_words": "true",
    }
    if language:
        params["language"] = language
    else:
        # The CLI documents auto-detection when --language is omitted; without
        # this Deepgram just applies its default language instead of detecting.
        params["detect_language"] = "true"

    with open(audio_path, "rb") as f:
        resp = requests.post(
            DEEPGRAM_URL,
            headers={"Authorization": f"Token {api_key}", "Content-Type": "audio/wav"},
            params=params,
            data=f,
            timeout=1800,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Deepgram returned {resp.status_code}: {resp.text[:500]}")

    return resp.json()


def to_scribe_schema(dg: dict, spacing_threshold: float = 0.05) -> dict:
    """Convert a Deepgram response into the schema the pipeline consumes."""
    channels = (dg.get("results") or {}).get("channels") or []
    if not channels:
        raise RuntimeError("Deepgram response has no results.channels")
    alternatives = channels[0].get("alternatives") or []
    if not alternatives:
        raise RuntimeError("Deepgram response has no alternatives")

    dg_words = alternatives[0].get("words") or []

    words: list[dict] = []
    prev_end: float | None = None

    for w in dg_words:
        start = w.get("start")
        end = w.get("end")
        text = (w.get("punctuated_word") or w.get("word") or "").strip()
        if start is None or end is None or not text:
            continue

        # Synthesize the gap token Deepgram doesn't send.
        if prev_end is not None and start - prev_end >= spacing_threshold:
            words.append({
                "type": "spacing",
                "text": " ",
                "start": prev_end,
                "end": start,
            })

        entry: dict = {"type": "word", "text": text, "start": start, "end": end}

        speaker = w.get("speaker")
        if speaker is not None:
            # pack_transcripts.py strips a "speaker_" prefix to render "S0".
            entry["speaker_id"] = f"speaker_{speaker}"
        if w.get("confidence") is not None:
            entry["confidence"] = w["confidence"]

        words.append(entry)
        prev_end = end

    transcript_text = alternatives[0].get("transcript", "")
    # With detect_language=true the result lands on the channel, not on
    # `results` or `metadata` (verified against a live nova-3 response).
    channel = channels[0]
    detected = (
        channel.get("detected_language")
        or (dg.get("results") or {}).get("language")
        or (dg.get("metadata") or {}).get("language")
    )

    return {
        "text": transcript_text,
        "words": words,
        "language_code": detected,
        "_language_confidence": channel.get("language_confidence"),
        "_provider": PROVIDER,
        "_raw_metadata": dg.get("metadata") or {},
    }


def describe_provider(payload: dict) -> str:
    """Human-readable provider of an existing transcript.

    transcribe.py writes Scribe's response verbatim with no provider marker, so
    a missing `_provider` means Scribe.
    """
    provider = payload.get("_provider") or "elevenlabs-scribe"
    model = payload.get("_model")
    return f"{provider} ({model})" if model else str(provider)


def check_cached(out_path: Path, model: str, spacing_threshold: float,
                 language: str | None, force: bool, verbose: bool) -> bool:
    """Decide whether an existing transcript can be reused.

    The on-disk path is shared with transcribe.py, because everything
    downstream expects exactly one transcripts/<stem>.json per source. That
    makes the path alone a bad cache key: a Scribe transcript would be returned
    verbatim by this tool without ever contacting Deepgram, and re-running with
    a different --model or --spacing-threshold would hand back a stale file.

    So the identity of the cached result is read out of the file itself.
    Returns True to reuse it; raises on a mismatch unless `force` is set,
    which always re-transcribes.
    """
    if force:
        return False
    try:
        payload = json.loads(out_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            f"{out_path} exists but could not be read ({exc}). "
            "Delete it or pass --force to overwrite."
        ) from None

    mismatches: list[str] = []
    if payload.get("_provider") != PROVIDER:
        mismatches.append(f"written by {describe_provider(payload)}, not {PROVIDER}")
    else:
        if payload.get("_model") != model:
            mismatches.append(f"model {payload.get('_model')!r} != requested {model!r}")
        # Language changes the transcript completely on code-switched audio:
        # `en` mangles Hindi into nonsense English, `multi` transcribes both.
        # It therefore belongs in the cache identity like model does.
        cached_lang = payload.get("_language")
        if cached_lang != language:
            mismatches.append(
                f"language {cached_lang!r} != requested {language!r}"
            )
        cached_threshold = payload.get("_spacing_threshold")
        if cached_threshold is not None and float(cached_threshold) != float(spacing_threshold):
            mismatches.append(
                f"spacing-threshold {cached_threshold} != requested {spacing_threshold}"
            )

    if not mismatches:
        if verbose:
            print(f"cached: {out_path.name} ({describe_provider(payload)})")
        return True

    reason = "; ".join(mismatches)
    raise RuntimeError(
        f"{out_path.name} already exists but {reason}.\n"
        "Transcription costs money, so this is refused rather than silently "
        "returning the wrong file (Hard Rule 9 caches per source, not per "
        "provider). Pass --force to re-transcribe and overwrite, or delete the "
        "file first."
    )


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str,
    model: str = "nova-3",
    language: str | None = None,
    verbose: bool = True,
    audio_track: int = 0,
    spacing_threshold: float = 0.05,
    force: bool = False,
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached per source, but the cache is validated against this provider and its
    options - see check_cached().
    """
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcript_path(edit_dir, video, audio_track)

    if out_path.exists() and check_cached(out_path, model, spacing_threshold,
                                          language, force, verbose):
        return out_path

    if verbose:
        print(f"  extracting audio from {video.name}", flush=True)

    n_tracks = count_audio_tracks(video)
    if n_tracks > 1 and verbose:
        print(f"  note: {video.name} has {n_tracks} audio tracks, using track "
              f"{audio_track + 1} (--audio-track to change)", flush=True)

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.wav"
        extract_audio(video, audio, audio_track)

        peak = peak_dbfs(audio)
        if peak < -60.0:
            raise RuntimeError(
                f"track {audio_track + 1} of {video.name} is silent "
                f"(peak {peak:.1f} dBFS) - not uploading. "
                + (f"The file has {n_tracks} audio tracks; try --audio-track "
                   + " or ".join(str(i) for i in range(n_tracks) if i != audio_track) + "."
                   if n_tracks > 1 else "Check the source audio.")
            )

        size_mb = audio.stat().st_size / (1024 * 1024)
        if verbose:
            print(f"  uploading {video.stem}.wav ({size_mb:.1f} MB) to Deepgram {model}", flush=True)
        raw = call_deepgram(audio, api_key, model=model, language=language)

    payload = to_scribe_schema(raw, spacing_threshold)
    # Stamp the identity of this result so the cache can be validated on reuse.
    payload["_model"] = model
    payload["_spacing_threshold"] = spacing_threshold
    payload["_language"] = language
    out_path.write_text(json.dumps(payload, indent=2))
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        n_words = sum(1 for w in payload["words"] if w.get("type") == "word")
        speakers = {w.get("speaker_id") for w in payload["words"] if w.get("speaker_id")}
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        print(f"    words: {n_words}, speakers: {len(speakers)}")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video with Deepgram")
    ap.add_argument("video", type=Path, nargs="?", help="Path to video file")
    ap.add_argument("--edit-dir", type=Path, default=None,
                    help="Edit output directory (default: <video_parent>/edit)")
    ap.add_argument("--model", type=str, default="nova-3",
                    help="Deepgram model. Default: nova-3")
    ap.add_argument("--language", type=str, default=None,
                    help="Optional language code (e.g. 'en'). Omit to auto-detect.")
    ap.add_argument("--num-speakers", type=int, default=None,
                    help="Accepted for parity with transcribe.py, but Deepgram "
                         "auto-detects speaker count and takes no hint. Ignored.")
    ap.add_argument("--audio-track", type=int, default=0,
                    help="Zero-based audio track to transcribe.")
    ap.add_argument("--spacing-threshold", type=float, default=0.05,
                    help="Synthesize a 'spacing' token for inter-word gaps >= this. Default 0.05.")
    ap.add_argument("--force", action="store_true",
                    help="Always re-transcribe and overwrite any existing transcript, "
                         "including one written by another provider or with different "
                         "options. Costs money.")
    ap.add_argument("--convert", type=Path, default=None,
                    help="Offline mode: convert an existing Deepgram JSON file to the "
                         "pipeline schema and print it. No API call, no video needed.")
    args = ap.parse_args()

    if args.convert:
        raw = json.loads(args.convert.read_text())
        print(json.dumps(to_scribe_schema(raw, args.spacing_threshold), indent=2))
        return

    if args.video is None:
        ap.error("video is required unless --convert is used")

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    if args.num_speakers:
        print("  note: --num-speakers ignored (Deepgram auto-detects speaker count)")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()

    try:
        transcribe_one(
            video=video,
            edit_dir=edit_dir,
            api_key=load_api_key(),
            model=args.model,
            language=args.language,
            audio_track=args.audio_track,
            spacing_threshold=args.spacing_threshold,
            force=args.force,
        )
    except RuntimeError as exc:
        # Cache mismatches and API/silent-track failures are expected operator
        # errors, not bugs. Report them plainly rather than as a traceback.
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
