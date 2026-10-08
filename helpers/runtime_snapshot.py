"""Save a record of core tool files and check whether they have changed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOTS = {"helpers", "skills", "references", "assets", "tests"}
SINGLE = {"SKILL.md", "AGENTS.md", "pyproject.toml", "uv.lock"}
SKIP = {"__pycache__", "node_modules", ".pytest_cache", "media", ".venv", ".git"}


# combine sorted file hashes into one stable record identifier
def _digest(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


# read core file contents without following links or entering unrelated folders
def fingerprint(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Tool root must be a directory")
    paths = [root / name for name in SINGLE]
    for name in sorted(ROOTS):
        folder = root / name
        if folder.is_symlink() or not folder.is_dir():
            continue
        for current, directories, files in os.walk(folder, followlinks=False):
            directories[:] = sorted(d for d in directories if d not in SKIP and not d.startswith(".env") and not (Path(current) / d).is_symlink())
            paths.extend(Path(current) / name for name in files if not name.startswith(".env"))
    files = {}
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        files[path.relative_to(root).as_posix()] = digest.hexdigest()
    return {"sha256": _digest(files), "files": files}


# report added removed or changed tool files against a saved record
def verify_snapshot(expected, root=None):
    if not isinstance(expected, dict) or not isinstance(expected.get("files"), dict):
        raise ValueError("Tool record must contain a files object")
    if not expected["files"] or any(not isinstance(k, str) or not isinstance(v, str) for k, v in expected["files"].items()):
        raise ValueError("Tool record must contain file names and hashes")
    if expected.get("sha256") != _digest(expected["files"]):
        raise ValueError("Tool record checksum does not match its file list")
    actual = fingerprint(root or Path(__file__).resolve().parents[1])
    if actual != expected:
        mismatches = sorted(k for k in set(expected["files"]) | set(actual["files"])
                            if expected["files"].get(k) != actual["files"].get(k))
        raise RuntimeError("Tool files changed: " + ", ".join(mismatches[:20]))
    return actual


# save an explicit tool record or compare an existing one without replacing it
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record")
    record.add_argument("-o", "--output", type=Path, required=True)
    check = commands.add_parser("check")
    check.add_argument("record", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            verify_snapshot(json.loads(args.record.read_text()), args.root)
            print("Tool files match the saved record")
        else:
            root = args.root.resolve(strict=True)
            output = args.output.resolve()
            if output.is_relative_to(root):
                relative = output.relative_to(root)
                if relative.parts and (relative.parts[0] in ROOTS or str(relative) in SINGLE):
                    raise ValueError("Save the record outside the tool files being checked")
            snapshot = fingerprint(root)
            if not snapshot["files"]:
                raise ValueError("No core tool files found in this directory")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(snapshot, indent=2) + "\n")
            print(f"Saved record of {len(snapshot['files'])} tool files to {args.output}")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Tool record: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
