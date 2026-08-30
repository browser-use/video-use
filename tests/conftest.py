"""Shared fixtures for srt_driven_edit pytest suite.

Generates session-scoped synthetic media via ffmpeg's lavfi sources so the
real extract/concat/burn pipeline can be exercised without bundling binary
fixtures.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Make the skill's helpers/ importable as a flat package (matches the
# `python helpers/srt_driven_edit.py` invocation contract).
HELPERS = Path(__file__).resolve().parent.parent / "helpers"
sys.path.insert(0, str(HELPERS))


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def pytest_collection_modifyitems(config, items):
    """Auto-skip all tests in this dir if ffmpeg/ffprobe missing."""
    if FFMPEG and FFPROBE:
        return
    marker = pytest.mark.skip(reason="ffmpeg or ffprobe not on PATH")
    for item in items:
        item.add_marker(marker)


# ---------------------------------------------------------------------------
# Synthetic media (session-scoped — each costs a few seconds to render)
# ---------------------------------------------------------------------------


def _ffmpeg(*args: str) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n  cmd: {' '.join(cmd)}\n  stderr: {r.stderr}")


@pytest.fixture(scope="session")
def synth_av(tmp_path_factory) -> Path:
    """30s 1080p@24 testsrc2 + 440Hz sine. Spans long enough for sub-second cuts."""
    d = tmp_path_factory.mktemp("synth")
    out = d / "av.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24:duration=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        "-shortest",
        str(out),
    )
    return out


@pytest.fixture(scope="session")
def synth_v_only(tmp_path_factory) -> Path:
    """30s 1080p video without an audio track. Exercises the auto-degrade path."""
    d = tmp_path_factory.mktemp("synth_vonly")
    out = d / "v_only.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24:duration=30",
        "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-t", "30",
        str(out),
    )
    return out


@pytest.fixture(scope="session")
def synth_voice(tmp_path_factory) -> Path:
    """5s 880Hz sine — drop-in per-segment voice clip."""
    d = tmp_path_factory.mktemp("synth_voice")
    out = d / "voice.wav"
    _ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=880:duration=5",
        "-ar", "48000", "-ac", "2",
        str(out),
    )
    return out


# ---------------------------------------------------------------------------
# Helpers for crafting SRT / plan files inside a test's tmp_path
# ---------------------------------------------------------------------------


def fmt_ts(s: float) -> str:
    total_ms = int(round(s * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    sec, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def write_srt(path: Path, cues: list[tuple[int, float, float, str]],
              encoding: str = "utf-8") -> None:
    """Write an SRT. cues: [(id, start_s, end_s, text)]."""
    lines: list[str] = []
    for cid, s, e, t in cues:
        lines.append(str(cid))
        lines.append(f"{fmt_ts(s)} --> {fmt_ts(e)}")
        lines.append(t)
        lines.append("")
    path.write_bytes("\n".join(lines).encode(encoding))


def write_plan_form_a(path: Path,
                      segments: list[tuple[int, float, float]]) -> None:
    """Legacy array form. segments: [(id, src_start_s, src_end_s)]."""
    data = [
        {"id": cid, "source_start": fmt_ts(s), "source_end": fmt_ts(e)}
        for cid, s, e in segments
    ]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_plan_form_b(path: Path, sources: dict[str, str],
                      segments: list[dict],
                      voices: dict[str, str] | None = None) -> None:
    """Object form with multi-source / multi-voice support."""
    data: dict = {"sources": sources, "segments": segments}
    if voices:
        data["voices"] = voices
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def helpers_ns():
    """Convenience: bundle the helpers module + write_* functions in one object."""
    import srt_driven_edit as sde

    class NS:
        pass

    ns = NS()
    ns.sde = sde
    ns.write_srt = write_srt
    ns.write_plan_form_a = write_plan_form_a
    ns.write_plan_form_b = write_plan_form_b
    ns.fmt_ts = fmt_ts
    return ns
