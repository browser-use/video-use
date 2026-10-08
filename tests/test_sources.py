"""Sources behavior tests that run without later editing or rendering branches."""

import json
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from edit_clock import (
    allocate_frames,
    check_partition,
    frame_to_sample,
    seconds_to_frame,
)
from edit_io import sha256, source_path
from prepare_source import prepare
from project_state import record, validate, view
from source_scan import catalog, selected_frames, scene_candidates


# generate three distinct scenes with continuous audio for actual decoder checks
@pytest.fixture
def source(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe required")
    path = tmp_path / "source.mkv"
    args = ["ffmpeg", "-v", "error", "-y"]
    for color in ("red", "green", "blue"):
        args += ["-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=10:d=1"]
    args += [
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=3",
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map",
        "[v]",
        "-map",
        "3:a",
        "-c:v",
        "ffv1",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    subprocess.run(args, check=True, capture_output=True)
    return path


# native indices must identify the requested scene rather than an approximate seek
def test_native_catalog_frames_and_scene_candidates(source):
    index = catalog(source)
    assert len(index["frames"]) == 30
    assert index["sha256"] == sha256(source)
    assert index["frames"][10]["pts"] == pytest.approx(1)
    frames = list(selected_frames(source, [20, 0, 10, 10]))
    assert [number for number, _ in frames] == [0, 10, 20]
    for (_, image), channel in zip(frames, [0, 1, 2]):
        assert image.size == (160, 90)
        pixel = image.getpixel((50, 50))
        assert pixel[channel] > max(pixel[i] for i in range(3) if i != channel) + 80
    assert [row["frame"] for row in scene_candidates(source, index)] == [10, 20]


# out-of-range frame requests fail instead of silently returning incomplete evidence
@pytest.mark.parametrize("frames", [[], [-1], [1.5], [True], [30]])
def test_invalid_frame_requests(source, frames):
    with pytest.raises(ValueError):
        list(selected_frames(source, frames))


# an explicit crop retains native frame count audio and the unmodified source
def test_prepared_copy_retains_frames_audio_and_provenance(source, tmp_path):
    digest = sha256(source)
    output = tmp_path / "prepared.mkv"
    result = prepare(source, output, crop=[0, 0, 160, 88])
    video = next(
        s for s in result["output_probe"]["streams"] if s["codec_type"] == "video"
    )
    audio = next(
        s for s in result["output_probe"]["streams"] if s["codec_type"] == "audio"
    )
    assert int(video["nb_read_frames"]) == 30
    assert (video["width"], video["height"]) == (160, 88)
    assert video["codec_name"] == "ffv1"
    assert audio["codec_name"] == "pcm_s24le" and audio["sample_rate"] == "48000"
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(output),
            "-vn",
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    ).stdout
    samples = np.frombuffer(decoded, dtype="<f4")
    assert len(samples) == 3 * 48000 and np.sqrt(np.mean(samples**2)) > 0.01
    assert sha256(source) == digest == result["source_sha256"]
    assert json.loads(Path(str(output) + ".json").read_text())[
        "output_sha256"
    ] == sha256(output)
    assert result["filters"] == ["crop=160:88:0:0"]


# invalid preparation choices must leave the original and output location untouched
@pytest.mark.parametrize("crop", [[1, 0, 100, 80], [0, 0, 162, 90], [0, 0, 0, 80]])
def test_invalid_crop_leaves_source_intact(source, tmp_path, crop):
    digest = sha256(source)
    output = tmp_path / "bad.mkv"
    with pytest.raises(ValueError, match="crop"):
        prepare(source, output, crop=crop)
    assert not output.exists() and sha256(source) == digest


# existing files and implicit HDR decisions are rejected before rendering
def test_preparation_rejects_overwrite_and_wrong_tonemap(source, tmp_path):
    with pytest.raises(FileExistsError):
        prepare(source, source)
    existing = tmp_path / "existing.mkv"
    existing.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        prepare(source, existing)
    assert existing.read_bytes() == b"keep"
    with pytest.raises(ValueError, match="tagged"):
        prepare(source, tmp_path / "sdr.mkv", tonemap=True)


# changed source evidence makes dependent artifacts stale even in a filtered view
def test_state_invalidates_downstream_and_requires_rerecording(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("first")
    (tmp_path / "timeline.json").write_text("{}")
    context = tmp_path / "context.json"
    entry = {
        "phase": "sources",
        "path": "source.txt",
        "summary": "selected source",
        "status": "measured",
    }
    dependent = {
        "phase": "timeline",
        "path": "timeline.json",
        "summary": "timing",
        "status": "draft",
        "depends_on": ["source"],
    }
    record(context, "source", entry)
    record(context, "timeline", dependent)
    assert not any(row["stale"] for row in view(context)["artifacts"])
    source.write_text("changed")
    assert all(row["stale"] for row in view(context)["artifacts"])
    record(context, "source", entry)
    assert view(context, "timeline")["artifacts"][0]["stale"]
    record(context, "timeline", dependent)
    assert not any(row["stale"] for row in view(context)["artifacts"])
    source.unlink()
    assert view(context, "timeline")["artifacts"][0]["stale"]


# malformed dependency graphs are rejected without accepting circular evidence
@pytest.mark.parametrize("dependencies", [["missing"], ["item"]])
def test_state_rejects_missing_or_cyclic_dependencies(dependencies):
    with pytest.raises(ValueError, match="unknown context dependency" if dependencies == ["missing"] else "cycle"):
        validate(
            {
                "artifacts": {
                    "item": {
                        "phase": "sources",
                        "status": "measured",
                        "path": "input",
                        "summary": "evidence",
                        "sha256": "0" * 64,
                        "depends_on": dependencies,
                    }
                }
            }
        )


# reference-only files remain blocked when accessed through a different filesystem name
def test_source_policy_rejects_study_aliases(tmp_path):
    original = tmp_path / "reference.mp4"
    original.write_bytes(b"study")
    alias = tmp_path / "alias.mp4"
    alias.symlink_to(original)
    manifest = {
        "sources": {"clip": {"file": str(alias), "provenance": "declared"}},
        "study_media": [str(original)],
    }
    with pytest.raises(ValueError, match="study media"):
        source_path(manifest, tmp_path, "clip")
    manifest["study_media"] = []
    manifest["sources"]["clip"]["study_only"] = True
    with pytest.raises(ValueError, match="study-only"):
        source_path(manifest, tmp_path, "clip")
    manifest["sources"]["clip"] = {"file": str(original)}
    with pytest.raises(ValueError, match="provenance"):
        source_path(manifest, tmp_path, "clip")


# rational timing preserves exact long durations at fractional frame rates
def test_fractional_clock_and_contiguous_partition():
    fps = Fraction(30000, 1001)
    assert frame_to_sample(30000, fps) == 48000 * 1001
    assert seconds_to_frame(Fraction(1001), fps) == 30000
    assert seconds_to_frame(Fraction(1, 60), 30) == 1
    assert allocate_frames(10, [1, 1, 1]) == [4, 3, 3]
    check_partition(
        [{"start_frame": 0, "end_frame": 4}, {"start_frame": 4, "end_frame": 10}], 10
    )
    with pytest.raises(ValueError):
        check_partition(
            [{"start_frame": 0, "end_frame": 4}, {"start_frame": 5, "end_frame": 10}],
            10,
        )


# a transformed screenshot retains enough consistent visual landmarks to be a candidate
def test_image_correspondence_survives_rotation():
    cv2 = pytest.importorskip("cv2", reason="install the editing extra")
    from find_shot import correspondence

    pixels = np.random.default_rng(12).integers(0, 256, (256, 256, 3), dtype=np.uint8)
    matrix = cv2.getRotationMatrix2D((128, 128), 7, 1)
    moved = cv2.warpAffine(pixels, matrix, (256, 256))
    result = correspondence(Image.fromarray(pixels), Image.fromarray(moved))
    assert result["inliers"] >= 10 and result["reprojection_px"] < 2
    assert (
        correspondence(Image.new("RGB", (80, 80)), Image.new("RGB", (80, 80)))[
            "inliers"
        ]
        == 0
    )


# the catalog CLI must reject attempts to overwrite its own source
def test_catalog_cli_cannot_overwrite_source(source):
    digest = sha256(source)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "helpers/source_scan.py"),
            str(source),
            "--out",
            str(source),
        ],
        capture_output=True,
    )
    assert result.returncode != 0 and sha256(source) == digest


