"""Style-neutral composition and graphic-layer primitives for Video Use.

The functions in this module translate explicit EDL treatment decisions into
FFmpeg filters and transparent PNG layers.  They intentionally do not provide
genre presets: the agent chooses the canvas, framing, type, color, placement,
and timing that fit the material.
"""

from __future__ import annotations

import copy
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


FONT_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    str(Path(__file__).resolve().parents[1] / "assets/fonts/AlfaSlabOne.ttf"),
)


# read picture dimensions duration and frame rate from ffprobe
def probe_video(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration:stream=width,height,avg_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise ValueError(f"no video stream in {path}")
    stream = streams[0]
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": str(stream.get("avg_frame_rate") or "0/0"),
        "duration": float(data.get("format", {}).get("duration", 0.0)),
    }


# round output dimensions down to even encoder compatible pixels
def _even_dimension(value: Any, name: str) -> int:
    dimension = int(value)
    if dimension < 2:
        raise ValueError(f"{name} must be at least 2")
    return dimension if dimension % 2 == 0 else dimension - 1


# resolve the requested output canvas or retain the source size
def canvas_dimensions(
    treatment: dict[str, Any] | None,
    fallback_width: int,
    fallback_height: int,
) -> tuple[int, int]:
    canvas = (treatment or {}).get("canvas")
    if not isinstance(canvas, dict):
        return fallback_width, fallback_height
    return (
        _even_dimension(canvas.get("width", fallback_width), "canvas.width"),
        _even_dimension(canvas.get("height", fallback_height), "canvas.height"),
    )


# scale pixel measurements while retaining fractional coordinates
def _scaled_pixel_value(value: Any, scale: float) -> Any:
    """Scale an explicit pixel value while preserving relative fractions.

    The visual parsers treat every numeric value from zero through one as a
    fraction of the canvas. Keep scaled pixel values just above that boundary
    so a small 2px stroke cannot accidentally become a 100%-of-canvas stroke.
    """
    if value is None or isinstance(value, bool):
        return value
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value
    if 0.0 <= numeric <= 1.0:
        return value
    scaled = numeric * scale
    return max(1.000001, scaled) if numeric >= 0 else scaled


# resize graphics and canvas settings together for previews
def scale_visual_specs(
    treatment: dict[str, Any] | None,
    graphics: list[dict[str, Any]] | None,
    captions: dict[str, Any] | None,
    *,
    fallback_width: int,
    fallback_height: int,
    max_dimension: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], float]:
    """Return visual specs scaled to a bounded preview canvas.

    EDL values between zero and one are relative coordinates or sizes and must
    remain unchanged. Values greater than one are pixels and scale with the
    canvas. The source objects are deep-copied so preview rendering never
    mutates the final-delivery EDL.
    """
    if max_dimension < 2:
        raise ValueError("max_dimension must be at least 2")

    scaled_treatment = copy.deepcopy(treatment or {})
    scaled_graphics = copy.deepcopy(graphics or [])
    scaled_captions = copy.deepcopy(captions or {})
    width, height = canvas_dimensions(scaled_treatment, fallback_width, fallback_height)
    scale = min(1.0, max_dimension / max(width, height))
    if scale >= 1.0:
        return scaled_treatment, scaled_graphics, scaled_captions, 1.0

    canvas = scaled_treatment.get("canvas")
    if isinstance(canvas, dict):
        canvas["width"] = _even_dimension(round(width * scale), "canvas.width")
        canvas["height"] = _even_dimension(round(height * scale), "canvas.height")
        if "blur" in canvas:
            canvas["blur"] = _scaled_pixel_value(canvas["blur"], scale)
        foreground = canvas.get("foreground")
        if isinstance(foreground, dict):
            for key in ("width", "height"):
                if key in foreground:
                    foreground[key] = _even_dimension(
                        round(float(foreground[key]) * scale),
                        f"canvas.foreground.{key}",
                    )
            for key in ("x", "y"):
                if key in foreground:
                    foreground[key] = _scaled_pixel_value(foreground[key], scale)

    graphic_pixel_fields = {
        "x",
        "y",
        "width",
        "height",
        "x1",
        "y1",
        "x2",
        "y2",
        "font_size",
        "min_font_size",
        "max_width",
        "stroke_width",
        "line_spacing",
        "radius",
        "outline_width",
        "line_width",
    }
    for graphic in scaled_graphics:
        if not isinstance(graphic, dict):
            continue
        for key in graphic_pixel_fields.intersection(graphic):
            graphic[key] = _scaled_pixel_value(graphic[key], scale)

    caption_pixel_fields = {
        "font_size",
        "min_font_size",
        "max_width",
        "x",
        "y",
        "stroke_width",
        "line_spacing",
        "shadow_x",
        "shadow_y",
        "background_padding_x",
        "background_padding_y",
        "background_radius",
    }
    for key in caption_pixel_fields.intersection(scaled_captions):
        scaled_captions[key] = _scaled_pixel_value(scaled_captions[key], scale)

    return scaled_treatment, scaled_graphics, scaled_captions, scale


