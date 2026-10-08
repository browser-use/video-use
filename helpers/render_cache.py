"""Reuse complete rendered clips only when their inputs, settings and tools match."""

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import tempfile


# Hash bytes in bounded memory so large source videos do not need to fit in RAM
def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Include the installed media tools and rendering implementation in cache identity
def runtime_signature():
    tools = {}
    for name in ("ffmpeg", "ffprobe"):
        executable = shutil.which(name)
        if executable is None:
            raise FileNotFoundError(f"{name} is required for render reuse")
        result = subprocess.run([executable, "-version"], capture_output=True, check=True, timeout=20)
        tools[name] = {"path": str(Path(executable).resolve()), "version": result.stdout.decode(errors="replace")}
    libraries = {}
    for name in ("numpy", "Pillow", "opencv-python", "opencv-python-headless", "opencv-contrib-python", "opencv-contrib-python-headless"):
        try:
            libraries[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            libraries[name] = None
    helpers = {path.name: file_digest(path) for path in sorted(Path(__file__).parent.glob("*.py"))}
    return {"tools": tools, "libraries": libraries, "python": platform.python_version(),
            "platform": platform.platform(), "helpers": helpers}


# Capture enough file state to notice replacement or edits even when mtimes are restored
def file_state(path):
    info = Path(path).stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Render input must be a regular file: {path}")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


# Keep completed clips separate from build outputs and verify every copied cache hit
class RenderCache:
    # One cache instance belongs to one render source hashes are shared across its cuts
    def __init__(self, directory, *, runtime=None, protected_inputs=()):
        directory = Path(directory)
        if directory.is_symlink():
            raise ValueError("Render cache directory must not be a symbolic link")
        self.directory = directory.resolve()
        self.protected_inputs = {Path(path).resolve() for path in protected_inputs}
        if any(path.is_relative_to(self.directory) for path in self.protected_inputs):
            raise ValueError("Render sources must be outside the generated cache directory")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.runtime = runtime_signature() if runtime is None else runtime
        self.digests = {}
        self.hits = 0
        self.rendered = 0

    # Reuse a source checksum within this render only while its complete file state matches
    def describe_inputs(self, inputs):
        rows = []
        for path in sorted({Path(value).resolve(strict=True) for value in inputs}):
            if path.is_relative_to(self.directory):
                raise ValueError("Render sources must be outside the generated cache directory")
            state = file_state(path)
            previous = self.digests.get(path)
            if previous is None or previous[0] != state:
                digest = file_digest(path)
                if file_state(path) != state:
                    raise RuntimeError(f"Source changed while being read: {path}")
                self.digests[path] = (state, digest)
            rows.append({"path": str(path), "sha256": self.digests[path][1], "state": state})
        return rows

    # Write a separate output copy so downstream commands cannot modify cached bytes
    def copy_output(self, source, destination, expected_digest):
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".render-copy-", suffix=".mp4", dir=destination.parent)
        temporary = Path(name)
        os.close(descriptor)
        try:
            shutil.copyfile(source, temporary)
            if file_digest(temporary) != expected_digest:
                return False
            if destination.is_symlink():
                raise ValueError("Rendered clip destination must not be a symbolic link")
            os.replace(temporary, destination)
            return True
        finally:
            temporary.unlink(missing_ok=True)

    # Read only complete matching records partial or corrupted entries cause a fresh render
    def restore(self, key, destination):
        media = self.directory / f"{key}.mp4"
        record = self.directory / f"{key}.json"
        for path in (media, record):
            if path.is_symlink():
                raise ValueError(f"Render cache entry must not be a symbolic link: {path.name}")
            if not path.is_file():
                return False
        try:
            if record.stat().st_size > 4096:
                return False
            data = json.loads(record.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1 or data.get("key") != key:
                return False
            if data.get("bytes", 0) <= 0 or data.get("bytes") != media.stat().st_size:
                return False
            digest = data.get("sha256")
            if not isinstance(digest, str) or len(digest) != 64:
                return False
        except (FileNotFoundError, ValueError, TypeError):
            return False
        try:
            return self.copy_output(media, destination, digest)
        except FileNotFoundError:
            if not media.exists():
                return False
            raise

    # Build one clip privately publish only success and leave failed attempts out of the cache
    def get_or_render(self, settings, inputs, destination, render):
        destination = Path(destination).absolute()
        if destination.is_symlink() or destination.resolve().is_relative_to(self.directory):
            raise ValueError("Rendered clip destination must be outside the cache and cannot be a link")
        before = self.describe_inputs(inputs)
        for source in self.protected_inputs | {Path(row["path"]) for row in before}:
            if destination.resolve() == source or (destination.exists() and destination.samefile(source)):
                raise ValueError("Rendered clip must not overwrite a source")
        payload = {"version": 1, "runtime": self.runtime, "settings": settings,
                   "inputs": [{"path": row["path"], "sha256": row["sha256"]} for row in before]}
        key = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if self.restore(key, destination):
            if any(file_state(row["path"]) != row["state"] for row in before):
                raise RuntimeError("Source changed while restoring a cached clip")
            self.hits += 1
            return True
        with tempfile.TemporaryDirectory(prefix=".render-work-", dir=self.directory) as folder:
            stage = Path(folder)
            media = stage / "clip.mp4"
            render(media)
            if media.is_symlink() or not media.is_file() or media.stat().st_size == 0:
                raise ValueError("Renderer did not produce a complete clip file")
            if any(file_state(row["path"]) != row["state"] for row in before):
                raise RuntimeError("Source changed while rendering; clip was not cached")
            digest = file_digest(media)
            record = stage / "clip.json"
            record.write_text(json.dumps({"version": 1, "key": key, "bytes": media.stat().st_size,
                                          "sha256": digest}), encoding="utf-8")
            if not self.copy_output(media, destination, digest):
                raise RuntimeError("Rendered clip changed while copying it")
            # Each replacement is atomic Concurrent writers may yield a mismatched pair
            # readers hash their private copy and treat that pair as a miss never as a hit
            for source, suffix in ((media, ".mp4"), (record, ".json")):
                target = self.directory / (key + suffix)
                if target.is_symlink():
                    raise ValueError("Render cache entry must not be a symbolic link")
                os.replace(source, target)
        self.rendered += 1
        return False

    # Expose work saved in CLI output and optional per-build verification records
    def summary(self):
        return {"reused_clips": self.hits, "rendered_clips": self.rendered}
