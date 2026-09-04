"""Transcribe a video with ElevenLabs Scribe (default) or BW Labs STT.

ElevenLabs Scribe: HTTP multipart upload, word-level timestamps, speaker
diarization, and audio events.  Requires ELEVENLABS_API_KEY.

BW Labs STT: WebSocket streaming, no duration limit, no speaker diarization,
no language selection.  Requires BW_STT_API_KEY and the websockets library.
  Get a key:  https://labs.bandwidth.com/
  API docs:   https://labs.bandwidth.com/docs/speech-to-text
  Install:    pip install websockets
         or:  pip install "video-use[bw-stt]"

Provider selection (highest priority first):
  1. --provider {elevenlabs,bw_stt} CLI flag
  2. TRANSCRIBE_PROVIDER environment variable or .env entry
  3. Auto-detect: whichever API key is present (ElevenLabs wins if both)

Output JSON schema (both providers):
  {"words": [{"type": "word", "text": "...", "start": 0.0, "end": 0.0,
              "speaker_id": "speaker_0"}, ...],
   "text": "...", "audio_duration_seconds": 0.0}

ElevenLabs responses also include "spacing" and "audio_event" word entries.
pack_transcripts.py handles all three types.

Cached: if the output file already exists, the API call is skipped.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe.py <video_path> --language en
    python helpers/transcribe.py <video_path> --num-speakers 2
    python helpers/transcribe.py <video_path> --provider bw_stt
    python helpers/transcribe.py <video_path> --audio-track 1
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
PROVIDERS = ("elevenlabs", "bw_stt")


def _find_env_value(key_name: str) -> str:
    """Return the value of *key_name* from .env or environment, or '' if absent."""
    for candidate in [Path(__file__).resolve().parent.parent / ".env", Path(".env")]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == key_name:
                    return v.strip().strip('"').strip("'")
    return os.environ.get(key_name, "")


def load_api_key(provider: str = "elevenlabs") -> str:
    env_var = "ELEVENLABS_API_KEY" if provider == "elevenlabs" else "BW_STT_API_KEY"
    v = _find_env_value(env_var)
    if not v:
        sys.exit(f"{env_var} not found in .env or environment")
    return v


def detect_provider() -> str:
    """Return the provider whose API key is available; ElevenLabs wins if both are set."""
    if _find_env_value("ELEVENLABS_API_KEY"):
        return "elevenlabs"
    if _find_env_value("BW_STT_API_KEY"):
        return "bw_stt"
    sys.exit(
        "No transcription API key found. "
        "Set ELEVENLABS_API_KEY (ElevenLabs Scribe) or BW_STT_API_KEY (BW Labs STT) "
        "in .env or the environment."
    )


def resolve_provider(provider: str | None = None) -> str:
    """Resolve the provider using the three-tier priority order.

    1. *provider* argument (from --provider CLI flag)
    2. TRANSCRIBE_PROVIDER in .env or environment
    3. Auto-detection from available API keys
    """
    if provider is not None:
        if provider not in PROVIDERS:
            sys.exit(f"Unknown provider '{provider}'. Choose from: {', '.join(PROVIDERS)}")
        return provider
    env_prov = _find_env_value("TRANSCRIBE_PROVIDER")
    if env_prov:
        if env_prov not in PROVIDERS:
            sys.exit(
                f"TRANSCRIBE_PROVIDER='{env_prov}' is not valid. "
                f"Choose from: {', '.join(PROVIDERS)}"
            )
        return env_prov
    return detect_provider()


def count_audio_tracks(video_path: Path) -> int:
    """How many audio streams the container holds."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True,
    )
    return len([ln for ln in out.stdout.splitlines() if ln.strip()])


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


def extract_audio(video_path: Path, dest: Path, audio_track: int = 0) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-map", f"0:a:{audio_track}",
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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

    if resp.status_code != 200:
        raise RuntimeError(f"Scribe returned {resp.status_code}: {resp.text[:500]}")

    return resp.json()