# construct an aspect preserving zoom and focus crop
def build_reframe_filter(spec: dict[str, Any] | None) -> str:
    """Return a same-size static focus crop for a segment or full composition."""
    if not spec:
        return ""
    zoom = float(spec.get("zoom", 1.0))
    focus_x = float(spec.get("focus_x", 0.5))
    focus_y = float(spec.get("focus_y", 0.5))
    if not 1.0 <= zoom <= 3.0:
        raise ValueError("reframe.zoom must be between 1 and 3")
    if not 0.0 <= focus_x <= 1.0 or not 0.0 <= focus_y <= 1.0:
        raise ValueError("reframe focus values must be between 0 and 1")
    if zoom == 1.0:
        return ""
    return (
        f"scale=ceil(iw*{zoom:.6f}/2)*2:ceil(ih*{zoom:.6f}/2)*2,"
        f"crop=trunc(iw/{zoom:.6f}/2)*2:trunc(ih/{zoom:.6f}/2)*2:"
        f"(iw-ow)*{focus_x:.6f}:(ih-oh)*{focus_y:.6f}"
    )


# validate hexadecimal canvas colors for the filter graph
def _ffmpeg_color(value: Any, default: str = "000000") -> str:
    text = str(value or default).strip().lstrip("#")
    if len(text) == 8:
        text = text[:6]
    if len(text) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        raise ValueError("canvas colors must use #RRGGBB")
    return f"0x{text}"


# translate centered fractional or pixel placement into filter expressions
def _position_expression(value: Any, axis: str) -> str:
    if value is None or value == "center":
        return f"({axis}-{'w' if axis == 'W' else 'h'})/2"
    numeric = float(value)
    if 0.0 <= numeric <= 1.0:
        return f"{axis}*{numeric:.6f}"
    return f"{numeric:.3f}"


