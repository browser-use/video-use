"""Transcribe a video with ElevenLabs Scribe.

Extracts mono 16kHz audio via ffmpeg, uploads to Scribe with verbatim +
diarize + audio events + word-level timestamps, writes the full response
to <edit_dir>/transcripts/<video_stem>.json.

Cached: if the output file already exists, the upload is skipped.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe.py <video_path> --language en
    python helpers/transcribe.py <video_path> --num-speakers 2
"""

from __future__ import annotations

import argparse
import array
import hashlib
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


def load_api_key() -> str:
    for candidate in [Path(__file__).resolve().parent.parent / ".env", Path(".env")]:
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "ELEVENLABS_API_KEY":
                    return v.strip().strip('"').strip("'")
    v = os.environ.get("ELEVENLABS_API_KEY", "")
    if not v:
        sys.exit("ELEVENLABS_API_KEY not found in .env or environment")
    return v


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


def _source_fingerprint(video: Path) -> dict:
    """Return a small metadata dict used to detect when the source video changed."""
    st = video.stat()
    return {
        "path": str(video.resolve()),
        "mtime_ns": st.st_mtime_ns,
        "size": st.st_size,
    }


def _source_matches(transcript_path: Path, video: Path) -> bool:
    """Return True if the existing transcript was produced from the current
    source video (same path, mtime, size). Transcripts without a _source key
    (written by older versions) are accepted if the path stem matches."""
    try:
        data = json.loads(transcript_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    meta = data.get("_source") if isinstance(data, dict) else None
    if not meta or not isinstance(meta, dict):
        return True
    fp = _source_fingerprint(video)
    return (meta.get("path") == fp["path"]
            and meta.get("mtime_ns") == fp["mtime_ns"]
            and meta.get("size") == fp["size"])


def transcript_path(edit_dir: Path, video: Path, audio_track: int = 0) -> Path:
    """Where a video's transcript lands.

    Names include a short hash of the resolved parent directory so two videos
    with the same stem in different directories (e.g. takes/A/clip.mp4 and
    takes/B/clip.mp4) do not collide during batch transcription. Track 0 keeps
    a stem-only alias if a legacy stem-only file already exists and matches
    the source, so transcripts made before this change stay valid.
    """
    track_suffix = "" if audio_track == 0 else f".track{audio_track}"
    parent_hash = hashlib.sha1(str(video.resolve()).encode("utf-8")).hexdigest()[:8]
    return edit_dir / "transcripts" / f"{video.stem}.{parent_hash}{track_suffix}.json"


def _legacy_transcript_path(edit_dir: Path, video: Path, audio_track: int = 0) -> Path | None:
    """Return the pre-hash transcript path if a matching legacy file exists."""
    track_suffix = "" if audio_track == 0 else f".track{audio_track}"
    legacy = edit_dir / "transcripts" / f"{video.stem}{track_suffix}.json"
    if legacy.exists() and _source_matches(legacy, video):
        return legacy
    return None


def resolve_transcript(transcripts_dir: Path, name: str) -> Path | None:
    """Find a transcript JSON by logical name (video stem or source key).

    Tries in order:
      1. <name>.json (legacy plain name — produced before hashing was added)
      2. <name>.<8-hex-hash>*.json  (new disambiguated name — any parent hash)

    Returns the first match or None. When multiple hashed files exist for the
    same stem (same-named videos in different directories), the most recently
    modified wins; this mirrors how a human would pick when the EDL's source
    key is ambiguous.
    """
    plain = transcripts_dir / f"{name}.json"
    if plain.exists():
        return plain
    matches = sorted(transcripts_dir.glob(f"{name}.*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if matches:
        return matches[0]
    return None


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
    verbose: bool = True,
    audio_track: int = 0,
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached: returns an existing transcript immediately if it was produced
    from the same source video (matched by path + mtime_ns + size). A
    stale transcript (source replaced/overwritten) or a collision with a
    different directory's same-stem video triggers a fresh transcription.
    """
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    legacy = _legacy_transcript_path(edit_dir, video, audio_track)
    if legacy is not None:
        if verbose:
            print(f"cached: {legacy.name}")
        return legacy

    out_path = transcript_path(edit_dir, video, audio_track)

    if out_path.exists() and _source_matches(out_path, video):
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    if out_path.exists() and verbose:
        print(f"  source changed — re-transcribing {video.name}")

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
            print(f"  uploading {video.stem}.wav ({size_mb:.1f} MB)", flush=True)
        payload = call_scribe(audio, api_key, language, num_speakers)

    if isinstance(payload, dict):
        payload["_source"] = _source_fingerprint(video)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        if isinstance(payload, dict) and "words" in payload:
            print(f"    words: {len(payload['words'])}")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video with ElevenLabs Scribe")
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
        help="Optional number of speakers when known. Improves diarization accuracy.",
    )
    ap.add_argument(
        "--audio-track",
        type=int,
        default=0,
        help="Zero-based audio track to transcribe. OBS writes the game on track 0 "
             "and the mic on track 1; without this ffmpeg applies its default audio "
             "stream selection, which picks the track with the most channels.",
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
        num_speakers=args.num_speakers,
        audio_track=args.audio_track,
    )


if __name__ == "__main__":
    main()
