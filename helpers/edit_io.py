"""Media IO and explicit source validation shared by the editing helpers."""

import hashlib
import json
import subprocess
from pathlib import Path


# run a media command and surface its error output when it fails
def run(args, log=None):
    """Run a media command and surface its error output when it fails."""
    result = subprocess.run([str(x) for x in args], capture_output=True)
    if log is not None:
        Path(log).write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError(
            f"{args[0]} failed ({result.returncode}):\n"
            + result.stderr.decode(errors="replace")[-6000:]
        )
    return result


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
    return json.loads(Path(path).read_text())


# write readable JSON while rejecting nonfinite measurement values
def save_json(path, data, *, exclusive=False):
    """Write readable JSON while rejecting nonfinite measurement values."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, allow_nan=False) + "\n"
    with path.open("x" if exclusive else "w") as stream:
        stream.write(payload)


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
