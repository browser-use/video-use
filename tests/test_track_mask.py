"""Manual polygon tracking exposes drift and protects supplied output files."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2", reason="install the editing extra")

from helpers.track_mask import track, refine_mask, self_intersects


# deterministic textured footage makes measured translation observable
def test_translation_tracks_both_directions():
    base = np.random.default_rng(7).integers(0, 255, (96, 96), dtype=np.uint8)
    frames = [
        cv2.warpAffine(base, np.float32([[1, 0, i], [0, 1, 0]]), (96, 96))
        for i in range(5)
    ]
    polygon = [[27, 25], [67, 25], [67, 65], [27, 65]]
    result = track(frames, 2, polygon)
    for row in result["frames"]:
        expected = np.asarray(polygon) + [row["frame"] - 2, 0]
        assert np.allclose(row["polygon"], expected, atol=0.3)
        assert not row["needs_review"]


# texture loss flags the span instead of claiming a reliable segmentation
def test_lost_tracking_stays_flagged():
    frames = [np.zeros((64, 64), np.uint8) for _ in range(3)]
    result = track(frames, 0, [[20, 20], [40, 20], [40, 40], [20, 40]])
    assert all(row["needs_review"] for row in result["frames"][1:])


# invalid seed polygons are rejected before optical flow
@pytest.mark.parametrize(
    "polygon",
    [
        [[0, 0], [2, 2]],
        [[0, 0], [70, 0], [0, 30]],
        [[0, 0], [float("nan"), 2], [0, 3]],
        [[10, 10], [30, 30], [10, 30], [30, 10]],
    ],
)
def test_invalid_seed(polygon):
    with pytest.raises(ValueError):
        track([np.zeros((64, 64), np.uint8)], 0, polygon)


# supplied protection keeps thin foreground details during refinement
def test_refinement_preserves_protected_pixels():
    assert self_intersects([[0, 0], [10, 10], [0, 10], [10, 0]])
    image = np.zeros((64, 64, 3), np.uint8)
    image[18:45, 18:45] = (180, 40, 20)
    seed = np.zeros((64, 64), np.uint8)
    seed[16:47, 16:47] = 255
    protected = np.zeros_like(seed)
    protected[30, 12] = 255
    refined = refine_mask(image, seed, protected)
    assert refined[30, 12] == 255 and refined[30, 30] == 255 and refined[0, 0] == 0


# the command refuses to overwrite its seed document even before decoding
def test_cli_preserves_seed(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"frame": 0, "polygon": [[1, 1], [5, 1], [5, 5]]}))
    before = seed.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "helpers/track_mask.py",
            "missing.mp4",
            str(seed),
            "--out",
            str(seed),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "new file" in result.stderr
    assert seed.read_bytes() == before


# zero area polygons cannot seed a usable matte
def test_review_zero_area_seed():
    with pytest.raises(ValueError, match='area'):
        track([np.zeros((32, 32), dtype=np.uint8)], 0, [[1, 1], [2, 2], [3, 3]])


# numpy polygons serialize without a special json encoder
def test_review_numpy_seed():
    result = track([np.zeros((32, 32), dtype=np.uint8)], 0, np.array([[2, 2], [20, 2], [20, 20]]))
    json.dumps(result, allow_nan=False)
