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
    with pytest.raises(ValueError):
        validate(
            {
                "artifacts": {
                    "item": {
                        "phase": "sources",
                        "status": "measured",
                        "path": "input",
                        "summary": "evidence",
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
def test_search_ranks_matching_video_frame(tmp_path):
    pytest.importorskip("cv2", reason="install the editing extra")
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
