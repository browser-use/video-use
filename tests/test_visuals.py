"""test visuals support for video use"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from helpers import visuals


# test canvas dimensions are even
def test_canvas_dimensions_are_even() -> None:
    assert visuals.canvas_dimensions(
        {"canvas": {"width": 1081, "height": 1921}}, 1920, 1080
    ) == (1080, 1920)


# test preview visual specs scale pixels but preserve relative values
def test_preview_visual_specs_scale_pixels_but_preserve_relative_values() -> None:
    treatment = {
        "canvas": {
            "width": 1080,
            "height": 1920,
            "fit": "blur",
            "blur": 30,
            "foreground": {"width": 1080, "height": 608, "x": 0.5, "y": 656},
        }
    }
    graphics = [
        {
            "type": "text",
            "text": "NVIDIA'S AI MOAT",
            "max_lines": 1,
            "x": 0.5,
            "y": 120,
            "font_size": 60,
            "stroke_width": 2,
        }
    ]
    captions = {"x": 0.5, "y": 0.72, "font_size": 60, "shadow_y": 6}

    scaled_treatment, scaled_graphics, scaled_captions, scale = (
        visuals.scale_visual_specs(
            treatment,
            graphics,
            captions,
            fallback_width=1920,
            fallback_height=1080,
            max_dimension=1280,
        )
    )

    assert scale == pytest.approx(2 / 3)
    assert visuals.canvas_dimensions(scaled_treatment, 1280, 720) == (720, 1280)
    foreground = scaled_treatment["canvas"]["foreground"]
    assert foreground["width"] == 720
    assert foreground["height"] == 404
    assert foreground["x"] == 0.5
    assert foreground["y"] == pytest.approx(656 * 2 / 3)
    assert scaled_treatment["canvas"]["blur"] == 20
    assert scaled_graphics[0]["x"] == 0.5
    assert scaled_graphics[0]["y"] == 80
    assert scaled_graphics[0]["font_size"] == 40
    assert scaled_graphics[0]["stroke_width"] == pytest.approx(4 / 3)
    assert scaled_captions["x"] == 0.5
    assert scaled_captions["y"] == 0.72
    assert scaled_captions["font_size"] == 40
    assert scaled_captions["shadow_y"] == 4
    assert treatment["canvas"]["width"] == 1080
    assert graphics[0]["font_size"] == 60

    image = Image.new("RGBA", (720, 1280), (0, 0, 0, 0))
    font, rendered_text = visuals._fit_font(
        ImageDraw.Draw(image),
        scaled_graphics[0]["text"],
        scaled_graphics[0],
        720,
        1280,
    )
    assert font.size == 40
    assert rendered_text == "NVIDIA'S AI MOAT"


# test reframe filter encodes zoom and focus
def test_reframe_filter_encodes_zoom_and_focus() -> None:
    value = visuals.build_reframe_filter(
        {"zoom": 1.08, "focus_x": 0.4, "focus_y": 0.25}
    )
    assert "scale=ceil(iw*1.080000" in value
    assert "(iw-ow)*0.400000" in value
    assert "(ih-oh)*0.250000" in value


# test reframe rejects unsafe values
def test_reframe_rejects_unsafe_values() -> None:
    with pytest.raises(ValueError, match="zoom"):
        visuals.build_reframe_filter({"zoom": 0.5})
    with pytest.raises(ValueError, match="focus"):
        visuals.build_reframe_filter({"zoom": 1.1, "focus_x": 2})


# test blur treatment builds background and foreground
def test_blur_treatment_builds_background_and_foreground() -> None:
    parts, label, dimensions = visuals.build_treatment_filters(
        "[0:v]",
        {
            "canvas": {
                "width": 1080,
                "height": 1920,
                "fit": "blur",
                "blur": 30,
                "foreground": {"width": 1080, "height": 800, "fit": "cover", "y": 410},
            }
        },
        fallback_width=1920,
        fallback_height=1080,
    )
    graph = ";".join(parts)
    assert dimensions == (1080, 1920)
    assert label == "[treated]"
    assert "gblur=sigma=30.000" in graph
    assert "crop=1080:800" in graph
    assert "overlay=x=(W-w)/2:y=410.000[treated]" in graph


# test render graphic layers builds style neutral primitives
def test_render_graphic_layers_builds_style_neutral_primitives(tmp_path: Path) -> None:
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(logo)
    graphics = [
        {
            "type": "text",
            "text": "A CLEAR IDEA",
            "start": 0,
            "duration": 2,
            "x": 0.5,
            "y": 0.15,
            "font_size": 0.08,
            "max_width": 0.9,
        },
        {
            "type": "line",
            "start": 0,
            "end": 2,
            "x1": 0.1,
            "y1": 0.3,
            "x2": 0.9,
            "y2": 0.3,
            "color": "#00FF00",
        },
        {
            "type": "box",
            "start": 0.5,
            "duration": 1,
            "x": 0.1,
            "y": 0.7,
            "width": 0.8,
            "height": 0.1,
            "color": "#00000080",
        },
        {
            "type": "image",
            "file": "logo.png",
            "start": 1,
            "duration": 1,
            "x": 0.5,
            "y": 0.5,
            "width": 40,
            "height": 40,
        },
    ]
    overlays = visuals.render_graphic_layers(
        graphics,
        tmp_path / "generated",
        width=360,
        height=640,
        base_dir=tmp_path,
    )
    assert len(overlays) == 4
    assert overlays[0]["start_in_output"] == 0
    assert overlays[1]["duration"] == 2
    assert all(Path(overlay["file"]).is_file() for overlay in overlays)
    assert all(overlay["kind"] == "image" for overlay in overlays)


# test text graphic wraps to its requested width
def test_text_graphic_wraps_to_its_requested_width() -> None:
    image = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    font, text = visuals._fit_font(
        ImageDraw.Draw(image),
        "A LONG HERO TITLE THAT NEEDS WRAPPING",
        {"font_size": 48, "min_font_size": 20, "max_width": 0.45, "max_lines": 4},
        400,
        300,
    )
    assert font.size >= 20
    assert "\n" in text


# test text graphic rejects clipped content
def test_text_graphic_rejects_clipped_content(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exceeds the canvas"):
        visuals.render_graphic_layers(
            [
                {
                    "type": "text",
                    "text": "OUTSIDE",
                    "start": 0,
                    "duration": 1,
                    "x": 0,
                    "y": 0,
                    "font_size": 40,
                }
            ],
            tmp_path,
            width=320,
            height=180,
            base_dir=tmp_path,
        )


# graphic output directories never delete existing inputs or previous renders
def test_graphics_preserve_existing_files(tmp_path):
    existing = tmp_path / "graphic_000.png"
    existing.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        visuals.render_graphic_layers(
            [], tmp_path, width=64, height=64, base_dir=tmp_path
        )
    assert existing.read_bytes() == b"keep"


# nonfinite timing fails without writing a graphic image
@pytest.mark.parametrize(
    "timing",
    [{"start": float("nan"), "duration": 1}, {"start": 0, "duration": float("inf")}],
)
def test_graphics_reject_nonfinite_timing(tmp_path, timing):
    with pytest.raises(ValueError, match="timing"):
        visuals.render_graphic_layers(
            [{"type": "box", "width": 32, "height": 32, **timing}],
            tmp_path / "out",
            width=64,
            height=64,
            base_dir=tmp_path,
        )
    assert not list((tmp_path / "out").glob("*.png"))


# generated canvas filters execute successfully with actual ffmpeg input
@pytest.mark.parametrize("fit", ["contain", "cover", "blur"])
def test_canvas_filters_encode(fit, tmp_path):
    import subprocess

    parts, label, dimensions = visuals.build_treatment_filters(
        "[0:v]",
        {"canvas": {"width": 64, "height": 96, "fit": fit}},
        fallback_width=96,
        fallback_height=64,
    )
    output = tmp_path / "canvas.png"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=96x64:r=1",
            "-filter_complex",
            ";".join(parts),
            "-map",
            label,
            "-frames:v",
            "1",
            str(output),
        ],
        check=True,
    )
    with Image.open(output) as image:
        assert image.size == dimensions == (64, 96)


# negative graphic coordinates preserve their direction when previewed
def test_review_negative_preview_coordinate():
    from helpers.visuals import _scaled_pixel_value
    assert _scaled_pixel_value(-100, 0.5) == -50
