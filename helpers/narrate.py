#!/usr/bin/env python3
"""Generate timestamped narration with ElevenLabs text-to-speech.

Reads a plain-text or markdown script, sends it to the ElevenLabs
``with-timestamps`` endpoint in paragraph-sized chunks, and writes:

    <out>.wav              48 kHz mono PCM narration (chunks joined with short silence)
    <out>.mp3              the same narration as MP3
    <out>.alignment.json   generated word timings ({"words": [{"text","start","end"}]})
    <out>.srt              a sidecar caption file for upload (not burned in)
    <out>.provider.json    raw timestamped provider responses for review
    <out>.tts_metrics.json provenance: model, voice, characters, latency, chunking

Script markup:

    Blank line                  paragraph break passed to the provider
    [pause 0.6]  or  [pause]    non v3 SSML pause request (default 0.5 s)
    [pause]                    v3 audio pause tag with provider chosen duration
    [laughs] [sighs] [sarcastic] ElevenLabs v3 audio tags; passed through only
                                 when --model eleven_v3, otherwise removed
    # heading / <!-- notes -->  ignored (never spoken)

Usage:
    python helpers/narrate.py script.md -o edit/narration
    python helpers/narrate.py script.md -o edit/narration --voice "Brian" --speed 1.1 --style 0.3
    python helpers/narrate.py script.md -o edit/narration --dry-run          # validate + character count only
    python helpers/narrate.py --list-voices

The API key is read from ``ELEVENLABS_API_KEY`` or ``.env`` at the repo root.
``--voice`` accepts a voice id or a case-insensitive name fragment; the default
is ``ELEVENLABS_VOICE_ID``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

API_BASE = "https://api.elevenlabs.io/v1"
MAX_CHUNK_CHARS = 4200
DEFAULT_PAUSE_S = 0.5
CHUNK_GAP_S = 0.32
SAMPLE_RATE = 48000

V3_TAG_WORDS = {
    "laughs",
    "laugh",
    "chuckles",
    "sighs",
    "sigh",
    "whispers",
    "whisper",
    "sarcastic",
    "excited",
    "curious",
    "deadpan",
    "shouts",
    "gasps",
    "clears throat",
    "pause",
    "long pause",
    "mischievously",
    "nervously",
    "sadly",
    "happily",
    "dramatic",
    "dramatically",
    "exhales",
}


# ---------------------------------------------------------------- script parsing
# read the elevenlabs api key from the environment or the dotenv file at the repo root
def load_api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if key:
        return key
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ELEVENLABS_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    raise SystemExit(
        "ELEVENLABS_API_KEY not set (environment or .env at the repo root)"
    )


# read the default voice id from the environment or the dotenv file
def default_voice_id() -> str | None:
    if os.environ.get("ELEVENLABS_VOICE_ID"):
        return os.environ["ELEVENLABS_VOICE_ID"]
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ELEVENLABS_VOICE_ID="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return None


# split a script into paragraphs turning pause markers into ssml breaks and dropping headings and notes
def parse_script(text: str, *, keep_v3_tags: bool) -> list[dict[str, Any]]:
    """Return paragraph dictionaries containing prepared speech text.

    Non v3 pause markers become SSML breaks. V3 keeps untimed pause tags and
    rejects timed pauses. Headings and HTML comments are not spoken.
    """
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    paragraphs: list[dict[str, Any]] = []
    for raw in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in raw.splitlines()]
        lines = [ln for ln in lines if ln and not ln.startswith("#")]
        if not lines:
            continue
        para = " ".join(lines)

        # replace a pause marker with an ssml break clamped between a tenth of a second and three seconds
        def pause_repl(match: re.Match) -> str:
            if keep_v3_tags:
                if match.group(1):
                    raise ValueError("timed pause markers require a non v3 model")
                return " [pause] "
            seconds = float(match.group(1)) if match.group(1) else DEFAULT_PAUSE_S
            if not 0.1 <= seconds <= 3.0:
                raise ValueError("pause duration must be between 0.1 and 3 seconds")
            return f' <break time="{seconds:g}s" /> '

        para = re.sub(r"\[pause(?:\s+([0-9.]+))?\]", pause_repl, para, flags=re.I)

        # keep a known v3 audio tag only when asked otherwise remove it and leave unknown brackets alone
        def tag_repl(match: re.Match) -> str:
            word = match.group(1).strip().lower()
            if word in V3_TAG_WORDS:
                return f"[{match.group(1).strip()}]" if keep_v3_tags else ""
            return match.group(0)

        para = re.sub(r"\[([A-Za-z ]{2,20})\]", tag_repl, para)
        para = re.sub(r"\s+", " ", para).strip()
        if keep_v3_tags and re.search(r"<break\b", para, re.I):
            raise ValueError("v3 does not support SSML breaks use audio tags")
        if para and re.sub(r"<[^>]*>|\[[^\]]*\]", "", para).strip():
            paragraphs.append({"text": para})
    if not paragraphs:
        raise ValueError("script contains no speakable text")
    return paragraphs


# group paragraphs into request sized chunks that stay under the character limit
def chunk_paragraphs(
    paragraphs: list[dict[str, Any]], max_chars: int = MAX_CHUNK_CHARS
) -> list[str]:
    """Group paragraphs into API requests under the character limit."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for para in paragraphs:
        text = para["text"]
        if len(text) > max_chars:
            raise ValueError(
                f"a single paragraph exceeds {max_chars} characters; split it"
            )
        if current and size + len(text) + 2 > max_chars:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(text)
        size += len(text) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


