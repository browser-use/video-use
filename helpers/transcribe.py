"""Transcribe a video with ElevenLabs Scribe or a local Whisper engine.

Extracts mono 16kHz audio via ffmpeg, then either uploads to Scribe with
verbatim + diarize + audio events + word-level timestamps, or runs the local
engine from local_stt.py. Writes the transcript to
<edit_dir>/transcripts/<video_stem>.json with a top-level "engine" key.

Engine selection is explicit, never a silent fallback: --engine wins, then
VIDEO_USE_TRANSCRIBER (elevenlabs or local) from .env or the environment, then
elevenlabs when ELEVENLABS_API_KEY resolves. Every run prints the engine and
where the choice came from.

Cached: if the output file already exists it is reused, even when it was made
by the other engine (a note is printed). --force re-transcribes and replaces
the old file only after the new run succeeds.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe.py <video_path> --language en
    python helpers/transcribe.py <video_path> --num-speakers 2
    python helpers/transcribe.py <video_path> --engine local
"""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import requests


SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ENGINES = ("elevenlabs", "local")
ENV_NAMES = ("ELEVENLABS_API_KEY", "VIDEO_USE_TRANSCRIBER")


# the dotenv files consulted in order with the repo root first then the working directory
def dotenv_candidates() -> list[Path]:
    return [Path(__file__).resolve().parent.parent / ".env", Path(".env")]


# unwrap a quoted dotenv value keeping any hash inside the quotes or drop a trailing comment from a bare one
def dotenv_value(raw: str) -> str:
    raw = raw.strip()
    quote = raw[:1]
    if quote in ('"', "'") and raw.find(quote, 1) > 0:
        return raw[1:raw.index(quote, 1)]
    return raw.split("#", 1)[0].strip()


# read the two settings this tool understands from dotenv files then the environment
def load_env(candidates: list[Path] | None = None) -> dict[str, tuple[str, str]]:
    """Map each known name to (value, source). A dotenv value wins over the environment."""
    found: dict[str, tuple[str, str]] = {}
    if candidates is None:
        candidates = dotenv_candidates()
    for candidate in candidates:
        if not candidate.exists():
            continue
        # parse key equals value lines and strip surrounding quotes from the value
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = dotenv_value(v)
            if k in ENV_NAMES and v and k not in found:
                found[k] = (v, ".env")
    for name in ENV_NAMES:
        v = os.environ.get(name, "")
        if v and name not in found:
            found[name] = (v, "environment")
    return found


# decide which engine runs and say where that decision came from
def resolve_engine(flag: str | None, env: dict[str, tuple[str, str]]) -> tuple[str, str]:
    if flag:
        if flag not in ENGINES:
            raise SystemExit(f"unknown engine {flag!r}; choose from {', '.join(ENGINES)}")
        return flag, "--engine"
    setting = env.get("VIDEO_USE_TRANSCRIBER")
    if setting:
        value, source = setting
        value = value.strip().lower()
        if value not in ENGINES:
            raise SystemExit(
                f"VIDEO_USE_TRANSCRIBER={value!r} (from {source}) is not valid; "
                f"use one of {', '.join(ENGINES)}"
            )
        return value, source
    if "ELEVENLABS_API_KEY" in env:
        return "elevenlabs", "ELEVENLABS_API_KEY present"
    raise SystemExit(
        "no transcription engine configured. Add one line to .env at the video-use repo root:\n"
        "  ELEVENLABS_API_KEY=<your key>        hosted Scribe transcription\n"
        "  VIDEO_USE_TRANSCRIBER=local         local Whisper, run helpers/local_stt.py probe first"
    )


# print the engine choice once so a run is never silently routed
def announce_engine(engine: str, source: str, env: dict[str, tuple[str, str]]) -> None:
    line = f"engine: {engine} (from {source})"
    if engine == "local" and "ELEVENLABS_API_KEY" in env:
        line += "; ELEVENLABS_API_KEY is present but unused"
    print(line, flush=True)


