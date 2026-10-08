"""Prove reuse and invalidation independently of FFmpeg measurement behavior."""

import json
import os
from pathlib import Path
import sys
import subprocess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))
from analysis_cache import AnalysisCache
import analyze


# A complete saved measurement must be reused without invoking its provider again
def test_result_reuse_and_damage(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"first")
    cache = AnalysisCache(source, tmp_path / "cache", {"tool": "1"})
    data, first = cache.get("metadata", {}, lambda: {"duration": 2})
    data, second = cache.get("metadata", {}, lambda: pytest.fail("unnecessary work"))
    assert data == {"duration": 2} and second["reused"] and not first["reused"]
    path = Path(first["record"])
    stored = json.loads(path.read_text())
    stored["result"]["duration"] = 9
    path.write_text(json.dumps(stored))
    data, rebuilt = cache.get("metadata", {}, lambda: {"duration": 3})
    assert data["duration"] == 3 and not rebuilt["reused"]
    path.write_text('{"partial":')
    assert not cache.get("metadata", {}, lambda: {})[1]["reused"]


# File content settings and tool versions all select a new measurement
def test_invalidation_even_with_preserved_mtime(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"first")
    directory = tmp_path / "cache"
    AnalysisCache(source, directory, {}).get("picture", {"threshold": 0.3}, lambda: {})
    assert not AnalysisCache(source, directory, {}).get("picture", {"threshold": 0.4}, lambda: {})[1]["reused"]
    assert not AnalysisCache(source, directory, {"tool": 2}).get("picture", {"threshold": 0.3}, lambda: {})[1]["reused"]
    before = source.stat()
    source.write_bytes(b"other")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert not AnalysisCache(source, directory, {}).get("picture", {"threshold": 0.3}, lambda: {})[1]["reused"]


# Failed providers and changing sources cannot create reusable records
def test_failure_and_source_mutation(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"first")
    directory = tmp_path / "cache"
    cache = AnalysisCache(source, directory, {})

    # Simulate a provider that dies after doing some work
    def fail():
        raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        cache.get("picture", {}, fail)
    assert not list(directory.iterdir())

    # Simulate an input being replaced while its analysis is in flight
    def mutate():
        source.write_bytes(b"changed")
        return {"frames": 20}

    with pytest.raises(RuntimeError, match="Source changed"):
        cache.get("picture", {}, mutate)
    assert not list(directory.iterdir())


# Sources and symbolic links remain outside generated cache writes
def test_cache_path_protection(tmp_path):
    folder = tmp_path / "cache"
    folder.mkdir()
    source = folder / "source"
    source.write_bytes(b"original")
    with pytest.raises(ValueError, match="outside"):
        AnalysisCache(source, folder, {})
    other = tmp_path / "other"
    other.symlink_to(folder, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        AnalysisCache(source, other, {})
    cache = AnalysisCache(source, tmp_path / "records", {})
    _, receipt = cache.get("metadata", {}, lambda: {})
    record = Path(receipt["record"])
    record.unlink()
    record.symlink_to(source)
    with pytest.raises(ValueError, match="symbolic"):
        cache.get("metadata", {}, lambda: {})
    assert source.read_bytes() == b"original"


# Nonfinite provider output is never published as a usable JSON record
def test_nonfinite_result(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"original")
    cache = AnalysisCache(source, tmp_path / "cache", {})
    with pytest.raises(ValueError):
        cache.get("metadata", {}, lambda: {"score": float("nan")})
    assert not list(cache.directory.iterdir())


# Audio settings must invalidate only the audio provider and queries do no media work
def test_provider_reuse_and_queries(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_bytes(b"original")
    calls = []
    monkeypatch.setattr(analyze, "metadata", lambda *args: calls.append("metadata") or {"streams": [{"codec_type": "audio"}, {"codec_type": "video"}]})
    monkeypatch.setattr(analyze, "picture", lambda *args: calls.append("picture") or {"scenes": [{"time": 1, "score": 0.5}], "motion": []})
    monkeypatch.setattr(analyze, "silence", lambda *args: calls.append("silence") or {"intervals": [{"start": 0, "end": 1}, {"start": 2, "end": 3}]})
    args = (source, tmp_path / "cache", ["scenes", "silence"])
    analyze.analyze(*args, runtime={})
    result = analyze.analyze(*args, runtime={}, noise_db=-40)
    assert calls == ["metadata", "picture", "silence", "silence"]
    query = analyze.summary(result, start=1, end=3, limit=1)
    assert query["silence"]["matching"] == 1 and query["scenes"]["matching"] == 1
    assert query["reuse"]["picture"]["reused"]
    with pytest.raises(ValueError, match="end"):
        analyze.summary(result, 3, 1)


# Audio free footage produces a clear empty silence observation
def test_no_audio_and_invalid_settings(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_bytes(b"original")
    monkeypatch.setattr(analyze, "metadata", lambda *args: {"streams": [{"codec_type": "video"}]})
    monkeypatch.setattr(analyze, "silence", lambda *args: pytest.fail("no audio to decode"))
    result = analyze.analyze(source, tmp_path / "cache", ["silence"], runtime={})
    assert result["results"]["silence"]["note"] == "Source has no audio stream"
    with pytest.raises(ValueError):
        analyze.analyze(source, tmp_path / "unused", ["motion"], motion_window=float("nan"), runtime={})
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_nonregular_cache_record_fails_without_hanging(tmp_path, kind):
    source = tmp_path / "source"
    source.write_bytes(b"original")
    cache = AnalysisCache(source, tmp_path / "cache", {})
    _, receipt = cache.get("metadata", {}, lambda: {})
    path = Path(receipt["record"])
    path.unlink()
    if kind == "directory":
        path.mkdir()
    elif hasattr(os, "mkfifo"):
        os.mkfifo(path)
    else:
        pytest.skip("named pipes unavailable")
    script = """import sys
from analysis_cache import AnalysisCache
try:
    AnalysisCache(sys.argv[1], sys.argv[2], {}).get('metadata', {}, lambda: {})
except ValueError as exc:
    assert 'regular files' in str(exc)
else:
    raise AssertionError('nonregular record was accepted')
"""
    result = subprocess.run([sys.executable, "-c", script, str(source), str(cache.directory)],
                            cwd=Path(__file__).resolve().parents[1] / "helpers",
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
