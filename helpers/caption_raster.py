"""Caption timing and raster rendering helpers.

The renderer prefers FFmpeg's subtitles filter when it is available and the
EDL does not request raster-specific styling.  PIL is the deterministic
fallback: it produces full-canvas transparent cue images which can be overlaid
as the final visual operation in the same FFmpeg filter graph as other layers.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


PUNCT_BREAK = set(".,!?;:")
FONT_CANDIDATES = (
    str(
        Path(__file__).resolve().parents[1]
        / "assets/fonts/AlfaSlabOne.ttf"
    ),
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


# keep one caption interval and its display text together
@dataclass(frozen=True)
class CaptionCue:
    start: float
    end: float
    text: str


# format output seconds on the SRT millisecond clock
def srt_timestamp(seconds: float) -> str:
    total_ms = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


# read hours minutes seconds and milliseconds from an SRT timestamp
def parse_srt_timestamp(value: str) -> float:
    hours, minutes, rest = value.strip().replace(".", ",").split(":")
    seconds, milliseconds = rest.split(",", 1)
    return (
        int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000
    )


# load valid nonempty subtitle intervals in presentation order
def parse_srt(path: Path) -> list[CaptionCue]:
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    if not raw:
        return []
    cues: list[CaptionCue] = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or " --> " not in lines[1]:
            continue
        start_text, end_text = lines[1].split(" --> ", 1)
        start = parse_srt_timestamp(start_text)
        end = parse_srt_timestamp(end_text)
        text = " ".join(lines[2:]).strip()
        if text and end > start:
            cues.append(CaptionCue(start, end, text))
    return sorted(cues, key=lambda cue: cue.start)


# read the optional caption style block from the edit manifest
def _caption_config(edl: dict[str, Any]) -> dict[str, Any]:
    value = edl.get("captions")
    return value if isinstance(value, dict) else {}


# select timestamped words that overlap one chosen source range
def _words_in_range(
    transcript: dict[str, Any], start: float, end: float
) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for word in transcript.get("words", []):
        if word.get("type") != "word":
            continue
        word_start = word.get("start")
        word_end = word.get("end")
        if word_start is None or word_end is None:
            continue
        if float(word_end) <= start or float(word_start) >= end:
            continue
        words.append(word)
    return words


# group words without losing text at punctuation or size boundaries
def chunk_words(
    words: list[dict[str, Any]],
    *,
    max_words: int = 2,
    break_on_punctuation: bool = True,
) -> list[list[dict[str, Any]]]:
    if not 1 <= max_words <= 12:
        raise ValueError("captions.max_words must be between 1 and 12")
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        text = str(word.get("text") or "").strip()
        if not text:
            continue
        current.append(word)
        punctuated = bool(text) and text[-1] in PUNCT_BREAK
        if len(current) >= max_words or (break_on_punctuation and punctuated):
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


# apply the requested case and trailing punctuation policy
def _transform_text(text: str, config: dict[str, Any]) -> str:
    case = str(config.get("case", "upper")).lower()
    if case == "upper":
        text = text.upper()
    elif case == "title":
        text = text.title()
    elif case != "natural":
        raise ValueError("captions.case must be upper, title, or natural")
    if bool(config.get("strip_trailing_punctuation", case == "upper")):
        text = text.rstrip(",;:")
    return text


# translate selected source word times onto the concatenated output timeline
def build_master_srt(
    edl: dict[str, Any], edit_dir: Path, out_path: Path
) -> list[CaptionCue]:
    """Build output-timeline captions using the optional EDL ``captions`` block."""
    config = _caption_config(edl)
    max_words = int(config.get("max_words", 2))
    break_on_punctuation = bool(config.get("break_on_punctuation", True))
    transcripts_dir = edit_dir / "transcripts"
    entries: list[CaptionCue] = []
    segment_offset = 0.0

    for selected_range in edl["ranges"]:
        source_name = selected_range["source"]
        segment_start = float(selected_range["start"])
        segment_end = float(selected_range["end"])
        segment_duration = segment_end - segment_start
        transcript_path = transcripts_dir / f"{source_name}.json"
        if not transcript_path.exists():
            print(
                f"  no transcript for {source_name}, skipping captions for this segment"
            )
            segment_offset += segment_duration
            continue

        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        words = _words_in_range(transcript, segment_start, segment_end)
        chunks = chunk_words(
            words,
            max_words=max_words,
            break_on_punctuation=break_on_punctuation,
        )
        for chunk in chunks:
            local_start = max(
                segment_start, float(chunk[0].get("start", segment_start))
            )
            local_end = min(segment_end, float(chunk[-1].get("end", segment_end)))
            output_start = max(0.0, local_start - segment_start) + segment_offset
            output_end = max(0.0, local_end - segment_start) + segment_offset
            if output_end <= output_start:
                output_end = output_start + 0.4
            text = " ".join(str(word.get("text") or "").strip() for word in chunk)
            text = re.sub(r"\s+", " ", text).strip()
            entries.append(
                CaptionCue(output_start, output_end, _transform_text(text, config))
            )
        segment_offset += segment_duration

    lines: list[str] = []
    for index, cue in enumerate(sorted(entries, key=lambda item: item.start), start=1):
        lines.extend(
            (
                str(index),
                f"{srt_timestamp(cue.start)} --> {srt_timestamp(cue.end)}",
                cue.text,
                "",
            )
        )
    if out_path.exists() or out_path.is_symlink():
        raise FileExistsError("choose a new subtitle output path")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"master SRT → {out_path.name} ({len(entries)} cues)")
    return entries


# decode explicit RGB or RGBA colors with a supplied default
def _parse_color(
    value: Any, default: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    if value is None:
        return default
    text = str(value).strip().lstrip("#")
    if len(text) == 6:
        text += "FF"
    if len(text) != 8 or not re.fullmatch(r"[0-9a-fA-F]{8}", text):
        raise ValueError("caption colors must use #RRGGBB or #RRGGBBAA")
    return tuple(int(text[index : index + 2], 16) for index in range(0, 8, 2))  # type: ignore[return-value]


# load the configured font or a bundled fallback at the requested size
def _load_font(config: dict[str, Any], size: int) -> ImageFont.FreeTypeFont:
    configured = config.get("font_path")
    candidates = (str(configured),) if configured else FONT_CANDIDATES
    font_index = int(config.get("font_index", 0))
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            return ImageFont.truetype(candidate, size, index=font_index)
        except (OSError, ValueError):
            continue
    raise RuntimeError(
        "no usable caption font found; set captions.font_path in the EDL"
    )


# convert relative layout values into canvas pixels
def _pixel_value(value: Any, extent: int, default_fraction: float) -> int:
    if value is None:
        return int(round(extent * default_fraction))
    numeric = float(value)
    if 0.0 <= numeric <= 1.0:
        return int(round(extent * numeric))
    return int(round(numeric))


# wrap words to the measured line width
def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    stroke_width: int = 0,
) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        box = draw.textbbox((0, 0), candidate, font=font, stroke_width=stroke_width)
        width = box[2] - box[0]
        if current and width > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [text]


# shrink text until all words fit within the declared layout
def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    config: dict[str, Any],
    width: int,
    height: int,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    max_width = _pixel_value(config.get("max_width"), width, 0.85)
    max_lines = max(1, min(4, int(config.get("max_lines", 2))))
    initial_size = _pixel_value(config.get("font_size"), height, 0.045)
    minimum_size = max(12, _pixel_value(config.get("min_font_size"), height, 0.025))
    stroke_width = _pixel_value(config.get("stroke_width"), height, 0.004)
    for size in range(max(initial_size, minimum_size), minimum_size - 1, -1):
        font = _load_font(config, size)
        lines = _wrap_text(draw, text, font, max_width, stroke_width)
        boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width) for line in lines]
        widest = max(box[2] - box[0] for box in boxes)
        if len(lines) <= max_lines and widest <= max_width:
            return font, lines
    raise ValueError(
        "caption cannot fit max_width and max_lines; shorten the chunk or adjust its layout"
    )


# draw a transparent caption image and reject clipped placement
def render_caption_image(
    cue: CaptionCue,
    output_path: Path,
    *,
    width: int,
    height: int,
    config: dict[str, Any] | None = None,
) -> None:
    config = config or {}
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image, "RGBA")
    font, lines = _fit_text(draw, cue.text, config, width, height)
    stroke_width = _pixel_value(config.get("stroke_width"), height, 0.004)
    line_spacing = _pixel_value(config.get("line_spacing"), height, 0.008)
    fill = _parse_color(config.get("fill"), (255, 255, 255, 255))
    stroke = _parse_color(config.get("stroke"), (0, 0, 0, 255))
    shadow = _parse_color(config.get("shadow"), (0, 0, 0, 150))
    shadow_x = _pixel_value(config.get("shadow_x"), width, 0.003)
    shadow_y = _pixel_value(config.get("shadow_y"), height, 0.003)
    boxes = [
        draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
        for line in lines
    ]
    line_heights = [box[3] - box[1] for box in boxes]
    block_height = sum(line_heights) + line_spacing * max(0, len(lines) - 1)
    center_x = _pixel_value(config.get("x"), width, 0.5)
    center_y = _pixel_value(config.get("y"), height, 0.72)
    block_top = center_y - block_height // 2
    max_line_width = max(box[2] - box[0] for box in boxes)

    background = config.get("background")
    padding_x = 0
    padding_y = 0
    if background:
        padding_x = _pixel_value(config.get("background_padding_x"), width, 0.025)
        padding_y = _pixel_value(config.get("background_padding_y"), height, 0.012)
        radius = _pixel_value(
            config.get("background_radius"), min(width, height), 0.015
        )
        draw.rounded_rectangle(
            (
                center_x - max_line_width // 2 - padding_x,
                block_top - padding_y,
                center_x + max_line_width // 2 + padding_x,
                block_top + block_height + padding_y,
            ),
            radius=radius,
            fill=_parse_color(background, (0, 0, 0, 160)),
        )

    left = center_x - max_line_width // 2 - padding_x + min(0, shadow_x)
    right = center_x + (max_line_width + 1) // 2 + padding_x + max(0, shadow_x)
    top = block_top - padding_y + min(0, shadow_y)
    bottom = block_top + block_height + padding_y + max(0, shadow_y)
    if left < 0 or top < 0 or right > width or bottom > height:
        raise ValueError("caption exceeds the canvas; adjust its position or max_width")

    cursor_y = block_top
    for line, box, line_height in zip(lines, boxes, line_heights):
        line_width = box[2] - box[0]
        x = center_x - line_width // 2 - box[0]
        y = cursor_y - box[1]
        if shadow[3] > 0:
            draw.text(
                (x + shadow_x, y + shadow_y),
                line,
                font=font,
                fill=shadow,
                stroke_width=stroke_width,
                stroke_fill=shadow,
            )
        draw.text(
            (x, y),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke,
        )
        cursor_y += line_height + line_spacing

    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError("choose a new caption image path")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, compress_level=1)


# escape image paths for the FFconcat manifest
def _quote_concat_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


# write caption and blank intervals without stretching short cues
def build_caption_track(
    srt_path: Path,
    output_dir: Path,
    *,
    width: int,
    height: int,
    total_duration: float,
    config: dict[str, Any] | None = None,
) -> Path:
    """Render caption cue images and return an FFconcat timeline path."""
    if (
        not math.isfinite(total_duration)
        or total_duration <= 0
        or width <= 0
        or height <= 0
    ):
        raise ValueError(
            "caption track needs positive dimensions and a finite positive duration"
        )
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("choose an empty caption output directory")
    cues = parse_srt(srt_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    blank_path = output_dir / "blank.png"
    concat_path = output_dir / "captions.ffconcat"
    Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(blank_path, compress_level=1)

    intervals: list[tuple[Path, float]] = []
    cursor = 0.0
    for index, cue in enumerate(cues, start=1):
        start = min(total_duration, max(cursor, cue.start))
        end = min(total_duration, max(start, cue.end))
        if start > cursor:
            intervals.append((blank_path, start - cursor))
            cursor = start
        if end <= start:
            continue
        cue_path = output_dir / f"caption_{index:04d}.png"
        render_caption_image(
            CaptionCue(start, end, cue.text),
            cue_path,
            width=width,
            height=height,
            config=config,
        )
        intervals.append((cue_path, end - start))
        cursor = end
    if cursor < total_duration:
        intervals.append((blank_path, total_duration - cursor))
    if not intervals:
        intervals.append((blank_path, total_duration))

    lines = ["ffconcat version 1.0"]
    for image_path, duration in intervals:
        lines.append(f"file '{_quote_concat_path(image_path)}'")
        lines.append("option framerate 1000")
        lines.append(f"duration {duration:.6f}")
    lines.append(f"file '{_quote_concat_path(blank_path)}'")
    lines.append("option framerate 1000")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return concat_path


# probe the local FFmpeg build for subtitle rendering support
@lru_cache(maxsize=1)
def ffmpeg_has_subtitle_filter() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(re.search(r"\bsubtitles\b", result.stdout + result.stderr))


# choose the explicit backend or a compatible automatic fallback
def choose_caption_renderer(
    config: dict[str, Any] | None = None, *, libass_available: bool | None = None
) -> str:
    config = config or {}
    requested = str(config.get("renderer", "auto")).lower()
    if requested not in {"auto", "libass", "pil"}:
        raise ValueError("captions.renderer must be auto, libass, or pil")
    if requested == "pil":
        return "pil"
    if libass_available is None:
        libass_available = ffmpeg_has_subtitle_filter()
    if requested == "libass" and not libass_available:
        print("warning: FFmpeg subtitles filter unavailable; using PIL captions")
        return "pil"
    if requested == "libass":
        return "libass"

    raster_style_keys = {
        "font_path",
        "font_index",
        "font_size",
        "min_font_size",
        "max_width",
        "max_lines",
        "x",
        "y",
        "fill",
        "stroke",
        "stroke_width",
        "shadow",
        "shadow_x",
        "shadow_y",
        "background",
        "background_padding_x",
        "background_padding_y",
        "background_radius",
        "line_spacing",
    }
    if raster_style_keys.intersection(config):
        return "pil"
    return "libass" if libass_available else "pil"