# estimate text length excluding ssml break markup
def spoken_characters(text: str) -> int:
    """Approximate text length excluding SSML breaks and not a billing estimate."""
    return len(re.sub(r"<break[^>]*/>", "", text))


# ---------------------------------------------------------------- alignment
# convert character level timestamps into word entries and drop ssml and audio tag tokens
def words_from_alignment(
    alignment: dict[str, Any], offset: float = 0.0
) -> list[dict[str, Any]]:
    """Convert ElevenLabs character timestamps to words, dropping markup tokens."""
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    if not chars or not (len(chars) == len(starts) == len(ends)):
        raise ValueError("alignment arrays must be nonempty and have equal lengths")
    if not math.isfinite(offset) or offset < 0:
        raise ValueError("alignment offset must be finite and nonnegative")
    previous_start = previous_end = 0.0
    for ch, start, end in zip(chars, starts, ends):
        if not isinstance(ch, str) or len(ch) != 1:
            raise ValueError("alignment must contain individual characters")
        if any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            for v in (start, end)
        ):
            raise ValueError("alignment timestamps must be finite numbers")
        if start < previous_start or end < previous_end or end < start:
            raise ValueError("alignment timestamps must be nonnegative and ordered")
        previous_start, previous_end = start, end
    text = "".join(chars)
    # blank markup without changing the character positions used by provider timestamps
    # only provider control tokens are removed from the spoken word list
    def strip_control(match):
        token = match.group()
        label = token[1:-1].strip().lower()
        known = label in V3_TAG_WORDS or re.fullmatch(r"pause(?:\s+[0-9.]+)?", label)
        return " " * len(token) if known or token.lower().startswith("<break") else token

    text = re.sub(r"<[^>]*>|\[[^\]]*\]", strip_control, text)
    words = []
    for match in re.finditer(r"\S+", text):
        start = round(starts[match.start()] + offset, 3)
        end = round(ends[match.end() - 1] + offset, 3)
        if end <= start:
            raise ValueError("spoken words must have positive duration")
        words.append(
            {"type": "word", "text": match.group(), "start": start, "end": end}
        )
    if not words:
        raise ValueError("alignment contains no spoken words")
    return words