# compose contain cover or blurred background canvas treatments
def build_treatment_filters(
    input_label: str,
    treatment: dict[str, Any] | None,
    *,
    fallback_width: int,
    fallback_height: int,
    output_label: str = "[treated]",
) -> tuple[list[str], str, tuple[int, int]]:
    """Build filter-graph fragments for global focus and canvas treatment."""
    treatment = treatment or {}
    parts: list[str] = []
    current = input_label
    reframe = build_reframe_filter(treatment.get("reframe"))
    if reframe:
        parts.append(f"{current}{reframe}[focused]")
        current = "[focused]"

    canvas = treatment.get("canvas")
    if not isinstance(canvas, dict):
        if parts:
            parts.append(f"{current}null{output_label}")
            return parts, output_label, (fallback_width, fallback_height)
        return parts, current, (fallback_width, fallback_height)

    width, height = canvas_dimensions(treatment, fallback_width, fallback_height)
    fit = str(canvas.get("fit", "contain")).lower()
    background = canvas.get("background", "#000000")
    if fit not in {"cover", "contain", "blur"}:
        raise ValueError("treatment.canvas.fit must be cover, contain, or blur")

    if fit == "cover":
        parts.append(
            f"{current}scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}{output_label}"
        )
        return parts, output_label, (width, height)

    if fit == "contain":
        color = _ffmpeg_color(background)
        parts.append(
            f"{current}scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={color}{output_label}"
        )
        return parts, output_label, (width, height)

    blur = float(canvas.get("blur", 24.0))
    brightness = float(canvas.get("brightness", -0.15))
    saturation = float(canvas.get("saturation", 0.82))
    if not 0.0 <= blur <= 100.0:
        raise ValueError("treatment.canvas.blur must be between 0 and 100")
    if not -1.0 <= brightness <= 1.0 or not 0.0 <= saturation <= 3.0:
        raise ValueError(
            "canvas brightness or saturation is outside the supported range"
        )
    foreground = canvas.get("foreground")
    foreground = foreground if isinstance(foreground, dict) else {}
    foreground_width = foreground.get("width")
    foreground_height = foreground.get("height")
    foreground_fit = str(foreground.get("fit", "contain")).lower()
    if foreground_fit not in {"cover", "contain"}:
        raise ValueError("canvas.foreground.fit must be cover or contain")

    parts.append(f"{current}split=2[canvas_bg_src][canvas_fg_src]")
    parts.append(
        f"[canvas_bg_src]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma={blur:.3f},"
        f"eq=brightness={brightness:.3f}:saturation={saturation:.3f}[canvas_bg]"
    )
    if foreground_width is not None and foreground_height is not None:
        fg_width = _even_dimension(foreground_width, "canvas.foreground.width")
        fg_height = _even_dimension(foreground_height, "canvas.foreground.height")
        force = "increase" if foreground_fit == "cover" else "decrease"
        fg_filter = f"scale={fg_width}:{fg_height}:force_original_aspect_ratio={force}"
        if foreground_fit == "cover":
            fg_filter += f",crop={fg_width}:{fg_height}"
    elif foreground_width is not None:
        fg_width = _even_dimension(foreground_width, "canvas.foreground.width")
        fg_filter = f"scale={fg_width}:-2"
    elif foreground_height is not None:
        fg_height = _even_dimension(foreground_height, "canvas.foreground.height")
        fg_filter = f"scale=-2:{fg_height}"
    else:
        fg_filter = f"scale={width}:{height}:force_original_aspect_ratio=decrease"
    parts.append(f"[canvas_fg_src]{fg_filter}[canvas_fg]")
    x = _position_expression(foreground.get("x"), "W")
    y = _position_expression(foreground.get("y"), "H")
    parts.append(f"[canvas_bg][canvas_fg]overlay=x={x}:y={y}{output_label}")
    return parts, output_label, (width, height)


# decode explicit RGB or RGBA graphic colors
def _parse_color(
    value: Any, default: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    if value is None:
        return default
    text = str(value).strip().lstrip("#")
    if len(text) == 6:
        text += "FF"
    if len(text) != 8 or not re.fullmatch(r"[0-9a-fA-F]{8}", text):
        raise ValueError("graphic colors must use #RRGGBB or #RRGGBBAA")
    return tuple(int(text[index : index + 2], 16) for index in range(0, 8, 2))  # type: ignore[return-value]


# resolve a coordinate expressed as pixels or a canvas fraction
def _pixel(value: Any, extent: int, default: float = 0.0) -> int:
    numeric = default if value is None else float(value)
    if 0.0 <= numeric <= 1.0:
        return int(round(numeric * extent))
    return int(round(numeric))


# load the configured font or a usable local fallback
def _load_font(spec: dict[str, Any], size: int) -> ImageFont.FreeTypeFont:
    configured = spec.get("font_path")
    candidates = (str(configured),) if configured else FONT_CANDIDATES
    font_index = int(spec.get("font_index", 0))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size, index=font_index)
            except (OSError, ValueError):
                continue
    raise RuntimeError("no usable graphic font found; set graphics[].font_path")


# break text into lines that fit the measured width
def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    stroke_width: int,
) -> list[str]:
    wrapped: list[str] = []
    for paragraph in text.splitlines() or [text]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            candidate_width = draw.textbbox(
                (0, 0), candidate, font=font, stroke_width=stroke_width
            )[2]
            if current and candidate_width > max_width:
                wrapped.append(current)
                current = word
            else:
                current = candidate
        wrapped.append(current)
    return wrapped or [text]


