"""Compare real cached and uncached picture renders and a complete composition."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

from helpers.render_cache import RenderCache
from _composition import build, stage_cached_shot


# Create a complete small project with generated motion and deterministic stereo audio
@pytest.fixture
def reuse_project(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe are required for render reuse media tests")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=2", "-an", "-c:v", "ffv1", str(tmp_path / "source.mkv")], check=True, timeout=30)
    rng = np.random.default_rng(73)
    audio = rng.normal(0, 0.07, (96000, 2)).astype(np.float32)
    audio = sosfilt(butter(4, 4000, fs=48000, output="sos"), audio, axis=0).astype(np.float32)
    wavfile.write(tmp_path / "audio.wav", 48000, audio)
    manifest = {"version": 3, "fps": 30, "total_frames": 30, "canvas": [160, 90], "picture": [0, 0, 160, 90],
                "sources": {"v": {"file": "source.mkv", "provenance": "generated test fixture"},
                            "a": {"file": "audio.wav", "provenance": "generated test signal"}},
                "shots": [{"id": "first", "source": "v", "source_frame": 0, "start_frame": 0, "end_frame": 15, "beat": "start", "reason": "test first cut"},
                          {"id": "last", "source": "v", "source_frame": 15, "start_frame": 15, "end_frame": 30, "beat": "end", "reason": "test second cut"}],
                "words": {}, "fonts": {}, "cards": [],
                "audio": [{"id": "music", "source": "a", "role": "music", "start_sample": 0,
                           "source_start_sample": 0, "sample_count": 48000, "gain_db": 0}]}
    return tmp_path, manifest


# Hash decoded frames without container timestamps or metadata influencing equality
def frame_hashes(path):
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-an", "-f", "framemd5", "-"], capture_output=True, text=True, check=True, timeout=30)
    return [line.split(",")[-1].strip() for line in result.stdout.splitlines() if not line.startswith("#") and line.strip()]


# Reusing cached pictures must preserve the actual encoded result and final verification
def test_complete_render_and_one_shot_revision(reuse_project):
    root, manifest = reuse_project
    edl = root / "edl.json"
    edl.write_text(json.dumps(manifest))
    normal = build(edl, root / "normal.mp4")
    cold = build(edl, root / "cold.mp4", reuse=True)
    assert normal["technical_pass"] and cold["technical_pass"]
    assert cold["render_reuse"] == {"reused_clips": 0, "rendered_clips": 2}
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "helpers/render.py"), str(edl), "-o", str(root / "warm.mp4"), "--reuse"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-4000:]
    warm = json.loads((root / "warm_build/verification.json").read_text())
    assert warm["technical_pass"]
    assert warm["render_reuse"] == {"reused_clips": 2, "rendered_clips": 0}
    assert frame_hashes(root / "normal.mp4") == frame_hashes(root / "cold.mp4") == frame_hashes(root / "warm.mp4")
    manifest["shots"][1]["source_frame"] = 30
    edl.write_text(json.dumps(manifest))
    revised = build(edl, root / "revised.mp4", reuse=True)
    assert revised["technical_pass"]
    assert revised["render_reuse"] == {"reused_clips": 1, "rendered_clips": 1}
    assert frame_hashes(root / "warm.mp4") != frame_hashes(root / "revised.mp4")


# Matte edits must invalidate their shot while unrelated project edits preserve it
def test_mask_source_and_settings_invalidation(reuse_project):
    root, manifest = reuse_project
    mask = root / "mask.png"
    Image.new("L", (160, 90), 255).save(mask)
    shot = copy.deepcopy(manifest["shots"][0])
    shot["isolation_mask"] = {"file": "mask.png"}
    cache = RenderCache(root / "render-cache")
    stage_cached_shot(manifest, root, shot, root / "first.mp4", cache)
    manifest["audio"][0]["gain_db"] = -6
    stage_cached_shot(manifest, root, shot, root / "second.mp4", cache)
    assert cache.summary() == {"reused_clips": 1, "rendered_clips": 1}
    Image.new("L", (160, 90), 0).save(mask)
    stage_cached_shot(manifest, root, shot, root / "third.mp4", cache)
    assert cache.summary() == {"reused_clips": 1, "rendered_clips": 2}
    assert frame_hashes(root / "first.mp4") != frame_hashes(root / "third.mp4")