def _call_bw_stt(audio: Path, api_key: str) -> dict:
    """Stream *audio* to BW Labs STT via WebSocket; return a normalised transcript dict.

    Uses the public WebSocket API directly — no SDK required, only websockets (PyPI).
    API docs: https://labs.bandwidth.com/docs/speech-to-text
    """
    try:
        import websockets.sync.client as ws_sync
    except ImportError:
        sys.exit(
            "websockets is required for BW Labs STT.\n"
            "Install it: pip install websockets\n"
            '       or: pip install "video-use[bw-stt]"'
        )

    import threading

    ENDPOINT = (
        "wss://api.labs.bandwidth.com/audio/v1/listen"
        "?encoding=linear16&sample_rate=16000&channels=1&mode=instant"
    )
    FRAMES_PER_CHUNK = 2560  # 160ms × 16 kHz mono (2560 samples × 2 bytes = 5120 bytes)
    MIN_FRAME_BYTES = 640    # API minimum frame duration is 20ms = 320 samples × 2 bytes

    all_words: list[dict] = []
    text_parts: list[str] = []
    audio_duration = 0.0

    with ws_sync.connect(ENDPOINT, additional_headers={"X-BW-LABS-API-KEY": api_key}) as ws:
        msg = json.loads(ws.recv())
        if msg["type"] != "SessionOpened":
            raise RuntimeError(f"unexpected opening message: {msg['type']}")

        # Send from a separate thread so the main thread drains Segment messages
        # as they stream in. The server transcribes while audio uploads; reading
        # only after sending everything would stall long files once the client's
        # receive buffer fills.
        send_error: list[BaseException] = []

        def _send_audio() -> None:
            try:
                # wave.open strips the WAV header; readframes() returns raw PCM
                with wave.open(str(audio), "rb") as wf:
                    while frames := wf.readframes(FRAMES_PER_CHUNK):
                        if len(frames) < MIN_FRAME_BYTES:
                            # Pad a short tail with silence to the 20ms API minimum
                            frames += b"\x00" * (MIN_FRAME_BYTES - len(frames))
                        ws.send(frames)
                ws.send(json.dumps({"type": "CloseStream"}))
            except BaseException as e:  # surfaced after the recv loop ends
                send_error.append(e)

        sender = threading.Thread(target=_send_audio, daemon=True)
        sender.start()

        session_closed = False
        for raw in ws:
            msg = json.loads(raw)
            if msg["type"] == "Segment":
                text_parts.append(msg["text"])
                for w in msg.get("words", []):
                    all_words.append({
                        "type": "word",
                        "text": w["word"],
                        "start": w["start"],
                        "end": w["end"],
                        "speaker_id": "speaker_0",
                    })
            elif msg["type"] == "SessionClosed":
                audio_duration = msg.get("audio_duration_seconds", 0.0)
                session_closed = True
                break
            elif msg["type"] == "Error":
                raise RuntimeError(f"BW STT error {msg['code']}: {msg['message']}")

        sender.join(timeout=10)
        if send_error:
            raise RuntimeError(f"BW STT send failed: {send_error[0]}") from send_error[0]
        if not session_closed:
            # A clean socket close ends the iterator without raising; returning
            # here would cache a truncated transcript as final output.
            raise RuntimeError(
                f"BW STT session ended without SessionClosed — transcript incomplete "
                f"({len(all_words)} words received). Not caching; retry the transcription."
            )

    return {
        "words": all_words,
        "text": "".join(text_parts),
        "audio_duration_seconds": audio_duration,
    }


def transcript_path(edit_dir: Path, video: Path, audio_track: int = 0) -> Path:
    """Where a video's transcript lands.

    The track belongs in the name, or a rerun with --audio-track hands back the transcript of
    the track it is meant to replace. Track 0 keeps the plain name, so transcripts made before
    the flag existed stay valid. Batch mode tests its cache with this too — one function, so
    the two cannot drift apart.
    """
    suffix = "" if audio_track == 0 else f".track{audio_track}"
    return edit_dir / "transcripts" / f"{video.stem}{suffix}.json"


def cached_provider(path: Path) -> str:
    """Which provider produced the transcript at *path*.

    Files written before the provider field existed came from ElevenLabs
    (the only backend at the time), so a missing field means "elevenlabs".
    An unreadable or corrupt file returns "" so it never matches a provider
    and gets re-transcribed.
    """
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return payload.get("provider", "elevenlabs")


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
    verbose: bool = True,
    audio_track: int = 0,
    provider: str = "elevenlabs",
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached: returns existing path immediately if a transcript from the same
    provider already exists. A cached file from a different provider is
    re-transcribed and overwritten.
    """
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcript_path(edit_dir, video, audio_track)

    if out_path.exists():
        prev = cached_provider(out_path)
        if prev == provider:
            if verbose:
                print(f"cached: {out_path.name}")
            return out_path
        if verbose:
            print(f"  cached {out_path.name} is from "
                  f"{prev or 'an unreadable file'}; re-transcribing with {provider}",
                  flush=True)

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
                f"(peak {peak:.1f} dBFS) - not uploading. "
                + (f"The file has {n_tracks} audio tracks; try --audio-track "
                   + " or ".join(str(i) for i in range(n_tracks) if i != audio_track) + "."
                   if n_tracks > 1 else "Check the source audio.")
            )

        size_mb = audio.stat().st_size / (1024 * 1024)

        if provider == "elevenlabs":
            if verbose:
                print(f"  uploading {video.stem}.wav ({size_mb:.1f} MB) to ElevenLabs Scribe",
                      flush=True)
            payload = call_scribe(audio, api_key, language, num_speakers)
        else:  # bw_stt
            if verbose:
                if language:
                    print("  note: BW Labs STT auto-detects language; --language is ignored",
                          flush=True)
                if num_speakers:
                    print("  note: BW Labs STT is single-channel; --num-speakers is ignored",
                          flush=True)
                print(f"  streaming {video.stem}.wav ({size_mb:.1f} MB) to BW Labs STT",
                      flush=True)
            payload = _call_bw_stt(audio, api_key)

    if isinstance(payload, dict):
        payload["provider"] = provider  # cache identity — see cached_provider()

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
        description="Transcribe a video with ElevenLabs Scribe or BW Labs STT"
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
        help="ISO language code (e.g., 'en'). ElevenLabs only; omit to auto-detect.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Number of speakers when known. ElevenLabs only; improves diarization.",
    )
    ap.add_argument(
        "--audio-track",
        type=int,
        default=0,
        help="Zero-based audio track to transcribe. OBS writes the game on track 0 "
             "and the mic on track 1; without this ffmpeg applies its default audio "
             "stream selection, which picks the track with the most channels.",
    )
    ap.add_argument(
        "--provider",
        choices=PROVIDERS,
        default=None,
        help=(
            "Transcription backend (default: auto-detect from available API keys; "
            "ElevenLabs wins if both are set). Override globally with "
            "TRANSCRIBE_PROVIDER env var."
        ),
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    provider = resolve_provider(args.provider)
    api_key = load_api_key(provider)

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        api_key=api_key,
        language=args.language,
        num_speakers=args.num_speakers,
        audio_track=args.audio_track,
        provider=provider,
    )


if __name__ == "__main__":
    main()