# shrink and wrap all text within the requested line budget
def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    spec: dict[str, Any],
    width: int,
    height: int,
) -> tuple[ImageFont.FreeTypeFont, str]:
    initial = max(12, _pixel(spec.get("font_size"), height, 0.05))
    minimum = max(10, _pixel(spec.get("min_font_size"), height, 0.02))
    max_width = max(1, _pixel(spec.get("max_width"), width, 0.9))
    max_lines = max(1, min(8, int(spec.get("max_lines", 3))))
    stroke_width = max(0, _pixel(spec.get("stroke_width"), height, 0.0))
    for size in range(max(initial, minimum), minimum - 1, -1):
        font = _load_font(spec, size)
        lines = _wrap_text(draw, text, font, max_width, stroke_width)
        widest = max(
            draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)[2]
            for line in lines
        )
        if len(lines) <= max_lines and widest <= max_width:
            return font, "\n".join(lines)
    raise ValueError(
        "text graphic cannot fit max_width and max_lines; adjust its layout"
    )


# draw styled text and reject canvas clipping
def _render_text(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    spec: dict[str, Any],
    width: int,
    height: int,
) -> None:
    text = str(spec.get("text") or "")
    if not text:
        raise ValueError("text graphic requires non-empty text")
    font, text = _fit_font(draw, text, spec, width, height)
    stroke_width = max(0, _pixel(spec.get("stroke_width"), height, 0.0))
    fill = _parse_color(spec.get("color"), (255, 255, 255, 255))
    stroke = _parse_color(spec.get("stroke_color"), (0, 0, 0, 255))
    spacing = _pixel(spec.get("line_spacing"), height, 0.008)
    align = str(spec.get("align", "center"))
    if align not in {"left", "center", "right"}:
        raise ValueError("text graphic align must be left, center, or right")
    box = draw.multiline_textbbox(
        (0, 0), text, font=font, spacing=spacing, align=align, stroke_width=stroke_width
    )
    text_width = box[2] - box[0]
    text_height = box[3] - box[1]
    x = _pixel(spec.get("x"), width, 0.5)
    y = _pixel(spec.get("y"), height, 0.5)
    anchor = str(spec.get("anchor", "center"))
    if anchor == "center":
        x -= text_width // 2 + box[0]
        y -= text_height // 2 + box[1]
    elif anchor == "top_left":
        x -= box[0]
        y -= box[1]
    elif anchor == "top_center":
        x -= text_width // 2 + box[0]
        y -= box[1]
    else:
        raise ValueError("text graphic anchor must be center, top_left, or top_center")
    if x + box[0] < 0 or y + box[1] < 0 or x + box[2] > width or y + box[3] > height:
        raise ValueError(
            "text graphic exceeds the canvas; adjust position or max_width"
        )
    draw.multiline_text(
        (x, y),
        text,
        font=font,
        fill=fill,
        spacing=spacing,
        align=align,
        stroke_width=stroke_width,
        stroke_fill=stroke,
    )


# draw a positioned rectangular graphic
def _render_box(
    draw: ImageDraw.ImageDraw,
    spec: dict[str, Any],
    width: int,
    height: int,
) -> None:
    x = _pixel(spec.get("x"), width)
    y = _pixel(spec.get("y"), height)
    box_width = _pixel(spec.get("width"), width)
    box_height = _pixel(spec.get("height"), height)
    if (
        box_width <= 0
        or box_height <= 0
        or x < 0
        or y < 0
        or x + box_width > width
        or y + box_height > height
    ):
        raise ValueError("box graphic must fit inside the canvas")
    radius = max(0, _pixel(spec.get("radius"), min(width, height)))
    outline_width = max(0, _pixel(spec.get("outline_width"), min(width, height)))
    draw.rounded_rectangle(
        (x, y, x + box_width, y + box_height),
        radius=radius,
        fill=_parse_color(spec.get("color"), (255, 255, 255, 255)),
        outline=_parse_color(spec.get("outline_color"), (0, 0, 0, 0)),
        width=outline_width,
    )