# count the audio streams in a container so multi track recordings can be flagged
def count_audio_tracks(video_path: Path) -> int:
    """How many audio streams the container holds."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True,
    )
    return len([ln for ln in out.stdout.splitlines() if ln.strip()])


# measure the peak level of a wav in dbfs so a silent track is caught before upload
def peak_dbfs(wav_path: Path) -> float:
    """Peak level of a 16-bit PCM wav, in dBFS. -inf for digital silence."""
    peak = 0
    with wave.open(str(wav_path), "rb") as w:
        # A chunk at a time: batch mode runs several of these at once, and a two-hour
        # take is 230 MB of 16 kHz mono before the array copy doubles it.
        while frames := w.readframes(1 << 16):
            samples = array.array("h", frames)
            peak = max(peak, max(samples), -min(samples))
    return 20 * math.log10(peak / 32768) if peak > 0 else float("-inf")


# use ffmpeg to write a mono 16khz pcm wav from the video
def extract_audio(video_path: Path, dest: Path, audio_track: int = 0) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-map", f"0:a:{audio_track}",
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# upload the wav to the scribe endpoint with verbatim settings and return the parsed json
def call_scribe(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
) -> dict:
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

    # any non 200 response is treated as a hard failure with a trimmed body for context
    if resp.status_code != 200:
        raise RuntimeError(f"Scribe returned {resp.status_code}: {resp.text[:500]}")

    return resp.json()


# make sure the local library is installed before any audio is extracted
def preflight_local(local_options: dict) -> str:
    import local_stt

    return local_stt.preflight(local_options.get("library"))


# run the local whisper engine on the extracted wav
def call_local(audio_path: Path, language: str | None, local_options: dict) -> dict:
    # imported here so the elevenlabs path never needs the local module or its libraries
    import local_stt

    return local_stt.transcribe_wav(
        audio_path,
        library=local_options.get("library"),
        language=language,
        model=local_options.get("model"),
        verbatim=local_options.get("verbatim", True),
    )


# resolve where a transcript lands with the track number in the name for anything but track zero
def transcript_path(edit_dir: Path, video: Path, audio_track: int = 0) -> Path:
    """Where a video's transcript lands.

    The track belongs in the name, or a rerun with --audio-track hands back the transcript of
    the track it is meant to replace. Track 0 keeps the plain name, so transcripts made before
    the flag existed stay valid. Batch mode tests its cache with this too — one function, so
    the two cannot drift apart.
    """
    suffix = "" if audio_track == 0 else f".track{audio_track}"
    return edit_dir / "transcripts" / f"{video.stem}{suffix}.json"


# read which engine made a cached transcript treating files from before the key existed as scribe
def cached_engine(path: Path) -> str:
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return "unknown"
    if not isinstance(payload, dict):
        return "unknown"
    return str(payload.get("engine", "elevenlabs"))


# transcribe one video into the transcripts folder and return the json path reusing a cached file if present
def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str | None = None,
    language: str | None = None,
    num_speakers: int | None = None,
    verbose: bool = True,
    audio_track: int = 0,
    *,
    engine: str = "elevenlabs",
    force: bool = False,
    local_options: dict | None = None,
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    The positional parameters keep their historical order so older callers still work; the
    engine controls are keyword only. Cached: returns the existing path immediately unless
    force is set. A cached file made by the other engine is still reused; the note tells the
    agent how to redo it.
    """
    if engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r}")
    if engine == "elevenlabs" and not api_key:
        raise ValueError("elevenlabs engine needs an api key")

    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcript_path(edit_dir, video, audio_track)

    if out_path.exists():
        previous = cached_engine(out_path)
        if not force:
            if verbose:
                note = "" if previous == engine else f" (made by {previous}; --force to redo with {engine})"
                print(f"cached: {out_path.name}{note}")
            return out_path
        if verbose:
            print(f"  replacing {out_path.name} (made by {previous}) with a {engine} transcript", flush=True)

    # a missing local library must fail here not after minutes of audio extraction
    if engine == "local":
        preflight_local(local_options or {})

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

        # Uploading silence costs the same as uploading speech and returns
        # nothing, so catch the wrong-track case before paying for it.
        peak = peak_dbfs(audio)
        if peak < -60.0:
            raise RuntimeError(
                f"track {audio_track + 1} of {video.name} is silent "
                f"(peak {peak:.1f} dBFS) - not transcribing. "
                + (f"The file has {n_tracks} audio tracks; try --audio-track "
                   + " or ".join(str(i) for i in range(n_tracks) if i != audio_track) + "."
                   if n_tracks > 1 else "Check the source audio.")
            )

        size_mb = audio.stat().st_size / (1024 * 1024)
        if engine == "elevenlabs":
            if verbose:
                print(f"  uploading {video.stem}.wav ({size_mb:.1f} MB)", flush=True)
            payload = call_scribe(audio, api_key, language, num_speakers)
            if isinstance(payload, dict):
                payload["engine"] = "elevenlabs"
        else:
            if verbose:
                print(f"  transcribing {video.stem}.wav ({size_mb:.1f} MB) locally", flush=True)
            payload = call_local(audio, language, local_options or {})

    # write beside the target and swap in so a failed run never destroys the old transcript
    tmp_path = out_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2))
    os.replace(tmp_path, out_path)
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        if isinstance(payload, dict) and "words" in payload:
            print(f"    words: {len(payload['words'])}")

    return out_path


