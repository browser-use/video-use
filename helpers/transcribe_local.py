"""Transcribe a video locally with whisper.cpp (no ElevenLabs / no API key).

Extracts mono 16 kHz audio via ffmpeg, runs the local whisper.cpp binary, and
writes a Scribe-compatible JSON (word-level timestamps, single speaker) to
<edit_dir>/transcripts/<video_stem>.json so the rest of the video-use pipeline
(pack_transcripts, render --build-subtitles) works unchanged.

Limitations vs. Scribe:
  - no speaker diarization (single speaker_0)
  - no audio-event tags ((laughter), (sigh), ...)
  - accuracy/language quality depends on the whisper model chosen

Usage:
    python helpers/transcribe_local.py <video>
    python helpers/transcribe_local.py <video> --edit-dir /custom/edit
    python helpers/transcribe_local.py <video> --language pt
    python helpers/transcribe_local.py <video> --model /path/to/ggml-medium.bin
    python helpers/transcribe_local.py <video> --audio-track 1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_MODEL_CANDIDATES = [
    Path.home() / ".cache" / "hyperframes" / "whisper" / "models" / "ggml-small.bin",
    Path.home() / ".cache" / "hyperframes" / "whisper" / "models" / "ggml-medium.bin",
    Path.home() / ".cache" / "hyperframes" / "whisper" / "models" / "ggml-base.bin",
]
DEFAULT_CLI_CANDIDATES = [
    Path.home() / ".cache" / "hyperframes" / "whisper" / "whisper.cpp" / "build" / "bin" / "whisper-cli",
    Path("whisper-cli"),
]


def find_whisper_cli() -> Path:
    env = os.environ.get("WHISPER_CLI")
    if env:
        p = Path(env)
        if p.exists():
            return p
    for c in DEFAULT_CLI_CANDIDATES:
        if c.exists():
            return c
    found = shutil.which("whisper-cli") or shutil.which("whisper-cpp")
    if found:
        return Path(found)
    sys.exit("whisper-cli not found. Set WHISPER_CLI or install whisper.cpp.")


def find_model(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if p.exists():
            return p
        sys.exit(f"whisper model not found: {p}")
    env = os.environ.get("WHISPER_MODEL")
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p
    for c in DEFAULT_MODEL_CANDIDATES:
        if c.exists():
            return c
    sys.exit(
        "no whisper model found. Download one (e.g. ggml-small.bin) and pass --model "
        "or set WHISPER_MODEL."
    )


def count_audio_tracks(video_path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True,
    )
    return len([ln for ln in out.stdout.splitlines() if ln.strip()])


def extract_audio(video_path: Path, dest: Path, audio_track: int = 0) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-map", f"0:a:{audio_track}",
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_whisper(cli: Path, model: Path, audio: Path, out_base: Path, language: str | None) -> Path:
    cmd = [
        str(cli),
        "-m", str(model),
        "-f", str(audio),
        "-ojf",
        "-owts",
        "-of", str(out_base),
    ]
    if language and language.lower() != "auto":
        cmd += ["-l", language]
    else:
        cmd += ["-l", "auto"]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    json_path = Path(str(out_base) + ".json")
    if not json_path.exists():
        sys.exit(f"whisper did not produce {json_path}")
    return json_path


def clean_token(text: str) -> str:
    text = re.sub(r"\[_TT_\d+\]", "", text)
    text = re.sub(r"\[_BEG_\]|\[_EOT_\]", "", text)
    return text.strip()


def tokens_to_words(tokens: list[dict]) -> list[dict]:
    """Group whisper tokens into word-level entries (Scribe shape)."""
    words: list[dict] = []
    current: dict | None = None
    for tok in tokens:
        raw = tok.get("text", "")
        txt = clean_token(raw)
        if not txt:
            continue
        start = tok["offsets"]["from"] / 1000.0
        end = tok["offsets"]["to"] / 1000.0
        # A leading space marks the start of a new word.
        if raw.startswith(" ") and current is not None:
            words.append(current)
            current = {"type": "word", "text": txt, "start": start, "end": end, "speaker_id": "speaker_0"}
        else:
            if current is None:
                current = {"type": "word", "text": txt, "start": start, "end": end, "speaker_id": "speaker_0"}
            else:
                if current["text"]:
                    current["text"] += txt
                else:
                    current["text"] = txt
                current["end"] = end
    if current is not None and current["text"]:
        words.append(current)
    # drop empties
    return [w for w in words if w["text"].strip()]


def transcript_path(edit_dir: Path, video: Path, audio_track: int = 0) -> Path:
    suffix = "" if audio_track == 0 else f".track{audio_track}"
    return edit_dir / "transcripts" / f"{video.stem}{suffix}.json"


def transcribe_one(
    video: Path,
    edit_dir: Path,
    language: str | None = None,
    model: str | None = None,
    audio_track: int = 0,
    verbose: bool = True,
    force: bool = False,
) -> Path:
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcript_path(edit_dir, video, audio_track)

    if out_path.exists() and not force:
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    n_tracks = count_audio_tracks(video)
    if n_tracks > 1 and verbose:
        print(f"  note: {video.name} has {n_tracks} audio tracks, using track {audio_track + 1}")

    t0 = None
    import time
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        audio = tmpdir / f"{video.stem}.wav"
        extract_audio(video, audio, audio_track)

        # silence guard
        peak = 0
        import wave, array, math
        with wave.open(str(audio), "rb") as w:
            while frames := w.readframes(1 << 16):
                samples = array.array("h", frames)
                peak = max(peak, max(samples), -min(samples))
        peak_dbfs = 20 * math.log10(peak / 32768) if peak > 0 else float("-inf")
        if peak_dbfs < -60.0:
            raise RuntimeError(f"track {audio_track + 1} of {video.name} is silent (peak {peak_dbfs:.1f} dBFS)")

        cli = find_whisper_cli()
        mdl = find_model(model)
        if verbose:
            print(f"  whisper: {cli.name}  model: {mdl.name}  lang: {language or 'auto'}")
        json_path = run_whisper(cli, mdl, audio, tmpdir / "transcript", language)
        raw = json.loads(json_path.read_text())

        all_words: list[dict] = []
        for seg in raw.get("transcription", []):
            all_words.extend(tokens_to_words(seg.get("tokens", [])))

        payload = {
            "language_code": (language if language and language.lower() != "auto" else raw.get("result", {}).get("language", "auto")),
            "words": all_words,
            "_source": "whisper.cpp (local)",
            "_model": mdl.name,
        }

    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    dt = time.time() - t0
    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        print(f"    words: {len(all_words)}")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video locally with whisper.cpp")
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument("--edit-dir", type=Path, default=None, help="Edit output directory (default: <video_parent>/edit)")
    ap.add_argument("--language", type=str, default="auto", help="Language code (e.g. 'pt') or 'auto'. Default auto.")
    ap.add_argument("--model", type=str, default=None, help="Path to ggml whisper model (.bin)")
    ap.add_argument("--audio-track", type=int, default=0, help="Zero-based audio track")
    ap.add_argument("--force", action="store_true", help="Re-transcribe even if cached")
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")
    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        language=args.language,
        model=args.model,
        audio_track=args.audio_track,
        force=args.force,
    )


if __name__ == "__main__":
    main()