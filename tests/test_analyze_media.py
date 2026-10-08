"""Measure actual cuts and silent intervals and verify command line cache reuse."""

import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import wave

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helpers"))
import analyze


# Build two solid shots with known silent middle and tail intervals
@pytest.fixture
def source(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe required")
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setparams((1, 2, 48000, 96000, "NONE", "not compressed"))
        stream.writeframes(b"".join(struct.pack("<h", 0 if 24000 <= i < 48000 or i >= 72000 else int(8000 * math.sin(2 * math.pi * 440 * i / 48000))) for i in range(96000)))
    path = tmp_path / "source.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=160x90:r=25:d=1",
                    "-f", "lavfi", "-i", "color=blue:s=160x90:r=25:d=1", "-i", str(audio),
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "ffv1", "-c:a", "pcm_s16le", str(path)], check=True, timeout=30)
    return path


# A second process must reuse all records and a new audio setting must preserve picture work
def test_real_analysis_and_cli_reuse(source, tmp_path):
    result = analyze.analyze(source, tmp_path / "cache", ["scenes", "motion", "silence"], scene_threshold=0.1)
    picture = result["results"]["picture"]
    assert picture["frames_measured"] == 50
    assert any(abs(row["time"] - 1) < 0.05 for row in picture["scenes"])
    intervals = result["results"]["silence"]["intervals"]
    assert len(intervals) == 2
    assert abs(intervals[0]["start"] - 0.5) < 0.01 and abs(intervals[0]["end"] - 1) < 0.01
    assert abs(intervals[1]["start"] - 1.5) < 0.01 and abs(intervals[1]["end"] - 2) < 0.01
    command = [sys.executable, str(ROOT / "helpers/analyze.py"), str(source), "--cache", str(tmp_path / "cache"),
               "--need", "scenes", "--need", "silence", "--scene-threshold", "0.1", "--start", "0.8", "--end", "1.2", "--limit", "1"]
    process = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    report = json.loads(process.stdout)
    assert all(row["reused"] for row in report["reuse"].values())
    assert report["scenes"]["matching"] == 1 and len(report["scenes"]["shown"]) == 1
    changed = analyze.analyze(source, tmp_path / "cache", ["scenes", "silence"], scene_threshold=0.1, noise_db=-40)
    assert changed["reuse"]["picture"]["reused"] and not changed["reuse"]["silence"]["reused"]


# Nonzero media timestamps remain traceable to the original source clock
def test_source_presentation_offset(source, tmp_path):
    shifted = tmp_path / "shifted.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-map", "0", "-c", "copy", "-output_ts_offset", "5", str(shifted)], check=True, timeout=30)
    result = analyze.analyze(shifted, tmp_path / "cache", ["scenes", "silence"], scene_threshold=0.1)
    assert abs(result["results"]["picture"]["scenes"][0]["time"] - 6) < 0.05
    assert abs(result["results"]["silence"]["intervals"][0]["start"] - 5.5) < 0.02


def test_picture_accepts_duplicate_times_but_rejects_backward_times(monkeypatch):
    timestamps = [5.0, 5.0, 5.1]

    def measure(args, folder, timeout):
        Path(folder, "events.txt").write_text("".join(
            f"frame:{i} pts:0 pts_time:{t}\nlavfi.scene_score=0.5\n" for i, t in enumerate(timestamps)
        ), encoding="utf-8")

    monkeypatch.setattr(analyze, "ffmpeg", measure)
    result = analyze.picture("unused", 0.3, 1, 10)
    assert result["frames_measured"] == 3
    assert result["motion"][0]["samples"] == 3
    assert [row["time"] for row in result["scenes"]] == timestamps
    timestamps[:] = [5.0, 4.9]
    with pytest.raises(ValueError, match="backwards"):
        analyze.picture("unused", 0.3, 1, 10)


def test_metadata_retains_rotation_tags_and_side_data(monkeypatch):
    streams = [{"codec_type": "video", "tags": {"rotate": "90", "unused": "drop"}},
               {"codec_type": "video", "side_data_list": [{"rotation": -90}]}]
    monkeypatch.setattr(analyze.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(
        args, 0, json.dumps({"streams": streams}).encode()))
    result = analyze.metadata("unused", 10)["streams"]
    assert result[0]["tags"] == {"rotate": "90"}
    assert result[1]["side_data_list"] == [{"rotation": -90}]