# add the engine flags shared by the single and batch entry points
def add_engine_arguments(ap: argparse.ArgumentParser) -> None:
    ap.add_argument(
        "--engine",
        choices=ENGINES,
        default=None,
        help="elevenlabs or local. Default: VIDEO_USE_TRANSCRIBER, else elevenlabs when a key resolves.",
    )
    ap.add_argument("--force", action="store_true", help="Re-transcribe even when a transcript is cached.")
    ap.add_argument(
        "--library",
        choices=("mlx-whisper", "faster-whisper"),
        default=None,
        help="Local engine only: override the library chosen by the hardware probe.",
    )
    ap.add_argument(
        "--model",
        default=None,
        help="Local engine only: Hugging Face repo id or local model directory.",
    )
    ap.add_argument(
        "--no-verbatim-prompt",
        action="store_true",
        help="Local engine only: disable the filler-preserving prompt.",
    )


# gather the local engine options from parsed arguments
def local_options_from(args: argparse.Namespace) -> dict:
    return {
        "library": args.library,
        "model": args.model,
        "verbatim": not args.no_verbatim_prompt,
    }


# cli entry point that resolves the video and edit directory then runs a single transcription
def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video with ElevenLabs Scribe or a local engine")
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
        "--num-speakers",
        type=int,
        default=None,
        help="Optional number of speakers when known. Improves diarization accuracy (elevenlabs only).",
    )
    ap.add_argument(
        "--audio-track",
        type=int,
        default=0,
        help="Zero-based audio track to transcribe. OBS writes the game on track 0 "
             "and the mic on track 1; without this ffmpeg applies its default audio "
             "stream selection, which picks the track with the most channels.",
    )
    add_engine_arguments(ap)
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    env = load_env()
    engine, source = resolve_engine(args.engine, env)
    announce_engine(engine, source, env)
    api_key = env["ELEVENLABS_API_KEY"][0] if "ELEVENLABS_API_KEY" in env else None
    if engine == "elevenlabs" and not api_key:
        sys.exit("ELEVENLABS_API_KEY not found in .env or environment")

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        engine=engine,
        api_key=api_key,
        language=args.language,
        num_speakers=args.num_speakers,
        audio_track=args.audio_track,
        force=args.force,
        local_options=local_options_from(args),
    )


if __name__ == "__main__":
    main()