# the public search path must rank the matching scene from an actual encoded video
def test_search_ranks_matching_video_frame(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2", reason="install the editing extra")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe required")
    from find_shot import search

    for n in range(3):
        pixels = np.random.default_rng(n).integers(
            0, 256, (360, 640, 3), dtype=np.uint8
        )
        Image.fromarray(pixels).save(tmp_path / f"input_{n}.png")
    video = tmp_path / "match.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-framerate",
            "1",
            "-i",
            str(tmp_path / "input_%d.png"),
            "-c:v",
            "ffv1",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    result = search(tmp_path / "input_1.png", video, every=1)
    assert result["candidates"][0]["frame"] == 1
    assert result["candidates"][0]["inliers"] > 50
    assert "review" in result["limit"]
    import find_shot
    index = catalog(video)
    monkeypatch.setattr(find_shot, "catalog", lambda path: pytest.fail("saved catalog must avoid another frame scan"))
    sift = cv2.SIFT_create
    calls = []

    class Detector:
        def __init__(self, **kwargs):
            self.inner = sift(**kwargs)

        def detectAndCompute(self, image, mask):
            calls.append(image.shape)
            return self.inner.detectAndCompute(image, mask)

    monkeypatch.setattr(cv2, "SIFT_create", Detector)
    reused = search(tmp_path / "input_1.png", video, every=1, index=index)
    assert reused["candidates"][0]["frame"] == 1
    assert len(calls) == 4  # one query plus three candidate frames
    video.write_bytes(b"changed source")
    with pytest.raises(ValueError, match="does not match source"):
        search(tmp_path / "input_1.png", video, index=index)


