"""Check early failures and the public check only flow with disposable media."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helpers"))
import check_edit as checks
from check_env import CheckResult


# Provide a complete edit while keeping each unit test independent of installed tools
@pytest.fixture
def edit(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original")
    edl = tmp_path / "edl.json"
    edl.write_text(json.dumps({"version": 1, "sources": {"s": "source.mp4"},
                               "ranges": [{"source": "s", "start": 0, "end": 1}]}))
    monkeypatch.setattr(checks, "check_media_tool", lambda name: CheckResult(name, True, "available"))
    monkeypatch.setattr(checks, "probe", lambda path: {"streams": [{"codec_type": "video", "duration": "2"}, {"codec_type": "audio"}]})
    filters = "\n".join(f" ... {name} V->V" for name in ["scale", "afade", "loudnorm", "subtitles", "overlay", "setpts", "null", "eq"])
    encoders = " V..... libx264 description\n A..... aac description"
    monkeypatch.setattr(checks, "command", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, encoders if "-encoders" in args else filters, ""))
    return edl, tmp_path / "final.mp4", source


# Change one declaration without changing the rest of the valid fixture
def change(edl, **fields):
    data = json.loads(edl.read_text())
    data.update(fields)
    edl.write_text(json.dumps(data))


# Valid checks neither render clips nor modify original inputs
def test_valid_edit_is_read_only(edit):
    edl, out, source = edit
    before = {p.name: p.read_bytes() for p in edl.parent.iterdir()}
    assert checks.check_edit(edl, out)["ok"]
    assert {p.name: p.read_bytes() for p in edl.parent.iterdir()} == before


# Missing media and bounds violations are reported before expensive rendering
def test_missing_source_and_out_of_bounds(edit):
    edl, out, source = edit
    change(edl, ranges=[{"source": "s", "start": 0, "end": 3}])
    assert not checks.check_edit(edl, out)["ok"]
    source.unlink()
    assert "missing" in json.dumps(checks.check_edit(edl, out)).lower()
    assert not out.exists()


# Invalid edit shapes never call media probes
@pytest.mark.parametrize("fields", [
    {"version": 2}, {"version": True}, {"ranges": []}, {"sources": []},
    {"ranges": [{"source": "../s", "start": 0, "end": 1}]},
    {"ranges": [{"source": "s", "start": True, "end": 1}]},
    {"ranges": [{"source": "s", "start": 2, "end": 1}]},
    {"ranges": [{"source": "s", "start": 0, "end": float("inf")}]},
    {"overlays": [{"file": "source.mp4", "start_in_output": 0, "duration": 3}]},
])
def test_invalid_plan_stops_before_probe(edit, monkeypatch, fields):
    edl, out, _ = edit
    change(edl, **fields)
    monkeypatch.setattr(checks, "probe", lambda path: pytest.fail("must validate first"))
    report = checks.check_edit(edl, out)
    assert not report["ok"] and report["checks"][0]["name"] == "edit"


# Source audio is required by the current legacy renderer
def test_silent_source_is_rejected(edit, monkeypatch):
    edl, out, _ = edit
    monkeypatch.setattr(checks, "probe", lambda path: {"streams": [{"codec_type": "video", "duration": "2"}]})
    report = checks.check_edit(edl, out)
    assert not report["ok"]
    assert "requires source audio" in json.dumps(report)


# Existing output data survives checks including links to sources
def test_existing_output_and_missing_parent(edit):
    edl, out, source = edit
    out.symlink_to(source)
    assert not checks.check_edit(edl, out)["ok"]
    assert source.read_bytes() == b"original"
    assert not checks.check_edit(edl, out.parent / "missing" / "final.mp4")["ok"]
    assert not (out.parent / "missing").exists()


# Disabled captions do not require a caption file or fonts
def test_caption_opt_out(edit, monkeypatch):
    edl, out, _ = edit
    change(edl, subtitles="absent.srt")
    monkeypatch.setattr(checks, "caption_font", lambda: "test font")
    assert not checks.check_edit(edl, out)["ok"]
    monkeypatch.setattr(checks, "caption_font", lambda: pytest.fail("captions disabled"))
    assert checks.check_edit(edl, out, no_subtitles=True)["ok"]


# Missing fonts have a specific failure instead of a late caption render error
def test_caption_font_failure(edit, monkeypatch):
    edl, out, _ = edit
    (edl.parent / "words.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    change(edl, subtitles="words.srt")

    # Simulate a system with libass but no installed usable fonts
    def no_font():
        raise ValueError("Install a system font")

    monkeypatch.setattr(checks, "caption_font", no_font)
    report = checks.check_edit(edl, out)
    assert not report["ok"]
    assert any(r["name"] == "caption font" and not r["ok"] for r in report["checks"])


# Missing transcripts warn as the renderer skips those segments; malformed ones fail
def test_build_subtitles_validates_transcripts(edit, monkeypatch):
    edl, out, _ = edit
    monkeypatch.setattr(checks, "caption_font", lambda: "test font")
    report = checks.check_edit(edl, out, build_subtitles=True)
    assert report["ok"] and any("Missing transcript" in w for w in report["warnings"])
    folder = edl.parent / "transcripts"
    folder.mkdir()
    (folder / "s.json").write_text(json.dumps({"words": [{"type": "word", "start": 0, "end": 0.4, "text": "Hi"}]}))
    assert checks.check_edit(edl, out, build_subtitles=True)["ok"]
    (folder / "s.json").write_text('{"words": "invalid"}')
    assert not checks.check_edit(edl, out, build_subtitles=True)["ok"]


@pytest.mark.parametrize("duration", [None, "N/A", "nan", "inf", "0", "-1"])
def test_format_duration_checks_bounds_when_stream_duration_is_unusable(edit, monkeypatch, duration):
    edl, out, _ = edit
    monkeypatch.setattr(checks, "probe", lambda path: {
        "streams": [{"codec_type": "video", "duration": duration}, {"codec_type": "audio"}],
        "format": {"duration": "2"},
    })
    change(edl, ranges=[{"source": "s", "start": 0, "end": 3}])
    report = checks.check_edit(edl, out)
    assert not report["ok"]
    assert any(r["name"] == "range 0" and not r["ok"] for r in report["checks"])


def test_unreadable_existing_subtitles_fail(edit, monkeypatch):
    edl, out, _ = edit
    captions = (edl.parent / "words.srt").resolve()
    captions.write_text("captions")
    change(edl, subtitles="words.srt")
    real_open = Path.open

    def unreadable(path, *args, **kwargs):
        if path == captions:
            raise PermissionError("captions are unreadable")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", unreadable)
    monkeypatch.setattr(checks, "caption_font", lambda: "test font")
    report = checks.check_edit(edl, out)
    assert not report["ok"]
    assert any(r["name"] == "subtitles" and "unreadable" in r["detail"] for r in report["checks"])


def test_real_caption_font_probe():
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required")
    filters = checks.command(["ffmpeg", "-hide_banner", "-filters"]).stdout
    if not any(len(row.split()) > 1 and row.split()[1] == "subtitles" for row in filters.splitlines()):
        pytest.skip("FFmpeg with libass required")
    assert checks.caption_font()


# Missing encoders or filters produce actionable environment failures
def test_missing_media_capabilities(edit, monkeypatch):
    edl, out, _ = edit
    monkeypatch.setattr(checks, "command", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""))
    report = checks.check_edit(edl, out)
    assert not report["ok"]
    assert "libx264" in json.dumps(report) and "loudnorm" in json.dumps(report)


# Probing repeated cuts still reads source metadata once
def test_probe_once_per_source(edit, monkeypatch):
    edl, out, _ = edit
    data = json.loads(edl.read_text())
    change(edl, ranges=data["ranges"] * 3)
    calls = []

    # Record real probe requests while returning a stable stream contract
    def probe(path):
        calls.append(path)
        return {"streams": [{"codec_type": "video", "duration": "2"}, {"codec_type": "audio"}]}

    monkeypatch.setattr(checks, "probe", probe)
    assert checks.check_edit(edl, out)["ok"] and len(calls) == 1


# A hung tool fails with a bounded diagnostic
def test_command_timeout(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffprobe", 30)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ValueError, match="exceeded 30 seconds"):
        checks.command(["ffprobe"])


# The public renderer check path works on real media without publishing an export
def test_real_render_check_cli(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe required")
    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=160x90:r=25:d=1",
                    "-f", "lavfi", "-i", "sine=duration=1", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)], check=True, timeout=30)
    edl = tmp_path / "edl.json"
    edl.write_text(json.dumps({"sources": {"s": "source.mp4"}, "ranges": [{"source": "s", "start": 0, "end": 0.8}]}))
    out = tmp_path / "final.mp4"
    result = subprocess.run([sys.executable, str(ROOT / "helpers/render.py"), str(edl), "-o", str(out), "--check"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not out.exists() and not (tmp_path / "clips_graded").exists()
    change(edl, ranges=[{"source": "s", "start": 0, "end": 3}])
    result = subprocess.run([sys.executable, str(ROOT / "helpers/render.py"), str(edl), "-o", str(out)], capture_output=True, text=True, timeout=60)
    assert result.returncode != 0 and not (tmp_path / "clips_graded").exists()
