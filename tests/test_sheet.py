"""Native contact sheet behavior before the optional cached review workflow."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from edit_io import sha256
from sheet import build


# create a short changing pattern to exercise actual sequential frame decoding
@pytest.fixture
def source(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe required")
    path = tmp_path / "source.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10:duration=2",
            "-c:v",
            "ffv1",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


# explicit native indices produce correctly sized thumbnails and source metadata
def test_explicit_frames_keep_original_indices(source, tmp_path):
    output = tmp_path / "sheet.jpg"
    result = build(source, output, frames=[19, 0, 7, 7], width=160, columns=2)
    assert [r["frame"] for r in result["frames"]] == [0, 7, 19]
    assert [r["pts"] for r in result["frames"]] == pytest.approx([0, 0.7, 1.9])
    assert result["source_sha256"] == sha256(source)
    assert result["visual_review"] == "pending"
    with Image.open(output) as image:
        assert image.size == (320, 228)
    assert json.loads(Path(str(output) + ".json").read_text()) == result


# time sampling uses the source timestamps rather than an assumed frame rate
def test_interval_selection(source, tmp_path):
    result = build(source, tmp_path / "sample.jpg", every=0.5)
    assert [r["frame"] for r in result["frames"]] == [0, 5, 10, 15]


# large selections produce bounded pages instead of one increasingly tall image
def test_paginated_sheet(source, tmp_path):
    output = tmp_path / "sheet.jpg"
    result = build(source, output, frames=list(range(13)), width=160, columns=2)
    assert result["sheets"] == [str(output), str(tmp_path / "sheet_002.jpg")]
    with Image.open(output) as image:
        assert image.size == (320, 6 * 114)
    with Image.open(result["sheets"][1]) as image:
        assert image.size == (320, 114)


# invalid native indices must fail without producing a review image
@pytest.mark.parametrize("frames", [[], [-1], [20]])
def test_invalid_selection_produces_no_sheet(source, tmp_path, frames):
    output = tmp_path / "bad.jpg"
    with pytest.raises(ValueError, match="outside"):
        build(source, output, frames=frames)
    assert not output.exists()


# a contact sheet cannot overwrite the source it is meant to inspect
def test_output_cannot_replace_source(source):
    digest = sha256(source)
    with pytest.raises(ValueError, match="overwrite"):
        build(source, source, frames=[0])
    assert sha256(source) == digest