# tagged HDR requires an explicit choice before output directories are created
def test_hdr_requires_explicit_conversion(source, tmp_path):
    tagged = tmp_path / "hdr.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(source),
            "-c",
            "copy",
            "-color_trc",
            "smpte2084",
            str(tagged),
        ],
        check=True,
        capture_output=True,
    )
    destination = tmp_path / "uncreated" / "prepared.mkv"
    with pytest.raises(ValueError, match="explicit tonemap"):
        prepare(tagged, destination)
    assert not destination.parent.exists()


def test_catalog_rejects_source_changes_during_scan(source, monkeypatch):
    import source_scan
    original = source_scan.timestamps

    def changed(path):
        pts = original(path)
        path.write_bytes(b"source changed during scan")
        return pts

    monkeypatch.setattr(source_scan, "timestamps", changed)
    with pytest.raises(ValueError, match="source changed"):
        catalog(source)


def test_prepare_failure_can_retry_same_path(source, tmp_path, monkeypatch):
    import prepare_source
    output = tmp_path / "retry.mkv"
    original = prepare_source.run

    def fail(args, **kwargs):
        Path(args[-1]).write_bytes(b"partial movie")
        Path(kwargs["log"]).write_text("encoder failed")
        raise RuntimeError("encode failure")

    monkeypatch.setattr(prepare_source, "run", fail)
    with pytest.raises(RuntimeError, match="encode failure"):
        prepare(source, output)
    assert not list(tmp_path.glob("retry.mkv*")) and not list(tmp_path.glob(".prepare-*"))
    monkeypatch.setattr(prepare_source, "run", original)
    assert prepare(source, output)["output_sha256"] == sha256(output)


def test_prepare_rejects_input_changes_before_publishing(source, tmp_path, monkeypatch):
    import prepare_source
    original = prepare_source.run
    output = tmp_path / "changed.mkv"

    def changed(args, **kwargs):
        result = original(args, **kwargs)
        source.write_bytes(b"changed during conversion")
        return result

    monkeypatch.setattr(prepare_source, "run", changed)
    with pytest.raises(ValueError, match="source changed"):
        prepare(source, output)
    assert not list(tmp_path.glob("changed.mkv*"))


def test_prepare_preserves_delayed_video_and_audio(source, tmp_path):
    from source_scan import timestamps
    shifted, output = tmp_path / "shifted.mkv", tmp_path / "prepared.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-map", "0", "-c", "copy",
                    "-output_ts_offset", "5", str(shifted)], check=True, timeout=30)
    result = prepare(shifted, output)
    assert timestamps(output) == pytest.approx(timestamps(shifted), abs=0.001)
    for streams in (result["input_probe"]["streams"], result["output_probe"]["streams"]):
        assert all(float(s["start_time"]) == pytest.approx(5, abs=0.001) for s in streams)


def test_rotated_source_frames_and_crop_use_display_dimensions(source, tmp_path):
    from edit_io import probe
    base, rotated, output = tmp_path / "base.mp4", tmp_path / "rotated.mp4", tmp_path / "crop.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-an", "-c:v", "libx264", str(base)], check=True, timeout=30)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(base), "-c", "copy", "-metadata:s:v:0", "rotate=90", str(rotated)], check=True, timeout=30)
    if not any(abs(row.get("rotation", 0)) == 90 for row in probe(rotated)["streams"][0].get("side_data_list", [])):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(base), "-c", "copy", str(rotated)], check=True, timeout=30)
    assert any(abs(row.get("rotation", 0)) == 90 for row in probe(rotated)["streams"][0].get("side_data_list", []))
    assert list(selected_frames(rotated, [0], width=90))[0][1].size == (90, 160)
    result = prepare(rotated, output, crop=[0, 0, 88, 160])
    video = next(s for s in result["output_probe"]["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (88, 160)


def test_prepare_audio_only_fails_clearly(source, tmp_path):
    audio, output = tmp_path / "audio.wav", tmp_path / "prepared.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-vn", str(audio)], check=True, timeout=30)
    with pytest.raises(ValueError, match="require a video stream"):
        prepare(audio, output)
    assert not output.exists()


def test_irregular_native_frame_selection(source):
    assert [i for i, _ in selected_frames(source, [0, 2, 13, 20, 29])] == [0, 2, 13, 20, 29]
