"""Small layout guards for narrated 16:9 Manim explainers.

Import these helpers into a project's ``script.py``. They keep authored visuals
above the caption rail and make text fit its container before rendering.
"""

from __future__ import annotations

from itertools import combinations

from manim import DOWN, UP, Mobject, Rectangle, RoundedRectangle, Text, VGroup, config

# re exported so chapter scripts import portable easing names from this asset
from manim.utils.rate_functions import ease_in_out_cubic, ease_out_cubic  # noqa: F401


CAPTION_RAIL_FRACTION = 0.16

COMPOSITION_REGIONS = {
    "split_left": {
        "media": {"x": 0.04, "y": 0.17, "width": 0.44, "height": 0.57},
        "illustration": {"x": 0.52, "y": 0.17, "width": 0.44, "height": 0.57},
    },
    "split_right": {
        "media": {"x": 0.52, "y": 0.17, "width": 0.44, "height": 0.57},
        "illustration": {"x": 0.04, "y": 0.17, "width": 0.44, "height": 0.57},
    },
}


# turn a top left normalized rectangle into an invisible manim guide centered in frame space
def normalized_region(rect: dict[str, float]) -> Rectangle:
    """Convert a top-left normalized video rectangle into a Manim guide."""
    try:
        x, y, width, height = (float(rect[key]) for key in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("region requires numeric x, y, width, and height") from exc
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError("region must use non-negative x/y and positive width/height")
    if x + width > 1.000001 or y + height > 1.000001:
        raise ValueError("region must stay inside the normalized frame")
    guide = Rectangle(
        width=config.frame_width * width,
        height=config.frame_height * height,
        stroke_opacity=0,
        fill_opacity=0,
    )
    # convert normalized top left origin into manim center coordinates where y grows upward
    center_x = (x + width / 2 - 0.5) * config.frame_width
    center_y = (0.5 - y - height / 2) * config.frame_height
    guide.move_to((center_x, center_y, 0))
    return guide


# build the media and illustration guides for a named split layout
def composition_regions(mode: str) -> dict[str, Rectangle]:
    """Return non-overlapping media/illustration guides for a split scene."""
    if mode not in COMPOSITION_REGIONS:
        choices = ", ".join(sorted(COMPOSITION_REGIONS))
        raise ValueError(f"unknown composition '{mode}'; choose {choices}")
    return {
        name: normalized_region(rect)
        for name, rect in COMPOSITION_REGIONS[mode].items()
    }


# scale a mobject down but never up so it fits the given box
def fit_mobject(mobject: Mobject, max_width: float, max_height: float) -> Mobject:
    """Scale down, never up, until a mobject fits the requested box."""
    if max_width <= 0 or max_height <= 0:
        raise ValueError("max_width and max_height must be positive")
    scale = min(1.0, max_width / mobject.width, max_height / mobject.height)
    mobject.scale(scale)
    return mobject


# create text at the requested size and shrink it to fit the box if needed
def fitted_text(
    text: str,
    *,
    max_width: float,
    max_height: float,
    font: str = "Menlo",
    font_size: int = 48,
    **kwargs,
) -> Text:
    """Create text at the desired size, then shrink it to fit if needed."""
    return fit_mobject(
        Text(text, font=font, font_size=font_size, **kwargs),
        max_width,
        max_height,
    )


# wrap content in a rounded panel and shrink the content to fit inside the padding
def safe_panel(
    content: Mobject,
    *,
    width: float,
    height: float,
    padding_x: float = 0.35,
    padding_y: float = 0.28,
    corner_radius: float = 0.18,
    stroke_color: str = "#3B4B68",
    fill_color: str = "#101827",
    fill_opacity: float = 0.92,
) -> VGroup:
    """Return a panel whose content is guaranteed to fit its inner bounds."""
    panel = RoundedRectangle(
        width=width,
        height=height,
        corner_radius=corner_radius,
        stroke_color=stroke_color,
        stroke_width=2,
        fill_color=fill_color,
        fill_opacity=fill_opacity,
    )
    fit_mobject(content, width - 2 * padding_x, height - 2 * padding_y)
    content.move_to(panel)
    return VGroup(panel, content)


# create an invisible guide for the drawable area above the caption rail
def content_frame(
    *,
    caption_rail_fraction: float = CAPTION_RAIL_FRACTION,
    margin: float = 0.28,
) -> Rectangle:
    """Create an invisible guide for the portion of frame above captions."""
    if not 0 <= caption_rail_fraction < 0.5:
        raise ValueError("caption_rail_fraction must be between 0 and 0.5")
    rail_height = config.frame_height * caption_rail_fraction
    guide = Rectangle(
        width=config.frame_width - 2 * margin,
        height=config.frame_height - rail_height - 2 * margin,
        stroke_opacity=0,
        fill_opacity=0,
    )
    # shift the guide up by half the rail so it sits centered in the remaining area
    guide.shift(UP * rail_height / 2)
    return guide


# raise before render if a mobject leaves the frame or enters the caption rail
def assert_inside_frame(
    mobject: Mobject,
    *,
    caption_safe: bool = True,
    caption_rail_fraction: float = CAPTION_RAIL_FRACTION,
    margin: float = 0.18,
) -> Mobject:
    """Raise before render if a mobject leaves the frame or caption-safe area."""
    # compute the allowed bounds with the caption rail folded into the bottom edge
    left = -config.frame_width / 2 + margin
    right = config.frame_width / 2 - margin
    top = config.frame_height / 2 - margin
    rail_height = config.frame_height * caption_rail_fraction if caption_safe else 0
    bottom = -config.frame_height / 2 + rail_height + margin
    if (
        mobject.get_left()[0] < left
        or mobject.get_right()[0] > right
        or mobject.get_top()[1] > top
        or mobject.get_bottom()[1] < bottom
    ):
        raise ValueError("mobject leaves the frame or enters the reserved caption rail")
    return mobject


# raise when a mobject leaves its assigned composition region by more than the margin
def assert_inside_region(
    mobject: Mobject,
    region: Rectangle,
    *,
    margin: float = 0.08,
) -> Mobject:
    """Raise when a mobject leaves its assigned composition region."""
    if (
        mobject.get_left()[0] < region.get_left()[0] + margin
        or mobject.get_right()[0] > region.get_right()[0] - margin
        or mobject.get_top()[1] > region.get_top()[1] - margin
        or mobject.get_bottom()[1] < region.get_bottom()[1] + margin
    ):
        raise ValueError("mobject leaves its assigned composition region")
    return mobject


# describe a final state mobject as a normalized rectangle the edl can protect
def normalized_bounds(mobject: Mobject, *, padding: float = 0.0) -> dict[str, float]:
    """Return an EDL-ready protected rectangle for a final-state mobject."""
    if padding < 0:
        raise ValueError("padding must be non-negative")
    # clamp the padded bounds to the frame then convert to top left normalized units
    left = max(-config.frame_width / 2, mobject.get_left()[0] - padding)
    right = min(config.frame_width / 2, mobject.get_right()[0] + padding)
    top = min(config.frame_height / 2, mobject.get_top()[1] + padding)
    bottom = max(-config.frame_height / 2, mobject.get_bottom()[1] - padding)
    return {
        "x": (left + config.frame_width / 2) / config.frame_width,
        "y": (config.frame_height / 2 - top) / config.frame_height,
        "width": (right - left) / config.frame_width,
        "height": (top - bottom) / config.frame_height,
    }


# report whether two axis aligned bounds overlap once a gap is added
def mobjects_overlap(first: Mobject, second: Mobject, *, gap: float = 0.05) -> bool:
    """Return whether two axis-aligned Manim bounds overlap, including a gap."""
    return not (
        first.get_right()[0] + gap <= second.get_left()[0]
        or second.get_right()[0] + gap <= first.get_left()[0]
        or first.get_top()[1] + gap <= second.get_bottom()[1]
        or second.get_top()[1] + gap <= first.get_bottom()[1]
    )


# raise when any pair of final state mobjects overlap
def assert_no_overlap(*mobjects: Mobject, gap: float = 0.05) -> None:
    """Raise when any pair of final-state mobjects overlap."""
    for first, second in combinations(mobjects, 2):
        if mobjects_overlap(first, second, gap=gap):
            raise ValueError("final-state mobjects overlap; resize or reposition them")


# create a small source note that sits just above the caption rail
def source_footer(
    text: str,
    *,
    color: str = "#8E9BB5",
    font: str = "Menlo",
) -> Text:
    """Create a restrained source note just above the caption rail."""
    footer = fitted_text(
        text,
        max_width=config.frame_width - 1.0,
        max_height=0.28,
        font=font,
        font_size=18,
        color=color,
    )
    rail_height = config.frame_height * CAPTION_RAIL_FRACTION
    # the buffer must clear the margin that assert_inside_frame enforces
    footer.to_edge(DOWN, buff=rail_height + 0.2)
    return assert_inside_frame(footer)
