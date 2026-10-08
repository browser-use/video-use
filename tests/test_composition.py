"""Behavioral checks for composition timing and reusable editing primitives."""

import copy
import json
import shutil
import subprocess
from pathlib import Path
import numpy as np
import pytest

pytest.importorskip(
    "cv2", reason="install the editing extra for advanced editing tests"
)

from PIL import Image, ImageDraw
from scipy.io import wavfile
from scipy.signal import butter, sosfilt
from edl import validate_edl, EDLValidationError
from effects import (
    curve,
    transform,
    validate_effects,
    validate_time_map,
    LayerCompositor,
)
from source_scan import catalog, selected_frames
from track_mask import self_intersects, refine_mask
from mix_audio import filter_chain
from edit_io import save_json


# project
@pytest.fixture
def project(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=30:duration=3",
            "-an",
            "-c:v",
            "ffv1",
            str(tmp_path / "source.mkv"),
        ],
        check=True,
    )
    rng = np.random.default_rng(73)
    audio = rng.normal(0, 0.07, (48000 * 3, 2)).astype(np.float32)
    audio = sosfilt(butter(4, 4000, fs=48000, output="sos"), audio, axis=0).astype(
        np.float32
    )
    wavfile.write(tmp_path / "music.wav", 48000, audio)
    m = {
        "version": 3,
        "fps": 30,
        "total_frames": 61,
        "canvas": [160, 180],
        "picture": [0, 44, 160, 90],
        "font_layout": "raqm",
        "fonts": {"body": "@skill/assets/fonts/AlfaSlabOne.ttf"},
        "sources": {
            "v": {"file": "source.mkv", "provenance": "generated test fixture"},
            "m": {"file": "music.wav", "provenance": "generated test signal"},
        },
        "shots": [
            {
                "id": "a",
                "source": "v",
                "source_frame": 0,
                "start_frame": 0,
                "end_frame": 29,
                "beat": "opening",
                "reason": "test cut",
            },
            {
                "id": "b",
                "source": "v",
                "source_frame": 29,
                "start_frame": 29,
                "end_frame": 61,
                "beat": "payoff",
                "reason": "test cut",
            },
        ],
        "words": {},
        "audio": [
            {
                "id": "music",
                "source": "m",
                "role": "music",
                "start_sample": 0,
                "source_start_sample": 0,
                "sample_count": 61 * 1600,
                "gain_db": 0,
            }
        ],
        "cards": [],
        "music_required_intervals": [
            {"start_frame": 0, "end_frame": 61, "minimum_rms_dbfs": -60}
        ],
        "delivery": {"lufs": -14, "true_peak": -1.5},
    }
    save_json(tmp_path / "edl.json", m)
    return tmp_path, m


# test unknown treatment and invalid time map fail
def test_unknown_treatment_and_invalid_time_map_fail(project):
    root, m = project
    bad = copy.deepcopy(m)
    bad["shots"][0]["effects"] = {"unimplemented_glow": 1}
    with pytest.raises(EDLValidationError, match="unsupported"):
        validate_edl(bad, root)
    with pytest.raises(ValueError):
        validate_time_map([[0, 10], [10, 5]], 11)
    with pytest.raises(ValueError):
        validate_effects({"scale": float("nan")})
    with pytest.raises(ValueError):
        filter_chain([{"type": "atempo", "frequency_hz": 200}])


# test premultiplied transform preserves color
def test_premultiplied_transform_preserves_color():
    im = Image.new("RGBA", (64, 64))
    ImageDraw.Draw(im).rectangle((16, 16, 47, 47), fill=(255, 20, 0, 255))
    output = np.asarray(transform(im, {"x": 0.5, "blur": 2, "opacity": 0.5}, 0))
    edge = (output[:, :, 3] > 5) & (output[:, :, 3] < 100)
    assert edge.any() and output[:, :, 0][edge].min() > 240
    assert output[:, :, 3].max() <= 128
    assert curve(
        {"points": [[0, 0], [10, 1]], "easing": "smoothstep"}, 5
    ) == pytest.approx(0.5)