# group words into caption cues by length and sentence end and write them as srt
def write_srt(
    words: list[dict[str, Any]], path: Path, *, max_words: int = 7, max_chars: int = 42
) -> int:
    cues: list[tuple[float, float, str]] = []
    current: list[dict[str, Any]] = []

    # close the current cue if it has words
    def flush() -> None:
        nonlocal current
        if current:
            cues.append(
                (
                    current[0]["start"],
                    current[-1]["end"],
                    " ".join(w["text"] for w in current),
                )
            )
            current = []

    for word in words:
        proposed = " ".join(w["text"] for w in [*current, word])
        if current and (word["start"] - current[-1]["end"] >= CHUNK_GAP_S - 0.002 or len(current) >= max_words or len(proposed) > max_chars):
            flush()
        current.append(word)
        if word["text"][-1:] in ".?!":
            flush()
    flush()

    # format seconds as an srt timestamp
    def stamp(seconds: float) -> str:
        ms = int(round(seconds * 1000))
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for index, (start, end, text) in enumerate(cues, start=1):
        lines.append(f"{index}\n{stamp(start)} --> {stamp(end)}\n{text}\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    return len(cues)


# ---------------------------------------------------------------- API
# fetch the voices available to the account
def list_voices(api_key: str) -> list[dict[str, Any]]:
    import requests

    response = requests.get(
        f"{API_BASE}/voices", headers={"xi-api-key": api_key}, timeout=60
    )
    response.raise_for_status()
    return response.json().get("voices", [])


# resolve a voice id or a name fragment to a single voice id and name or exit on no or many matches
def resolve_voice(api_key: str, voice: str | None) -> tuple[str, str]:
    """Return (voice_id, voice_name) from an id or a name fragment."""
    voice = voice or default_voice_id()
    if not voice:
        raise SystemExit("no voice: pass --voice <id|name> or set ELEVENLABS_VOICE_ID")
    voices = list_voices(api_key)
    for item in voices:
        if item.get("voice_id") == voice:
            return voice, item.get("name", voice)
    matches = [
        item for item in voices if voice.lower() in str(item.get("name", "")).lower()
    ]
    if len(matches) == 1:
        return matches[0]["voice_id"], matches[0]["name"]
    if not matches:
        raise SystemExit(f"voice '{voice}' not found; run --list-voices")
    names = ", ".join(item["name"] for item in matches)
    raise SystemExit(f"voice '{voice}' is ambiguous: {names}")


# call the with timestamps endpoint for one chunk passing neighbor text for continuity and record latency
def synthesize_chunk(
    api_key: str,
    voice_id: str,
    text: str,
    *,
    model: str,
    settings: dict[str, float],
    previous_text: str | None,
    next_text: str | None,
) -> dict[str, Any]:
    import requests

    payload: dict[str, Any] = {
        "text": text,
        "model_id": model,
        "voice_settings": settings,
    }
    if previous_text and model != "eleven_v3":
        payload["previous_text"] = previous_text[-600:]
    if next_text and model != "eleven_v3":
        payload["next_text"] = next_text[:600]
    started = time.time()
    response = requests.post(
        f"{API_BASE}/text-to-speech/{voice_id}/with-timestamps",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=600,
    )
    if response.status_code != 200:
        raise SystemExit(f"ElevenLabs request failed with HTTP {response.status_code}")
    data = response.json()
    if "audio_base64" not in data:
        raise SystemExit("ElevenLabs response has no audio")
    if not base64.b64decode(data["audio_base64"], validate=True):
        raise ValueError("provider returned empty audio")
    words_from_alignment(
        data.get("normalized_alignment") or data.get("alignment") or {}
    )
    data["_latency_s"] = round(time.time() - started, 2)
    return data


# ---------------------------------------------------------------- audio assembly
# write chunk mp3 bytes and decode them to a mono wav returning its path and duration
def _decode_to_pcm(mp3_bytes: bytes, workdir: Path, index: int) -> tuple[Path, float]:
    mp3 = workdir / f"chunk_{index:02d}.mp3"
    wav = workdir / f"chunk_{index:02d}.wav"
    mp3.write_bytes(mp3_bytes)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(mp3),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            str(wav),
        ],
        check=True,
    )
    with wave.open(str(wav)) as handle:
        duration = handle.getnframes() / handle.getframerate()
    return wav, duration


