"""Local speech to text for machines without an ElevenLabs key.

Runs Whisper large-v3-turbo through mlx-whisper on Apple Silicon or through
faster-whisper (CTranslate2) everywhere else, and returns the same transcript
shape the rest of the pipeline reads: a top-level ``words`` list of
``{"type": "word", "text", "start", "end", "speaker_id"}`` entries.

The library is chosen from a hardware probe so a machine only downloads the
one model it will use. Nothing here installs packages: a missing library exits
with the exact install command, and model weights are fetched once, pinned to
a commit, into the standard Hugging Face cache.

What the local engine does not provide: speaker labels, audio event tags such
as laughter, and ``spacing`` entries. Fillers are kept on a best effort basis
through a verbatim prompt. Whisper reports word ends well but folds each pause
into the start of the following word, so word starts are moved forward to the
first audible energy frame before the transcript is written.

Usage:
    python helpers/local_stt.py probe
    python helpers/local_stt.py transcribe <audio.wav> -o <out.json> [--language en]
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path


LIBRARIES = ("mlx-whisper", "faster-whisper")

# import name and pyproject extra for each supported library
LIBRARY_INFO = {
    "mlx-whisper": {"module": "mlx_whisper", "extra": "stt-mlx"},
    "faster-whisper": {"module": "faster_whisper", "extra": "stt-cpu"},
}

# one pinned whisper large v3 turbo conversion per library
MODELS = {
    "mlx-whisper": {
        "repo": "mlx-community/whisper-large-v3-turbo",
        "revision": "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
        "size_gb": 1.6,
    },
    "faster-whisper": {
        "repo": "deepdml/faster-whisper-large-v3-turbo-ct2",
        "revision": "4df90f75321148c3a29a9e2351b7ddf8f5b115a8",
        "size_gb": 1.6,
    },
}

# a filler rich prompt nudges whisper toward verbatim output instead of a cleaned up transcript
VERBATIM_PROMPT = "Umm, let me think like, hmm... Okay, here's what I'm, like, thinking."

# segments whisper itself marks as probably silent are dropped above this probability
NO_SPEECH_THRESHOLD = 0.6
# onset trimming works on ten millisecond energy frames
ONSET_FRAME_SECONDS = 0.01
# a word starts where its energy first reaches this share of its own peak
ONSET_RATIO = 0.2
# a trimmed word always keeps at least this much duration
ONSET_MIN_KEEP = 0.05
# energy frames read per pass so a long take never sits in memory at once
ENERGY_READ_FRAMES = 4096
# no real word lasts this long so longer ones are decoder artifacts
MAX_WORD_SECONDS = 3.0
# the same word this many times in a row is a repetition loop
MAX_REPEATS = 4


# describe the machine without importing any model library or touching the network
def probe() -> dict:
    system = platform.system()
    machine = platform.machine()
    installed = {
        name: importlib.util.find_spec(info["module"]) is not None
        for name, info in LIBRARY_INFO.items()
    }
    return {
        "system": system,
        "machine": machine,
        "apple_silicon": system == "Darwin" and machine == "arm64",
        "cuda": shutil.which("nvidia-smi") is not None,
        "memory_gb": memory_gb(),
        "free_disk_gb": free_disk_gb(hf_cache_dir()),
        "python": platform.python_version(),
        "installed": installed,
    }


# physical memory in gigabytes or none where the platform does not expose it
def memory_gb() -> float | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):
        return None
    return round(pages * page_size / 1024**3, 1)


# free space in gigabytes at the nearest existing parent of a path
def free_disk_gb(path: Path) -> float:
    probe_path = path
    while not probe_path.exists() and probe_path != probe_path.parent:
        probe_path = probe_path.parent
    return round(shutil.disk_usage(probe_path).free / 1024**3, 1)


# where huggingface_hub stores snapshots honoring the usual environment overrides
def hf_cache_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).expanduser()
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


# pick the library for this machine unless a cli override names one
def choose_library(info: dict, override: str | None = None) -> str:
    if override:
        if override not in LIBRARIES:
            raise SystemExit(f"unknown library {override!r}; choose from {', '.join(LIBRARIES)}")
        return override
    return "mlx-whisper" if info.get("apple_silicon") else "faster-whisper"


# the exact commands that add a library to this checkout with the cuda runtime libraries when a gpu is present
def install_command(library: str, cuda: bool = False) -> str:
    extra = "stt-cuda" if library == "faster-whisper" and cuda else LIBRARY_INFO[library]["extra"]
    return f"uv sync --extra {extra}    (or: pip install -e '.[{extra}]')"


# libraries already settled in this process keyed by the override that asked for them
_PREFLIGHTED: dict[str | None, str] = {}


# settle the library before any audio work so a missing install fails first and later calls are free
def preflight(library: str | None = None) -> str:
    if library not in _PREFLIGHTED:
        info = probe()
        chosen = choose_library(info, library)
        require_library(chosen, info["cuda"])
        _PREFLIGHTED[library] = chosen
    return _PREFLIGHTED[library]


# true only when ctranslate2 can see a cuda device with its runtime libraries loaded
def cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except (ImportError, RuntimeError):
        return False


# import the library or exit with the install command so nothing expensive starts
def require_library(library: str, cuda: bool = False):
    module = LIBRARY_INFO[library]["module"]
    try:
        return importlib.import_module(module)
    except ImportError:
        raise SystemExit(
            f"{library} is not installed. From the video-use repo run:\n"
            f"  {install_command(library, cuda)}"
        ) from None


# download the pinned model once and return its local path and a label for the transcript
def ensure_model(library: str, model: str | None = None) -> tuple[Path, str]:
    if model and Path(model).expanduser().exists():
        path = Path(model).expanduser().resolve()
        return path, str(path)
    if model:
        repo, revision = model, None
    else:
        repo, revision = MODELS[library]["repo"], MODELS[library]["revision"]
    from huggingface_hub import snapshot_download

    path = Path(snapshot_download(repo_id=repo, revision=revision))
    label = f"{repo}@{revision[:12]}" if revision else repo
    return path, label


# decide whether the verbatim prompt applies for the requested language
def verbatim_prompt_for(language: str | None, enabled: bool) -> str | None:
    if not enabled:
        return None
    if language is None or language.lower().startswith("en"):
        return VERBATIM_PROMPT
    return None


# run mlx whisper and return plain segment dicts plus the detected language
def run_mlx_whisper(wav: Path, model_path: Path, language: str | None, prompt: str | None) -> tuple[list[dict], str | None]:
    mlx_whisper = require_library("mlx-whisper")
    result = mlx_whisper.transcribe(
        str(wav),
        path_or_hf_repo=str(model_path),
        language=language,
        word_timestamps=True,
        initial_prompt=prompt,
        condition_on_previous_text=False,
        verbose=None,
    )
    segments = [
        {
            "no_speech_prob": float(segment.get("no_speech_prob", 0.0)),
            "words": [
                {"text": w.get("word", ""), "start": w.get("start"), "end": w.get("end")}
                for w in segment.get("words", [])
            ],
        }
        for segment in result.get("segments", [])
    ]
    return segments, result.get("language")


# faster whisper models are kept for the life of the process so batch runs load once
_FASTER_MODELS: dict[str, object] = {}


# run faster whisper and return plain segment dicts plus the detected language
def run_faster_whisper(wav: Path, model_path: Path, language: str | None, prompt: str | None) -> tuple[list[dict], str | None]:
    faster_whisper = require_library("faster-whisper")
    key = str(model_path)
    if key not in _FASTER_MODELS:
        device = "cuda" if cuda_available() else "cpu"
        if device == "cpu" and shutil.which("nvidia-smi") is not None:
            print("  nvidia gpu found but the cuda runtime libraries are missing; running on cpu "
                  f"({install_command('faster-whisper', cuda=True)})", flush=True)
        _FASTER_MODELS[key] = faster_whisper.WhisperModel(
            key,
            device=device,
            compute_type="float16" if device == "cuda" else "int8",
        )
    model = _FASTER_MODELS[key]
    raw_segments, info = model.transcribe(
        str(wav),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        initial_prompt=prompt,
        condition_on_previous_text=False,
    )
    segments = [
        {
            "no_speech_prob": float(getattr(segment, "no_speech_prob", 0.0)),
            "words": [
                {"text": w.word, "start": w.start, "end": w.end}
                for w in (segment.words or [])
            ],
        }
        for segment in raw_segments
    ]
    return segments, getattr(info, "language", None)


RUNNERS = {"mlx-whisper": run_mlx_whisper, "faster-whisper": run_faster_whisper}


# turn library segments into the canonical word list dropping silent segments and empty words
def words_from_segments(segments: list[dict], no_speech_threshold: float = NO_SPEECH_THRESHOLD) -> list[dict]:
    words: list[dict] = []
    for segment in segments:
        if float(segment.get("no_speech_prob") or 0.0) > no_speech_threshold:
            continue
        for item in segment.get("words", []):
            text = (item.get("text") or "").strip()
            start = item.get("start")
            end = item.get("end")
            if not text or start is None or end is None:
                continue
            start = float(start)
            end = float(end)
            if end <= start:
                continue
            words.append({"type": "word", "text": text, "start": start, "end": end, "speaker_id": None})
    return words


# casefolded letters and digits of a word in any script for repeat and prompt echo checks
def _plain(text: str) -> str:
    return re.sub(r"[^\w']+", "", text.casefold())


# drop decoder artifacts that survive the segment filter
def clean_words(words: list[dict], prompt: str | None = None) -> list[dict]:
    kept: list[dict] = []
    repeats = 0
    for word in words:
        if word["end"] - word["start"] > MAX_WORD_SECONDS:
            continue
        if kept and _plain(word["text"]) == _plain(kept[-1]["text"]):
            repeats += 1
            if repeats >= MAX_REPEATS:
                continue
        else:
            repeats = 0
        kept.append(word)
    if prompt:
        kept = remove_prompt_echo(kept, prompt)
    return kept


# remove any run of words that repeats the verbatim prompt token for token
def remove_prompt_echo(words: list[dict], prompt: str) -> list[dict]:
    tokens = [_plain(token) for token in prompt.split()]
    tokens = [token for token in tokens if token]
    if not tokens:
        return words
    plain = [_plain(word["text"]) for word in words]
    drop: set[int] = set()
    index = 0
    while index + len(tokens) <= len(plain):
        if plain[index:index + len(tokens)] == tokens:
            drop.update(range(index, index + len(tokens)))
            index += len(tokens)
        else:
            index += 1
    return [word for position, word in enumerate(words) if position not in drop]


# root mean square energy of a mono sixteen bit wav in fixed frames read in chunks so long takes never load at once
def frame_energy(wav: Path, frame_seconds: float = ONSET_FRAME_SECONDS):
    import wave

    import numpy as np

    chunks = []
    with wave.open(str(wav), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise ValueError(f"{wav} must be mono 16 bit pcm for onset trimming")
        hop = max(1, int(handle.getframerate() * frame_seconds))
        # every read is a whole number of frames so chunk edges never split a frame
        while data := handle.readframes(hop * ENERGY_READ_FRAMES):
            pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            count = len(pcm) // hop
            if count == 0:
                break
            chunks.append(np.sqrt(np.mean(pcm[: count * hop].reshape(count, hop) ** 2, axis=1)))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks)


# move each word start forward to its first audible frame because whisper folds pauses into the next word
def trim_onsets(words: list[dict], energy, frame_seconds: float = ONSET_FRAME_SECONDS) -> list[dict]:
    trimmed: list[dict] = []
    for word in words:
        start, end = word["start"], word["end"]
        first = int(start / frame_seconds)
        last = max(first + 1, int(end / frame_seconds))
        window = [float(value) for value in energy[first:last]]
        peak = max(window, default=0.0)
        if peak <= 0.0:
            trimmed.append(dict(word))
            continue
        threshold = peak * ONSET_RATIO
        offset = next(index for index, value in enumerate(window) if value >= threshold)
        onset = (first + offset) * frame_seconds
        new_start = min(max(start, onset), end - ONSET_MIN_KEEP)
        item = dict(word)
        item["start"] = round(max(start, new_start), 3)
        trimmed.append(item)
    return trimmed


# assemble the transcript payload the pipeline reads
def build_payload(library: str, model_label: str, language: str | None, words: list[dict]) -> dict:
    return {
        "engine": "local",
        "library": library,
        "model": model_label,
        "language_code": language,
        "text": " ".join(word["text"] for word in words),
        "words": words,
    }


# transcribe a mono wav with the chosen library and return the canonical payload
def transcribe_wav(
    wav: Path,
    library: str | None = None,
    language: str | None = None,
    model: str | None = None,
    verbatim: bool = True,
) -> dict:
    library = preflight(library)
    model_path, model_label = ensure_model(library, model)
    runner = RUNNERS[library]
    prompt = verbatim_prompt_for(language, verbatim)
    segments, detected = runner(wav, model_path, language, prompt)
    # the english prompt biases language detection so a non english result is redone without it
    if prompt and language is None and detected and not detected.lower().startswith("en"):
        prompt = None
        segments, detected = runner(wav, model_path, detected, None)
    words = clean_words(words_from_segments(segments), prompt)
    if words:
        words = trim_onsets(words, frame_energy(wav))
    return build_payload(library, model_label, language or detected, words)


# cli entry point with a probe subcommand and a transcribe subcommand
def main() -> None:
    ap = argparse.ArgumentParser(description="Local Whisper transcription")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("probe", help="Print the hardware probe and the library this machine would use")
    run = sub.add_parser("transcribe", help="Transcribe a mono 16 kHz wav to transcript json")
    run.add_argument("audio", type=Path)
    run.add_argument("-o", "--output", type=Path, required=True)
    run.add_argument("--language", default=None, help="ISO language code; omit to auto-detect")
    run.add_argument("--library", choices=LIBRARIES, default=None, help="Override the probed library")
    run.add_argument("--model", default=None, help="Hugging Face repo id or local model directory")
    run.add_argument("--no-verbatim-prompt", action="store_true", help="Disable the filler-preserving prompt")
    args = ap.parse_args()

    if args.command == "probe":
        info = probe()
        library = choose_library(info)
        info["library"] = library
        info["model"] = MODELS[library]
        info["installed_for_library"] = info["installed"][library]
        info["install_command"] = install_command(library, info["cuda"])
        print(json.dumps(info, indent=2))
        return

    if not args.audio.exists():
        sys.exit(f"audio not found: {args.audio}")
    payload = transcribe_wav(
        args.audio,
        library=args.library,
        language=args.language,
        model=args.model,
        verbatim=not args.no_verbatim_prompt,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2))
    print(f"saved: {args.output} ({len(payload['words'])} words)")


if __name__ == "__main__":
    main()
