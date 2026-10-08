"""Frame effects preserve color timing masks and source provenance."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2", reason="install the editing extra")
from PIL import Image, ImageDraw

from helpers import effects


# transparent edges retain their color when blurred and faded
def test_premultiplied_transform_preserves_color():
    image = Image.new("RGBA", (64, 64))
    ImageDraw.Draw(image).rectangle((16, 16, 47, 47), fill=(255, 20, 0, 255))
    output = np.asarray(
        effects.transform(image, {"x": 0.5, "blur": 2, "opacity": 0.5}, 0)
    )
    edge = (output[:, :, 3] > 5) & (output[:, :, 3] < 100)
    assert edge.any() and output[:, :, 0][edge].min() > 240
    assert output[:, :, 3].max() <= 128


# interpolation honors holds and exact endpoints
@pytest.mark.parametrize(
    "easing, midpoint",
    [("linear", 0.5), ("smoothstep", 0.5), ("cubic_out", 0.875), ("hold", 0)],
)
def test_curve_boundaries(easing, midpoint):
    spec = {"points": [[2, 0], [12, 1]], "easing": easing}
    effects.validate_curve(spec, "opacity", 0, 1)
    assert effects.curve(spec, 0) == 0
    assert effects.curve(spec, 7) == pytest.approx(midpoint)
    assert effects.curve(spec, 12) == 1


# malformed effect values fail before rendering
@pytest.mark.parametrize(
    "spec",
    [
        {"scale": float("nan")},
        {"opacity": 2},
        {"shutter": True},
        {"samples": 17},
        {"x": {"points": [[1, 0], [1, 2]]}},
        {"glow": 1},
    ],
)
def test_invalid_effects(spec):
    with pytest.raises(ValueError):
        effects.validate_effects(spec)


# layer masks and half open frame ranges preserve the background
def test_masked_layer_intervals(tmp_path):
    Image.new("RGBA", (64, 64), (200, 0, 0, 255)).save(tmp_path / "subject.png")
    mask = Image.new("L", (64, 64))
    ImageDraw.Draw(mask).rectangle((20, 20, 43, 43), fill=255)
    mask.save(tmp_path / "mask.png")
    manifest = {
        "total_frames": 8,
        "picture": [0, 0, 64, 64],
        "layers": [
            {
                "id": "subject",
                "image": "subject.png",
                "start_frame": 2,
                "end_frame": 6,
                "mask": {"file": "mask.png"},
                "fill": "white",
                "fill_opacity": {"points": [[2, 1], [4, 0]]},
            }
        ],
    }
    effects.validate_layers(manifest, tmp_path)
    compositor = effects.LayerCompositor(manifest, tmp_path, {})
    base = Image.new("RGBA", (64, 64), (0, 0, 80, 255))
    try:
        assert compositor.frame(base, 1).getpixel((30, 30)) == (0, 0, 80, 255)
        assert compositor.frame(base, 2).getpixel((30, 30)) == (255, 255, 255, 255)
        assert compositor.frame(base, 4).getpixel((30, 30)) == (200, 0, 0, 255)
        assert compositor.frame(base, 6).getpixel((30, 30)) == (0, 0, 80, 255)
        assert compositor.frame(base, 3).getpixel((0, 0)) == (0, 0, 80, 255)
    finally:
        compositor.close()


# reference only footage cannot become a foreground render layer
def test_layer_rejects_study_source(tmp_path):
    manifest = {
        "total_frames": 8,
        "picture": [0, 0, 64, 64],
        "sources": {
            "ref": {"file": "ref.mp4", "provenance": "reference", "study_only": True}
        },
        "layers": [{"id": "x", "source": "ref", "start_frame": 0, "end_frame": 8}],
    }
    with pytest.raises(ValueError, match="independently sourced"):
        effects.validate_layers(manifest, tmp_path, check_files=False)


# real encoded retiming selects requested source frames and honors output rate
def test_retime_and_composite_encoded_frames(tmp_path):
    source = tmp_path / "source.mkv"
    frames = np.stack(
        [
            np.full((32, 32, 3), value, np.uint8)
            for value in [20, 60, 100, 140, 180, 220]
        ]
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            "32x32",
            "-r",
            "24",
            "-i",
            "pipe:0",
            "-c:v",
            "ffv1",
            str(source),
        ],
        input=frames.tobytes(),
        check=True,
    )
    manifest = {
        "fps": 24,
        "total_frames": 3,
        "picture": [0, 0, 32, 32],
        "sources": {"s": {"file": source.name, "provenance": "synthetic test"}},
        "shots": [
            {
                "source": "s",
                "start_frame": 0,
                "end_frame": 3,
                "time_map": [[0, 0], [2, 4]],
            }
        ],
    }
    mapped = tmp_path / "mapped.mp4"
    effects.stage_mapped(manifest, tmp_path, manifest["shots"][0], mapped)
    final = tmp_path / "composed.mkv"
    effects.render_layers(manifest, tmp_path, mapped, {}, final)
    info = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-show_streams",
                "-of",
                "json",
                str(final),
            ]
        )
    )["streams"][0]
    assert info["avg_frame_rate"] == "24/1"
    assert int(info["nb_read_frames"]) == 3
    reader = cv2.VideoCapture(str(final))
    try:
        for expected in [20, 100, 180]:
            ok, frame = reader.read()
            assert ok and float(frame.mean()) == pytest.approx(expected, abs=3)
    finally:
        reader.release()
    before = final.read_bytes()
    with pytest.raises(FileExistsError):
        effects.render_layers(manifest, tmp_path, mapped, {}, final)
    assert final.read_bytes() == before
    with pytest.raises(FileExistsError):
        effects.stage_mapped(manifest, tmp_path, manifest["shots"][0], mapped)


# invalid retiming maps and rates fail before encoding
@pytest.mark.parametrize(
    "points", [[[0, 4], [2, 0]], [[1, 0], [2, 2]], [[0, 0], [0, 1]]]
)
def test_bad_time_map(points):
    with pytest.raises(ValueError):
        effects.validate_time_map(points, 3)


# one output frame needs exactly one native source position
def test_review_single_frame_map():
    effects.validate_time_map([[0, 4]], 1)
    with pytest.raises(ValueError):
        effects.validate_time_map([[0, 1], ['bad', 2], [2, 3]], 3)