# join the chunk audio with short silence gaps and offset each chunk word times onto the shared timeline
def assemble(
    chunks: list[dict[str, Any]], out_base: Path, gap_s: float = CHUNK_GAP_S
) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
    """Concatenate chunk audio with silence gaps and offset word times."""
    if any(
        out_base.with_suffix(s).exists() or out_base.with_suffix(s).is_symlink()
        for s in (".wav", ".mp3")
    ):
        raise FileExistsError("audio assembly requires new output files")
    if not chunks or not math.isfinite(gap_s) or gap_s < 0:
        raise ValueError("assembly requires chunks and a finite nonnegative gap")
    gap_frames = round(SAMPLE_RATE * gap_s)
    words: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        pcm_parts: list[bytes] = []
        cursor = 0.0
        for index, chunk in enumerate(chunks):
            wav, duration = _decode_to_pcm(
                base64.b64decode(chunk["audio_base64"], validate=True), workdir, index
            )
            with wave.open(str(wav)) as handle:
                pcm_parts.append(handle.readframes(handle.getnframes()))
            chunk_words = words_from_alignment(
                chunk.get("normalized_alignment") or chunk.get("alignment") or {},
                offset=cursor,
            )
            if duration <= 0 or chunk_words[-1]["end"] > cursor + duration + 0.05:
                raise ValueError("alignment exceeds the decoded chunk duration")
            words.extend(chunk_words)
            segments.append(
                {
                    "index": index,
                    "start": round(cursor, 3),
                    "end": round(cursor + duration, 3),
                    "text": chunk["_text"],
                    "characters": spoken_characters(chunk["_text"]),
                }
            )
            cursor += duration
            if index != len(chunks) - 1:
                pcm_parts.append(b"\x00\x00" * gap_frames)
                cursor += gap_frames / SAMPLE_RATE
        out_wav = out_base.with_suffix(".wav")
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_wav), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(b"".join(pcm_parts))
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(out_base.with_suffix(".wav")),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "160k",
            str(out_base.with_suffix(".mp3")),
        ],
        check=True,
    )
    return words, cursor, segments


# read a wav and report its duration and peak level in dbfs
def measure_audible(wav_path: Path) -> dict[str, float]:
    with wave.open(str(wav_path)) as handle:
        frames = handle.readframes(handle.getnframes())
        rate = handle.getframerate()
    import array

    samples = array.array("h", frames)
    if not samples:
        return {"duration_s": 0.0, "peak_dbfs": -120.0}
    peak = max(abs(s) for s in samples) / 32768.0
    return {
        "duration_s": round(len(samples) / rate, 3),
        "peak_dbfs": round(20 * math.log10(peak) if peak else -120.0, 1),
    }


# enumerate the complete output set with the completion marker last
def output_paths(base: Path) -> dict[str, Path]:
    return {
        suffix: base.with_suffix(suffix)
        for suffix in (
            ".wav",
            ".mp3",
            ".alignment.json",
            ".srt",
            ".provider.json",
            ".tts_metrics.json",
        )
    }


# hash output files in bounded blocks for cache validation
def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# reuse output only when settings and every recorded file hash match
def cache_valid(base: Path, fingerprint: str) -> bool:
    paths = output_paths(base)
    try:
        metrics = json.loads(paths[".tts_metrics.json"].read_text())
        if metrics.get("fingerprint") != fingerprint:
            return False
        return all(
            path.is_file()
            and not path.is_symlink()
            and metrics.get("output_hashes", {}).get(suffix) == file_hash(path)
            for suffix, path in paths.items()
            if suffix != ".tts_metrics.json"
        )
    except (OSError, ValueError, AttributeError, TypeError):
        return False


