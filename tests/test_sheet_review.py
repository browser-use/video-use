"""Critical-frame review reuses native decoding without weakening visual evidence."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))
import sheet


# stand in for native decoding so most tests do not need FFmpeg
@pytest.fixture
def media(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"first render")
    counts = {"catalog": 0, "decode": 0}

    # return a deterministic native PTS catalog
    def catalog(path):
        counts["catalog"] += 1
        return {
            "sha256": sheet.sha256(path),
            "frames": [{"frame": n, "pts": 2 + n / 10} for n in range(80)],
        }

    # count decoder passes and yield known pixels
    def decode(path, frames):
        counts["decode"] += 1
        for n in frames:
            yield n, Image.new("RGB", (640, 360), (n, 20, 30))

    monkeypatch.setattr(sheet, "catalog", catalog)
    monkeypatch.setattr(sheet, "selected_frames", decode)
    return source, tmp_path / "review", counts


# timestamp selection is relative to first PTS and snaps forward without duplicate frames
def test_times_snap_to_native_frames():
    rows = [{"frame": i, "pts": p} for i, p in enumerate([2, 2.1, 2.4, 2.8])]
    assert sheet.frames_at_times(rows, [0, 0.11, 0.2]) == [0, 2]
    with pytest.raises(ValueError, match="outside"):
        sheet.frames_at_times(rows, [0.81])


# invalid input fails before decoding or creating an output directory
@pytest.mark.parametrize(
    "times,kwargs",
    [
        ([], {}),
        ([float("nan")], {}),
        ([float("inf")], {}),
        ([-1], {}),
        ([0], {"width": 0}),
        ([0], {"columns": 11}),
        (list(range(121)), {}),
    ],
)
def test_invalid_arguments(media, times, kwargs):
    source, dest, calls = media
    with pytest.raises(ValueError):
        sheet.review(source, dest, times, **kwargs)
    assert calls["decode"] == 0 and not dest.exists()


# the exact match skips both probe and decoder while retaining native pixels
def test_cache_and_full_resolution(media):
    source, dest, calls = media
    first = sheet.review(source, dest, [0, 1])
    second = sheet.review(source, dest, [1, 0, 1])
    assert not first["cache_hit"] and second["cache_hit"]
    assert calls == {"catalog": 1, "decode": 1}
    with Image.open(dest / "frame_000000.png") as image:
        assert image.size == (640, 360) and image.getpixel((10, 10)) == (0, 20, 30)
    assert first["visual_review"].startswith("pending")


# source replacement settings damaged output and force each invalidate reuse
def test_invalidation_and_force(media):
    source, dest, calls = media
    sheet.review(source, dest, [0])
    source.write_bytes(b"other render")  # same byte length, different fingerprint
    assert not sheet.review(source, dest, [0])["cache_hit"]
    assert not sheet.review(source, dest, [0, 1])["cache_hit"]
    assert not sheet.review(source, dest, [0, 1], width=240)["cache_hit"]
    (dest / "frame_000000.png").write_bytes(b"broken")
    assert not sheet.review(source, dest, [0, 1], width=240)["cache_hit"]
    assert not sheet.review(source, dest, [0, 1], width=240, force=True)["cache_hit"]
    assert calls["decode"] == 6


# source/output aliasing and redirected output paths must never overwrite source bytes
def test_path_safety(media, tmp_path):
    source, dest, _ = media
    with pytest.raises(ValueError, match="destination"):
        sheet.review(source, source.parent, [0])
    link = tmp_path / "linked"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        sheet.review(source, link / "review", [0])
    dest.mkdir()
    (dest / "frame_000000.png").symlink_to(source)
    with pytest.raises(ValueError, match="unsafe"):
        sheet.review(source, dest, [0])
    assert source.read_bytes() == b"first render"


# malicious cache paths are not accepted or followed
def test_cache_manifest_cannot_escape(media):
    source, dest, _ = media
    result = sheet.review(source, dest, [0])
    result["artifacts"] = {"../source.mp4": sheet.sha256(source)}
    (dest / "review.json").write_text(json.dumps(result))
    assert not sheet.review(source, dest, [0])["cache_hit"]


# interrupted extraction preserves previously published evidence
def test_failed_extraction_keeps_old_evidence(media, monkeypatch):
    source, dest, _ = media
    sheet.review(source, dest, [0])
    old = (dest / "review.json").read_bytes()

    # fail after one successful decoded frame
    def fail(*args):
        yield 0, Image.new("RGB", (640, 360))
        raise RuntimeError("decoder failed")

    monkeypatch.setattr(sheet, "selected_frames", fail)
    with pytest.raises(RuntimeError, match="decoder failed"):
        sheet.review(source, dest, [0, 1], force=True)
    assert (dest / "review.json").read_bytes() == old
    assert not list(dest.glob(".review-*"))


# replacing source during decoding is not allowed to publish mixed-version evidence
def test_source_changes_during_extraction(media, monkeypatch):
    source, dest, _ = media

    # simulate a still-running renderer replacing the source
    def changing(*args):
        source.write_bytes(b"replaced while decoding")
        yield 0, Image.new("RGB", (640, 360))

    monkeypatch.setattr(sheet, "selected_frames", changing)
    with pytest.raises(ValueError, match="source changed"):
        sheet.review(source, dest, [0])
    assert not (dest / "review.json").exists()


# sheets paginate and label native indices and original PTS
def test_pagination_and_labels(media, monkeypatch):
    source, dest, _ = media
    labels = []
    original = sheet.ImageDraw.ImageDraw.text

    # record visible sheet labels without replacing Pillow drawing
    def text(self, xy, label, *args, **kwargs):
        labels.append(label)
        return original(self, xy, label, *args, **kwargs)

    monkeypatch.setattr(sheet.ImageDraw.ImageDraw, "text", text)
    result = sheet.review(source, dest, [i * 0.2 for i in range(35)], columns=5)
    assert result["sheets"] == ["sheet_001.jpg", "sheet_002.jpg"]
    assert len(labels) == 35 and labels[0] == "f0  PTS 2.000000s"
    with Image.open(dest / result["sheets"][0]) as image:
        assert image.size == (1600, 6 * (180 + 24))


# generated real media proves the native decoder picks distinct scene pixels
def test_real_three_scene_video(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe required")
    source = tmp_path / "three.mp4"
    command = ["ffmpeg", "-v", "error", "-y"]
    for color in ("red", "green", "blue"):
        command += ["-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=10:d=1"]
    command += [
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(source),
    ]
    subprocess.run(command, check=True, capture_output=True)
    dest = tmp_path / "review"
    result = sheet.review(source, dest, [0, 1, 2])
    assert [r["frame"] for r in result["frames"]] == [0, 10, 20]
    for n, channel in [(0, 0), (10, 1), (20, 2)]:
        with Image.open(dest / f"frame_{n:06}.png") as image:
            color = image.getpixel((100, 90))
            assert color[channel] > max(color[i] for i in range(3) if i != channel) + 80
    assert sheet.review(source, dest, [0, 1, 2])["cache_hit"]