# test layers reveal silhouette and honor half open bounds
def test_layers_reveal_silhouette_and_honor_half_open_bounds(tmp_path):
    image = Image.new("RGBA", (64, 64), (200, 0, 0, 255))
    image.save(tmp_path / "subject.png")
    mask = Image.new("L", (64, 64))
    ImageDraw.Draw(mask).rectangle((20, 20, 43, 43), fill=255)
    mask.save(tmp_path / "mask.png")
    m = {
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
    compositor = LayerCompositor(m, tmp_path, {})
    base = Image.new("RGBA", (64, 64), (0, 0, 80, 255))
    assert compositor.frame(base, 1).getpixel((30, 30)) == (0, 0, 80, 255)
    assert compositor.frame(base, 2).getpixel((30, 30)) == (255, 255, 255, 255)
    assert compositor.frame(base, 4).getpixel((30, 30)) == (200, 0, 0, 255)
    assert compositor.frame(base, 6).getpixel((30, 30)) == (0, 0, 80, 255)
    assert compositor.frame(base, 3).getpixel((0, 0)) == (0, 0, 80, 255)


# test polygon crossings and protected refinement
def test_polygon_crossings_and_protected_refinement():
    assert self_intersects([[0, 0], [10, 10], [0, 10], [10, 0]])
    assert not self_intersects([[0, 0], [10, 0], [10, 10], [0, 10]])
    image = np.zeros((64, 64, 3), np.uint8)
    image[18:45, 18:45] = (180, 40, 20)
    seed = np.zeros((64, 64), np.uint8)
    seed[16:47, 16:47] = 255
    protected = np.zeros_like(seed)
    protected[30, 12] = 255
    refined = refine_mask(image, seed, protected)
    assert refined[30, 12] == 255 and refined[30, 30] == 255 and refined[0, 0] == 0


# test composition render preserves picture audio and layers
def test_composition_render_preserves_picture_audio_and_layers(project):
    root, m = project
    panel = Image.new("RGBA", (160, 90))
    ImageDraw.Draw(panel).rectangle((4, 4, 60, 30), fill="red")
    panel.save(root / "panel.png")
    m["layers"] = [
        {
            "id": "panel",
            "image": "panel.png",
            "start_frame": 10,
            "end_frame": 25,
            "clip": [0, 0, {"points": [[10, 1], [15, 160]]}, 90],
            "effects": {
                "x": {"points": [[10, -3], [15, 0]], "easing": "cubic_out"},
                "blur": {"points": [[10, 3], [15, 0]]},
            },
        }
    ]
    m["shots"][1]["effects"] = {"scale": 1.05}
    m["font_layout"] = "basic"
    m["words"] = {
        "word": {
            "text": "TEST",
            "source": "m",
            "start_sample": 9600,
            "end_sample": 19200,
            "source_start_sample": 9600,
            "source_end_sample": 19200,
        }
    }
    m["cards"] = [
        {
            "id": "caption",
            "start_frame": 6,
            "end_frame": 12,
            "word_ids": ["word"],
            "lines": [
                {
                    "text": "TEST",
                    "font": "body",
                    "x": 10,
                    "y": 10,
                    "cap_height": 14,
                    "color": "#FFFFFF",
                }
            ],
        }
    ]
    save_json(root / "edl.json", m)
    import sys

    rendered = subprocess.run(
        [
            sys.executable,
            "helpers/render.py",
            str(root / "edl.json"),
            "-o",
            str(root / "final.mp4"),
        ],
        capture_output=True,
        text=True,
    )
    assert rendered.returncode == 0, rendered.stderr[-4000:]
    from _composition import build

    result = json.loads((root / "final_build/verification.json").read_text())
    assert result["technical_pass"]
    assert (
        result["checks"]["declared_music_presence"]
        and result["checks"]["audio_alignment"]
    )
    assert result["checks"]["frame_count"] and result["expected_audio_samples"] == 97600
    from verify_edit import review_sheets

    assert review_sheets(m, root / "final.mp4", root / "review")
    assert (root / "final_build/audio/music.wav").exists()
    with pytest.raises(FileExistsError):
        build(root / "edl.json", root / "final.mp4")


# test mapped picture preserves requested frames
def test_mapped_picture_preserves_requested_frames(project):
    root, m = project
    from effects import stage_mapped

    shot = {
        "id": "ramp",
        "source": "v",
        "start_frame": 0,
        "end_frame": 10,
        "time_map": [[0, 0], [4, 1], [9, 20]],
    }
    stage_mapped(m, root, shot, root / "mapped.mp4")
    assert len(catalog(root / "mapped.mp4")["frames"]) == 10


# test card support and occlusion share one frame clock
def test_card_support_and_occlusion_share_one_frame_clock(project):
    from PIL import features
    from cards import CardRenderer

    if not features.check_feature("raqm"):
        pytest.skip("RAQM required for production typography")
    root, m = project
    mask = Image.new("L", (160, 90))
    ImageDraw.Draw(mask).rectangle((0, 0, 70, 89), fill=255)
    mask.save(root / "mask.png")
    m["cards"] = [
        {
            "id": "text",
            "start_frame": 4,
            "end_frame": 12,
            "lines": [
                {"text": "TEST", "font": "body", "x": 60, "y": 35, "cap_height": 16}
            ],
            "shadow": {"radius": 1.5, "opacity": 0.8},
            "outline": {"radius": 1},
            "occlusion_mask": {"file": "mask.png"},
        }
    ]
    renderer = CardRenderer(m, root)
    assert renderer.frame(3).getbbox() is None and renderer.frame(12).getbbox() is None
    alpha = np.asarray(renderer.frame(4).getchannel("A"))
    assert alpha[:, 71:].max() > 0 and alpha[:, :71].max() == 0
    _, boxes = renderer.frame(4, measure=True)
    assert boxes[0]["card_support_xyxy"][0] < boxes[0]["box"][0]
    assert boxes[0]["visible_support_xyxy"][0] >= 71


# test malformed foreground settings fail during validation
@pytest.mark.parametrize(
    "settings",
    [
        {"source_frame": 1, "source_start": 0.1},
        {"speed": 0},
        {"source_frame": -1},
        {"source_crop": [0, 0, 159, 90]},
        {"mask": {"file": "missing.png", "unused_feather": 2}},
    ],
)
def test_malformed_foreground_settings_fail_during_validation(project, settings):
    root, m = project
    m["layers"] = [
        {
            "id": "foreground",
            "source": "v",
            "start_frame": 0,
            "end_frame": 10,
            **settings,
        }
    ]
    with pytest.raises(EDLValidationError):
        validate_edl(m, root, check_files=False)


# test retimed base and continuous moving foreground share the cut clock
def test_mixed_retiming_and_foreground_keep_exact_clock(project):
    root, m = project
    m["shots"][1].pop("source_frame")
    m["shots"][1]["time_map"] = [[0, 30], [6, 32], [31, 70]]
    m["layers"] = [
        {
            "id": "moving",
            "source": "v",
            "source_frame": 50,
            "start_frame": 25,
            "end_frame": 36,
            "clip": [0, 0, 30, 90],
        }
    ]
    save_json(root / "edl.json", m)
    from _composition import build

    result = build(root / "edl.json", root / "mixed.mp4")
    assert result["technical_pass"] and result["checks"]["frame_clock"]
    assert len(catalog(root / "mixed_build/layer_001.mp4")["frames"]) == 11
    stages = [catalog(root / f"mixed_build/shot_{i:03}.mp4") for i in (1, 2)]
    assert [s["stream"]["time_base"] for s in stages] == ["1/15360", "1/15360"]


# test render never overwrites inputs stored under a generated directory
def test_generated_directory_cannot_contain_layer_inputs(project):
    root, m = project
    folder = root / "unsafe_build"
    folder.mkdir()
    panel = folder / "panel.png"
    Image.new("RGBA", (160, 90), "red").save(panel)
    before = panel.read_bytes()
    m["layers"] = [
        {
            "id": "panel",
            "image": "unsafe_build/panel.png",
            "start_frame": 0,
            "end_frame": 3,
        }
    ]
    save_json(root / "edl.json", m)
    from _composition import build

    with pytest.raises(ValueError, match="inputs must be outside"):
        build(root / "edl.json", root / "unsafe.mp4")
    assert panel.read_bytes() == before and not (folder / "shot_001.mp4").exists()


# test source chapters never extend a prepared picture stage
def test_source_chapters_cannot_extend_picture_stages(project):
    root, m = project
    metadata = root / "chapters.ffmeta"
    metadata.write_text(
        ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=60000\ntitle=Long source chapter\n"
    )
    source = root / "chaptered.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(root / "source.mkv"),
            "-i",
            str(metadata),
            "-map",
            "0:v:0",
            "-map_chapters",
            "1",
            "-c",
            "copy",
            str(source),
        ],
        check=True,
    )
    m["sources"]["v"]["file"] = source.name
    from _composition import stage_shot

    stage = root / "chapter_free.mp4"
    stage_shot(m, root, m["shots"][0], stage)
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_chapters",
            "-show_format",
            "-of",
            "json",
            str(stage),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)
    assert data.get("chapters", []) == [] and float(data["format"]["duration"]) < 1
    assert len(catalog(stage)["frames"]) == 29
