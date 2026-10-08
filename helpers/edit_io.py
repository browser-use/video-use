"""Media IO and explicit source validation shared by the editing helpers."""

import hashlib
import json
import math
import os
import stat
import subprocess
import tempfile
from pathlib import Path


# run a media command and surface its error output when it fails
def run(args, log=None, timeout=600):
    """Run a bounded command, keeping full errors on disk and only a tail in memory."""
    with (Path(log).open("w+b") if log is not None else tempfile.TemporaryFile()) as errors:
        try:
            result = subprocess.run([str(x) for x in args], stdout=subprocess.PIPE,
                                    stderr=errors, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{args[0]} exceeded {timeout:g} seconds") from exc
        errors.flush()
        errors.seek(max(0, errors.tell() - 6000))
        result.stderr = errors.read()
    if result.returncode:
        raise RuntimeError(
            f"{args[0]} failed ({result.returncode}):\n"
            + result.stderr.decode(errors="replace")[-6000:]
        )
    return result


def file_state(path):
    """Notice replacements and edits while a media command is running."""
    info = Path(path).stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("source must be a regular file")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def display_dimensions(stream):
    """Return pixel dimensions after FFmpeg's automatic quarter-turn rotation."""
    rotation = stream.get("tags", {}).get("rotate", 0)
    for row in stream.get("side_data_list", []):
        if "rotation" in row:
            rotation = row["rotation"]
            break
    try:
        rotation = float(rotation)
        if not math.isfinite(rotation) or not math.isclose(rotation / 90, round(rotation / 90), abs_tol=1e-6):
            raise ValueError
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("source rotation must be a quarter turn") from exc
    width, height = stream["width"], stream["height"]
    return (height, width) if round(rotation / 90) % 2 else (width, height)


# read stream metadata and optionally count decoded frames with ffprobe
def probe(path, frames=False):
    """Read stream metadata and optionally count decoded frames with ffprobe."""
    return json.loads(
        run(
            [
                "ffprobe",
                "-v",
                "error",
                *(["-count_frames"] if frames else []),
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                path,
            ]
        ).stdout
    )


# fingerprint file contents in bounded memory
def sha256(path):
    """Fingerprint file contents in bounded memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# read a project or evidence document from disk
def load_json(path):
    """Read a project or evidence document from disk."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


# write readable JSON while rejecting nonfinite measurement values
def save_json(path, data, *, exclusive=False):
    """Write readable JSON while rejecting nonfinite measurement values."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise FileExistsError("JSON output cannot be a symbolic link")
    payload = json.dumps(data, indent=2, allow_nan=False, ensure_ascii=False) + "\n"
    descriptor, name = tempfile.mkstemp(prefix=".json-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
        if path.is_symlink():
            raise FileExistsError("JSON output cannot be a symbolic link")
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


# resolve an artifact path relative to its project directory
def resolve(root, name):
    """Resolve an artifact path relative to its project directory."""
    path = Path(name)
    return path.resolve() if path.is_absolute() else (Path(root) / path).resolve()


# require a declared render source and reject reference-only file aliases
def source_path(manifest, root, source_id):
    """Require a declared render source and reject reference-only file aliases."""
    source = manifest["sources"][source_id]
    if source.get("study_only") or not source.get("provenance"):
        raise ValueError(
            f"{source_id}: render sources need provenance and cannot be study-only"
        )
    path = resolve(root, source["file"])
    if not path.is_file():
        raise FileNotFoundError(path)
    blocked_paths = manifest.get("study_media", [])
    if not isinstance(blocked_paths, list):
        raise ValueError("study_media must be a list of paths")
    for blocked in blocked_paths:
        other = resolve(root, blocked)
        if path == other or (other.exists() and path.samefile(other)):
            raise ValueError("study media entered the render graph")
    return path


# recover the last decodable JSON object from command output
def last_json(text):
    """Recover the last decodable JSON object from command output."""
    candidates = []
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, length = json.JSONDecoder().raw_decode(text[start:])
            candidates.append((start + length, -start, value))
        except json.JSONDecodeError:
            pass
    if not candidates:
        raise ValueError("no JSON measurement in command output")
    return max(candidates, key=lambda row: row[:2])[2]
