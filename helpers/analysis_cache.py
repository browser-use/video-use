"""Store verified JSON analysis results independently for each source and provider."""

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

try:
    from .edit_io import sha256
except ImportError:
    from edit_io import sha256


MAX_RECORD_BYTES = 64 * 1024 * 1024


# Use one stable finite JSON representation for keys and result checksums
def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


# Notice replaced or edited files even when their modification times are restored
def source_state(path):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Analysis source must be a regular file")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


# One cache session shares a source checksum across independent analysis providers
class AnalysisCache:
    # Keep generated records away from sources and capture their initial identity
    def __init__(self, source, directory, runtime):
        self.source = Path(source).resolve(strict=True)
        directory = Path(directory)
        if directory.is_symlink():
            raise ValueError("Analysis cache directory cannot be a symbolic link")
        self.directory = directory.resolve()
        if self.source.is_relative_to(self.directory):
            raise ValueError("Source must be outside the analysis cache directory")
        self.before = source_state(self.source)
        self.digest = sha256(self.source)
        self.check_source()
        self.runtime = runtime
        self.directory.mkdir(parents=True, exist_ok=True)

    # Refuse to return or publish analysis of a file that changed during the operation
    def check_source(self):
        if source_state(self.source) != self.before:
            raise RuntimeError("Source changed during analysis; run the check again")

    # Reuse only a complete matching record with an intact result checksum
    def get(self, provider, settings, produce):
        identity = {"version": 1, "provider": provider, "settings": settings,
                    "source": str(self.source), "source_sha256": self.digest, "runtime": self.runtime}
        key = hashlib.sha256(encoded(identity)).hexdigest()
        target = self.directory / (key + ".json")
        if target.is_symlink():
            raise ValueError("Analysis cache records cannot be symbolic links")
        self.check_source()
        payload = None
        try:
            descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            pass
        else:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                os.close(descriptor)
                raise ValueError("Analysis cache records must be regular files; choose a clean cache directory")
            with os.fdopen(descriptor, "rb") as stream:
                if info.st_size <= MAX_RECORD_BYTES:
                    payload = stream.read(MAX_RECORD_BYTES + 1)
        try:
            if payload is not None and len(payload) <= MAX_RECORD_BYTES:
                record = json.loads(payload)
                if (isinstance(record, dict) and record.get("identity") == identity
                        and record.get("result_sha256") == hashlib.sha256(encoded(record["result"])).hexdigest()):
                    self.check_source()
                    return record["result"], {"reused": True, "record": str(target)}
        except (FileNotFoundError, ValueError, TypeError, KeyError, OverflowError):
            pass
        result = produce()
        payload = encoded({"identity": identity, "result": result,
                           "result_sha256": hashlib.sha256(encoded(result)).hexdigest()})
        if len(payload) > MAX_RECORD_BYTES:
            raise ValueError("Analysis result exceeds the 64 MiB record limit")
        self.check_source()
        descriptor, name = tempfile.mkstemp(prefix=".analysis-", suffix=".json", dir=self.directory)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
            self.check_source()
            if target.is_symlink():
                raise ValueError("Analysis cache records cannot be symbolic links")
            if target.exists() and not stat.S_ISREG(target.stat().st_mode):
                raise ValueError("Analysis cache records must be regular files; choose a clean cache directory")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return result, {"reused": False, "record": str(target)}