# ---------------------------------------------------------------- main
# build the command line parser for the narration generator
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("script", nargs="?", type=Path, help="script .md/.txt")
    parser.add_argument(
        "-o", "--output", type=Path, help="output base path, e.g. edit/narration"
    )
    parser.add_argument(
        "--voice",
        help="ElevenLabs voice id or name fragment (default: ELEVENLABS_VOICE_ID)",
    )
    parser.add_argument(
        "--model",
        default="eleven_multilingual_v2",
        help="eleven_multilingual_v2 (default, precise timestamps) or eleven_v3 (more expressive, audio tags)",
    )
    parser.add_argument("--stability", type=float, default=0.5)
    parser.add_argument("--similarity", type=float, default=0.75)
    parser.add_argument("--style", type=float, default=0.0)
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="0.7-1.2; 1.05-1.15 for fast explainer delivery",
    )
    parser.add_argument("--no-speaker-boost", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse, chunk, count characters; no API call",
    )
    parser.add_argument("--list-voices", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace existing narration outputs after a successful generation",
    )
    return parser


# command line entry that parses the script synthesizes each chunk and writes the audio alignment srt and metrics
def main() -> None:
    args = build_parser().parse_args()
    if args.list_voices:
        for item in list_voices(load_api_key()):
            labels = item.get("labels") or {}
            print(
                f"{item['voice_id']}  {item['name']:32} {labels.get('gender', '?'):8} {labels.get('age', '?'):12} "
                f"{labels.get('accent', '?'):10} {labels.get('descriptive') or labels.get('description', '')}"
            )
        return
    if not args.script or not args.output:
        raise SystemExit("script and -o/--output are required (or use --list-voices)")
    if not 0.7 <= args.speed <= 1.2:
        raise SystemExit("--speed must be between 0.7 and 1.2")
    for name in ("stability", "similarity", "style"):
        if not 0 <= getattr(args, name) <= 1:
            raise SystemExit(f"--{name} must be between 0 and 1")
    if args.model == "eleven_v3" and args.stability not in (0, 0.5, 1):
        raise SystemExit("v3 stability must be 0 or 0.5 or 1")

    keep_tags = args.model.startswith("eleven_v3")
    paragraphs = parse_script(
        args.script.read_text(encoding="utf-8"), keep_v3_tags=keep_tags
    )
    chunks = chunk_paragraphs(paragraphs)
    total_chars = sum(spoken_characters(chunk) for chunk in chunks)
    word_estimate = sum(len(chunk.split()) for chunk in chunks)
    print(
        f"script: {len(paragraphs)} paragraphs, {len(chunks)} request(s), {total_chars} characters, ~{word_estimate} words"
    )
    print(f"estimated duration at 200 wpm: {word_estimate / 200 * 60:.0f}s")
    if args.dry_run:
        for index, chunk in enumerate(chunks):
            print(f"--- chunk {index} ({spoken_characters(chunk)} chars) ---")
            print(chunk[:300] + ("…" if len(chunk) > 300 else ""))
        return

    settings = {
        "stability": args.stability,
        "similarity_boost": args.similarity,
        "style": args.style,
        "use_speaker_boost": not args.no_speaker_boost,
        "speed": args.speed,
    }
    out_base = args.output
    outputs = output_paths(out_base)
    for path in outputs.values():
        if path.is_symlink() or path.resolve() == args.script.resolve():
            raise SystemExit("output must not replace the script or a symbolic link")
        if path.exists() and not path.is_file():
            raise SystemExit(f"output is not a regular file: {path}")
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required for narration audio assembly")
    out_base.parent.mkdir(parents=True, exist_ok=True)
    api_key = load_api_key()
    voice_id, voice_name = resolve_voice(api_key, args.voice)
    # fingerprint the chunks voice model and settings so unchanged scripts reuse the cached output
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "version": 2,
                "chunks": chunks,
                "model": args.model,
                "voice": voice_id,
                "settings": settings,
                "gap_s": CHUNK_GAP_S,
                "sample_rate": SAMPLE_RATE,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:12]
    if not args.force and cache_valid(out_base, fingerprint):
        print(f"cached: {out_base.with_suffix('.wav')} (complete verified output set)")
        return
    if not args.force and any(path.exists() for path in outputs.values()):
        raise SystemExit(
            "outputs exist but do not match a complete cache use a new output or --force"
        )

    responses: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        print(
            f"synthesizing chunk {index + 1}/{len(chunks)} ({spoken_characters(chunk)} chars) with {voice_name} / {args.model}"
        )
        data = synthesize_chunk(
            api_key,
            voice_id,
            chunk,
            model=args.model,
            settings=settings,
            previous_text=chunks[index - 1] if index > 0 else None,
            next_text=chunks[index + 1] if index + 1 < len(chunks) else None,
        )
        data["_text"] = chunk
        responses.append(data)

    destination = out_base
    with tempfile.TemporaryDirectory(prefix="narration-", dir=out_base.parent) as tmp:
        out_base = Path(tmp) / "narration"
        metrics_path = out_base.with_suffix(".tts_metrics.json")
        words, duration, segments = assemble(responses, out_base)
        audible = measure_audible(out_base.with_suffix(".wav"))
        if audible["peak_dbfs"] < -60:
            raise SystemExit("narration is silent; refusing to write alignment")

        alignment = {
            "kind": "narration_alignment",
            "generator": "helpers/narrate.py",
            "model": args.model,
            "voice_id": voice_id,
            "voice_name": voice_name,
            "voice_settings": settings,
            "duration": round(duration, 3),
            "audio": destination.with_suffix(".wav").name,
            "words": words,
            "segments": segments,
        }
        out_base.with_suffix(".alignment.json").write_text(
            json.dumps(alignment, indent=1) + "\n", encoding="utf-8"
        )
        cues = write_srt(words, out_base.with_suffix(".srt"))
        metrics = {
            "provider": "elevenlabs",
            "model": args.model,
            "voice_id": voice_id,
            "voice_name": voice_name,
            "voice_settings": settings,
            "requests": len(responses),
            "text_characters": total_chars,
            "alignment_words": len(words),
            "duration_s": round(duration, 3),
            "words_per_minute": round(60 * len(words) / duration, 1) if duration else 0,
            "peak_dbfs": audible["peak_dbfs"],
            "latency_s": round(sum(r["_latency_s"] for r in responses), 2),
            "fingerprint": fingerprint,
            "script": str(args.script),
        }
        out_base.with_suffix(".provider.json").write_text(
            json.dumps(responses, indent=1) + "\n", encoding="utf-8"
        )
        metrics["output_hashes"] = {
            suffix: file_hash(path)
            for suffix, path in output_paths(out_base).items()
            if suffix != ".tts_metrics.json"
        }
        metrics_path.write_text(json.dumps(metrics, indent=1) + "\n", encoding="utf-8")
        # publish the metrics last so an interrupted publication is never a valid cache
        for suffix, staged in output_paths(out_base).items():
            staged.replace(outputs[suffix])
    out_base = destination

    print(
        f"narration → {out_base.with_suffix('.wav')} ({duration:.1f}s, {len(words)} words, "
        f"{metrics['words_per_minute']} wpm, peak {audible['peak_dbfs']} dBFS)"
    )
    print(
        f"alignment → {out_base.with_suffix('.alignment.json')}   srt → {out_base.with_suffix('.srt')} ({cues} cues)"
    )


if __name__ == "__main__":
    main()
