"""Verify smaller review movies retain source clocks and protect original files."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from PIL import Image, ImageChops, ImageStat

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helpers"))
import review_copy
from edit_io import sha256
import timeline_view


# Generate moving footage with optional source audio using only local tools
@pytest.fixture
def source(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe required")
    path = tmp_path / "source.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                    "-c:v", "ffv1", "-c:a", "pcm_s16le", "-shortest", str(path)], check=True, timeout=30)
    return path


# Repeated creation verifies and reuses a completed copy without touching the original
def test_copy_reuse_and_timeline(source, tmp_path, monkeypatch):
    original = sha256(source)
    result = review_copy.make_copy(source, tmp_path / "cache", max_edge=320)
    assert not result["reused"] and result["timing"]["frames"] == 50
    assert result["copy_video"]["width"] == 320 and result["copy_video"]["height"] == 180
    assert result["timing"]["maximum_timing_error_seconds"] <= 0.000002
    assert result["timing"]["audio_end_error_seconds"] <= 0.05
    assert sha256(source) == original
    repeated = review_copy.make_copy(source, tmp_path / "cache", max_edge=320)
    assert repeated["reused"] and repeated["movie"] == result["movie"]
    picture_inputs, audio_inputs = [], []
    extract, envelope = timeline_view.extract_frames, timeline_view.compute_envelope

    # Record actual picture selection while still exercising real frame extraction
    def capture_picture(video, *args, **kwargs):
        picture_inputs.append(video)
        return extract(video, *args, **kwargs)

    # Record the original audio path used for the waveform
    def capture_audio(video, *args, **kwargs):
        audio_inputs.append(video)
        return envelope(video, *args, **kwargs)

    monkeypatch.setattr(timeline_view, "extract_frames", capture_picture)
    monkeypatch.setattr(timeline_view, "compute_envelope", capture_audio)
    image = tmp_path / "timeline.png"
    timeline_view.render_timeline(source, 0.2, 1.5, image, 3, None, Path(result["manifest"]))
    assert picture_inputs == [Path(result["movie"])] and audio_inputs == [source]
    with Image.open(image) as picture:
        assert picture.width >= 1920 and picture.height > 300
    assert sha256(source) == original
    command = [sys.executable, str(ROOT / "helpers/review_copy.py"), str(source), "--cache", str(tmp_path / "cache"), "--max-edge", "320"]
    run = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["reused"]


# Damaged movies and changed sources must never be used as review evidence
def test_stale_copy_is_rejected(source, tmp_path):
    result = review_copy.make_copy(source, tmp_path / "cache", max_edge=320)
    movie = Path(result["movie"])
    movie.write_bytes(b"damaged")
    with pytest.raises(ValueError, match="changed"):
        review_copy.load_verified(result["manifest"], source)
    with pytest.raises(ValueError, match="changed"):
        review_copy.make_copy(source, tmp_path / "cache", max_edge=320)
    assert not list((tmp_path / "cache").glob(".review-copy-*"))


# Timing record edits and replacement source bytes invalidate saved review evidence
def test_record_and_source_changes(source, tmp_path):
    result = review_copy.make_copy(source, tmp_path / "cache", max_edge=320)
    path = Path(result["manifest"])
    original = path.read_bytes()
    data = json.loads(original)
    data["timing"]["seek_offset_seconds"] += 0.5
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="record changed"):
        review_copy.load_verified(path, source)
    path.write_bytes(original)
    source.write_bytes(b"new source bytes")
    with pytest.raises(ValueError, match="source changed"):
        review_copy.load_verified(path, source)


# Shifted source audio and video preserve their relationship after the copy is normalized
def test_shifted_audio_and_video(source, tmp_path, monkeypatch):
    shifted = tmp_path / "shifted.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-map", "0", "-c", "copy",
                    "-output_ts_offset", "5", str(shifted)], check=True, timeout=30)
    result = review_copy.make_copy(shifted, tmp_path / "cache", max_edge=320)
    assert result["timing"]["source_first_pts"] >= 5
    assert result["timing"]["audio_start_error_seconds"] <= 0.05
    assert result["timing"]["audio_end_error_seconds"] <= 0.05
    assert abs(result["timing"]["seek_offset_seconds"]) < 0.05
    expected = timeline_view.extract_frames(shifted, 0.2, 1.6, 4, tmp_path / "original-frames")
    extract, envelope = timeline_view.extract_frames, timeline_view.compute_envelope
    measured, audio_inputs = [], []

    def compare_pictures(video, *args, **kwargs):
        assert video == Path(result["movie"])
        paths = extract(video, *args, **kwargs)
        for original, copy in zip(expected, paths):
            with Image.open(original) as a, Image.open(copy) as b:
                measured.append(max(ImageStat.Stat(ImageChops.difference(a, b)).mean))
        return paths

    def capture_audio(video, *args, **kwargs):
        audio_inputs.append(video)
        return envelope(video, *args, **kwargs)

    monkeypatch.setattr(timeline_view, "extract_frames", compare_pictures)
    monkeypatch.setattr(timeline_view, "compute_envelope", capture_audio)
    output = tmp_path / "shifted-timeline.png"
    timeline_view.render_timeline(shifted, 0.2, 1.6, output, 4, None, Path(result["manifest"]))
    assert len(measured) == 4 and max(measured) < 5
    assert audio_inputs == [shifted] and output.is_file()


# HDR conversion requires an explicit choice and produces tagged SDR review pixels
def test_hdr_requires_tonemap(source, tmp_path):
    filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, check=True).stdout
    if " zscale " not in filters:
        pytest.skip("zscale required for the explicit HDR review path")
    hdr = tmp_path / "hdr.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p10le",
                    "-vf", "setparams=range=limited:color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc",
                    "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", str(hdr)], check=True, timeout=30)
    facts = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(hdr)], capture_output=True, check=True).stdout)
    assert facts["streams"][0]["color_transfer"] == "arib-std-b67"
    with pytest.raises(ValueError, match="requires --tonemap"):
        review_copy.make_copy(hdr, tmp_path / "cache", max_edge=320)
    assert not list((tmp_path / "cache").iterdir())
    result = review_copy.make_copy(hdr, tmp_path / "cache", max_edge=320, tonemap=True)
    assert result["copy_video"]["color_transfer"] == "bt709"
    assert result["copy_video"]["color_primaries"] == "bt709"


# Variable frame spacing and a nonzero source start survive review encoding
def test_variable_timing_and_offset(source, tmp_path):
    vfr = tmp_path / "vfr.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-map", "0:v:0", "-an", "-vf",
                    "select='not(mod(n,3))',setpts=PTS+5/TB", "-fps_mode", "passthrough", "-c:v", "ffv1", str(vfr)], check=True, timeout=30)
    # Vary selected frame gaps rather than simply making a lower constant rate
    uneven = tmp_path / "uneven.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-copyts", "-i", str(vfr), "-vf", "select='not(eq(n,3))'", "-fps_mode", "passthrough", "-c:v", "ffv1", str(uneven)], check=True, timeout=30)
    result = review_copy.make_copy(uneven, tmp_path / "cache", max_edge=320)
    assert result["timing"]["frames"] == 16
    assert result["timing"]["source_first_pts"] >= 5
    assert result["timing"]["maximum_timing_error_seconds"] <= 0.000002
    assert "audio_start_error_seconds" not in result["timing"]


# A phone display matrix must produce an upright portrait review copy
def test_rotated_picture(source, tmp_path):
    base, rotated = tmp_path / "base.mp4", tmp_path / "rotated.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-an", "-c:v", "libx264", str(base)], check=True, timeout=30)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(base), "-c", "copy", "-metadata:s:v:0", "rotate=90", str(rotated)], check=True, timeout=30)
    raw = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(rotated)], capture_output=True, check=True).stdout)
    if not any(abs(float(row.get("rotation", 0))) == 90 for stream in raw["streams"] for row in stream.get("side_data_list", [])):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(base), "-c", "copy", str(rotated)], check=True, timeout=30)
    result = review_copy.make_copy(rotated, tmp_path / "cache", max_edge=320)
    assert result["copy_video"]["width"] == 180 and result["copy_video"]["height"] == 320


# Invalid options or cache locations fail before any source can be overwritten
def test_invalid_options_and_paths(source, tmp_path):
    original = sha256(source)
    for kwargs in ({"max_edge": 99}, {"max_edge": True}, {"crf": 60}, {"timeout": float("inf")}):
        with pytest.raises(ValueError):
            review_copy.make_copy(source, tmp_path / "unused", **kwargs)
    assert not (tmp_path / "unused").exists()
    with pytest.raises(ValueError, match="outside"):
        review_copy.make_copy(source, tmp_path)
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        review_copy.make_copy(source, link)
    assert sha256(source) == original


# A failed encode leaves no cache entry that a later run could mistake for success
def test_failed_encode_is_not_published(source, tmp_path, monkeypatch):
    original = review_copy.run

    # Fail only encoding after a partial output has appeared
    def fail(args, folder, timeout, stdout=None):
        if str(args[0]) == "ffmpeg":
            Path(folder, "review.mp4").write_bytes(b"partial")
            raise ValueError("encoding failed")
        return original(args, folder, timeout, stdout)

    monkeypatch.setattr(review_copy, "run", fail)
    with pytest.raises(ValueError, match="encoding failed"):
        review_copy.make_copy(source, tmp_path / "cache")
    assert not list((tmp_path / "cache").iterdir())


# The frame verifier detects missing frames and changed spacing independently of encoding
def test_frame_timing_failures(tmp_path):
    left, right = tmp_path / "a.csv", tmp_path / "b.csv"
    left.write_text("5.0\n5.04\n5.12\n")
    right.write_text("0.0\n0.04\n0.12\n")
    assert review_copy.compare_timing(left, right)["frames"] == 3
    right.write_text("0.0\n0.04\n0.08\n")
    with pytest.raises(ValueError, match="timing"):
        review_copy.compare_timing(left, right)
    right.write_text("0.0\n0.04\n")
    with pytest.raises(ValueError, match="count"):
        review_copy.compare_timing(left, right)