# draw a line between declared canvas coordinates
def _render_line(
    draw: ImageDraw.ImageDraw,
    spec: dict[str, Any],
    width: int,
    height: int,
) -> None:
    points = (
        _pixel(spec.get("x1"), width),
        _pixel(spec.get("y1"), height),
        _pixel(spec.get("x2"), width),
        _pixel(spec.get("y2"), height),
    )
    if not (
        0 <= points[0] <= width
        and 0 <= points[2] <= width
        and 0 <= points[1] <= height
        and 0 <= points[3] <= height
    ):
        raise ValueError("line graphic endpoints must fit inside the canvas")
    line_width = max(1, _pixel(spec.get("line_width"), min(width, height), 0.003))
    draw.line(
        points,
        fill=_parse_color(spec.get("color"), (255, 255, 255, 255)),
        width=line_width,
    )


# place a supplied image while retaining its transparency
def _render_image(
    image: Image.Image,
    spec: dict[str, Any],
    width: int,
    height: int,
    base_dir: Path,
) -> None:
    raw_path = Path(str(spec.get("file") or ""))
    source_path = (
        raw_path if raw_path.is_absolute() else (base_dir / raw_path).resolve()
    )
    if not source_path.is_file():
        raise ValueError(f"graphic image not found: {source_path}")
    source = Image.open(source_path).convert("RGBA")
    target_width = _pixel(spec.get("width"), width, source.width)
    target_height = _pixel(spec.get("height"), height, source.height)
    if target_width <= 0 or target_height <= 0:
        raise ValueError("image graphic dimensions must be positive")
    source.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
    x = _pixel(spec.get("x"), width, 0.5) - source.width // 2
    y = _pixel(spec.get("y"), height, 0.5) - source.height // 2
    if x < 0 or y < 0 or x + source.width > width or y + source.height > height:
        raise ValueError("image graphic exceeds the canvas")
    opacity = float(spec.get("opacity", 1.0))
    if not 0.0 <= opacity <= 1.0:
        raise ValueError("image graphic opacity must be between 0 and 1")
    if opacity < 1.0:
        alpha = source.getchannel("A").point(lambda value: int(value * opacity))
        source.putalpha(alpha)
    image.alpha_composite(source, (x, y))


# write transparent graphic images with explicit output timing
def render_graphic_layers(
    graphics: list[dict[str, Any]],
    output_dir: Path,
    *,
    width: int,
    height: int,
    base_dir: Path,
) -> list[dict[str, Any]]:
    """Render EDL graphic specs to full-canvas PNG overlay entries."""
    if output_dir.is_symlink() or (output_dir.exists() and any(output_dir.iterdir())):
        raise FileExistsError("graphic output directory must be new or empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    overlays: list[dict[str, Any]] = []
    for index, spec in enumerate(graphics):
        if not isinstance(spec, dict):
            raise ValueError("each graphics entry must be an object")
        graphic_type = str(spec.get("type", "text")).lower()
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image, "RGBA")
        if graphic_type == "text":
            _render_text(image, draw, spec, width, height)
        elif graphic_type == "box":
            _render_box(draw, spec, width, height)
        elif graphic_type == "line":
            _render_line(draw, spec, width, height)
        elif graphic_type == "image":
            _render_image(image, spec, width, height, base_dir)
        else:
            raise ValueError("graphics[].type must be text, box, line, or image")
        output_path = output_dir / f"graphic_{index:03d}.png"
        start = float(spec.get("start_in_output", spec.get("start", 0.0)))
        if "duration" in spec:
            duration = float(spec["duration"])
        elif "end" in spec:
            duration = float(spec["end"]) - start
        else:
            raise ValueError("graphics entries require duration or end")
        if (
            not math.isfinite(start)
            or not math.isfinite(duration)
            or start < 0
            or duration <= 0
        ):
            raise ValueError("graphic timing must have start >= 0 and duration > 0")
        image.save(output_path, compress_level=1)
        overlays.append(
            {
                "file": str(output_path),
                "start_in_output": start,
                "duration": duration,
                "kind": "image",
            }
        )
    return overlays
