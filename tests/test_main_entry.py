"""Tests for the project-root main.py wrapper.

Only the default-injection logic is unit-tested here; the actual run_job
path is exercised by tests/test_srt_driven_*.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def main_mod(monkeypatch, tmp_path):
    """Fresh import of main.py rooted at a tmp dir so ROOT/input doesn't leak."""
    monkeypatch.chdir(tmp_path)
    # Force-reload main with a new ROOT pointing at tmp_path so file-existence
    # checks reflect what the test wrote, not what's actually in the repo root.
    import importlib
    import main as _m
    importlib.reload(_m)
    _m.ROOT = tmp_path  # rebind so input/source.mp4 etc. resolve in tmp
    return _m


def test_defaults_when_no_flags(main_mod, tmp_path):
    """No flags + nothing in input/ → srt/plan/output defaults, no source/voice."""
    out = main_mod._inject_defaults([])
    assert "--srt" in out and "input/script.srt" in out
    assert "--plan" in out and "input/edit_plan.json" in out
    assert "-o" in out and "output/final.mp4" in out
    # input/source.mp4 doesn't exist → --source NOT injected
    assert "--source" not in out
    assert "--voice" not in out
    # output/ dir was created
    assert (tmp_path / "output").is_dir()


def test_injects_source_when_present(main_mod, tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "source.mp4").write_bytes(b"x")
    out = main_mod._inject_defaults([])
    assert "--source" in out and "input/source.mp4" in out


def test_injects_voice_when_present(main_mod, tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "voice.wav").write_bytes(b"x")
    out = main_mod._inject_defaults([])
    assert "--voice" in out and "input/voice.wav" in out


def test_user_flags_win(main_mod, tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "source.mp4").write_bytes(b"x")
    user = ["--srt", "scripts/ep01.srt",
            "--plan", "plans/ep01.json",
            "--source", "raw/ep01.mp4",
            "-o", "out/ep01.mp4"]
    out = main_mod._inject_defaults(user)
    # User-supplied wins; no duplicate defaults appended
    assert out.count("--srt") == 1 and "scripts/ep01.srt" in out
    assert out.count("--plan") == 1 and "plans/ep01.json" in out
    assert out.count("--source") == 1 and "raw/ep01.mp4" in out
    assert out.count("-o") == 1 and "out/ep01.mp4" in out
    # Default input/script.srt etc. NOT injected
    assert "input/script.srt" not in out
    assert "input/edit_plan.json" not in out


def test_equals_form_recognized(main_mod, tmp_path):
    """--flag=value form must count as 'flag is set' so we don't double-inject."""
    out = main_mod._inject_defaults(["--srt=scripts/x.srt", "--plan=plans/x.json"])
    # Defaults must NOT be appended. Both the user's tokens and any default
    # bare `--srt` / `--plan` would otherwise coexist.
    assert "--srt=scripts/x.srt" in out
    assert "--plan=plans/x.json" in out
    assert "--srt" not in out          # no bare default flag
    assert "--plan" not in out
    assert "input/script.srt" not in out
    assert "input/edit_plan.json" not in out


def test_batch_mode_skips_all_defaults(main_mod, tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "source.mp4").write_bytes(b"x")
    out = main_mod._inject_defaults(["--batch", "jobs.json"])
    # No single-job defaults — manifest owns paths.
    assert "--srt" not in out
    assert "--plan" not in out
    assert "--source" not in out
    assert "-o" not in out
    assert "--output" not in out


def test_short_output_flag_recognized(main_mod, tmp_path):
    out = main_mod._inject_defaults(["-o", "custom/path.mp4"])
    assert out.count("-o") == 1
    assert "output/final.mp4" not in out
