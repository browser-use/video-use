"""Shared construction helpers for the style-neutral semantic domain kit."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
from manim import (
    Arrow,
    Circle,
    DOWN,
    Mobject,
    RIGHT,
    RoundedRectangle,
    UP,
    VGroup,
)

try:
    from ..teaching import VisualTheme, fit_to_frame, theme_text
except ImportError:  # Supports copying ``teaching.py`` and ``domains/`` together.
    from teaching import VisualTheme, fit_to_frame, theme_text


# raise unless the value is a real VisualTheme
def require_theme(theme: VisualTheme) -> VisualTheme:
    if not isinstance(theme, VisualTheme):
        raise TypeError("domain components require a shared VisualTheme")
    return theme


# turn labels into strings or fall back to defaults and never return an empty list
def normalized_labels(labels: Iterable[object] | None, *, fallback: Sequence[str]) -> list[str]:
    values = [str(value) for value in labels] if labels is not None else list(fallback)
    return values or [""]


# make a themed label that shrinks to a max width
def label_for(
    value: object,
    theme: VisualTheme,
    *,
    size: float = 22,
    color: str | None = None,
    width: float = 1.55,
) -> Mobject:
    return theme_text(
        value,
        theme,
        role="label",
        font_size=size,
        color=color,
        max_width=max(width, 0.05),
    )


# make a rounded box with a centered label that fits inside it
def labeled_box(
    label: object,
    theme: VisualTheme,
    *,
    width: float = 1.8,
    height: float = 0.78,
    color: str | None = None,
    fill_opacity: float = 0.12,
    font_size: float = 22,
) -> VGroup:
    stroke = color or theme.primary
    box = RoundedRectangle(
        width=width,
        height=height,
        corner_radius=min(0.14, height / 4),
        color=stroke,
        stroke_width=2,
        fill_color=stroke,
        fill_opacity=fill_opacity,
    )
    text = label_for(label, theme, size=font_size, width=width - 0.18)
    return VGroup(box, text)


# make a circle with a centered label for graph style nodes
def circle_node(
    label: object,
    theme: VisualTheme,
    *,
    radius: float = 0.38,
    color: str | None = None,
) -> VGroup:
    stroke = color or theme.primary
    circle = Circle(
        radius=radius,
        color=stroke,
        stroke_width=2,
        fill_color=stroke,
        fill_opacity=0.12,
    )
    text = label_for(label, theme, size=19, width=radius * 1.55)
    return VGroup(circle, text)


# draw an arrow between two mobjects or two points
def arrow_between(
    start: Mobject | Sequence[float],
    end: Mobject | Sequence[float],
    theme: VisualTheme,
    *,
    color: str | None = None,
    buff: float = 0.12,
) -> Arrow:
    start_point = start.get_center() if isinstance(start, Mobject) else np.asarray(start, dtype=float)
    end_point = end.get_center() if isinstance(end, Mobject) else np.asarray(end, dtype=float)
    return Arrow(
        start_point,
        end_point,
        buff=buff,
        color=color or theme.muted,
        stroke_width=2.4,
        max_tip_length_to_length_ratio=0.16,
    )


# build a horizontal row of labeled boxes from labels or fallbacks
def row_of_boxes(
    labels: Iterable[object] | None,
    theme: VisualTheme,
    *,
    fallback: Sequence[str],
    color: str | None = None,
    box_width: float = 1.65,
    box_height: float = 0.74,
    buff: float = 0.42,
) -> VGroup:
    values = normalized_labels(labels, fallback=fallback)
    boxes = VGroup(
        *[
            labeled_box(
                value,
                theme,
                width=box_width,
                height=box_height,
                color=color,
            )
            for value in values
        ]
    ).arrange(RIGHT, buff=buff)
    return boxes


# draw a short arrow from each box in a row to the next one
def arrows_for_row(nodes: VGroup, theme: VisualTheme, *, color: str | None = None) -> VGroup:
    return VGroup(
        *[
            Arrow(
                nodes[index].get_right(),
                nodes[index + 1].get_left(),
                buff=0.08,
                color=color or theme.muted,
                stroke_width=2.2,
                max_tip_length_to_length_ratio=0.22,
            )
            for index in range(max(0, len(nodes) - 1))
        ]
    )


# place a themed title just above a body mobject and no wider than it
def title_above(label: object, body: Mobject, theme: VisualTheme) -> Mobject:
    title = theme_text(label, theme, role="body", font_size=26, max_width=body.width)
    title.next_to(body, UP, buff=0.28)
    return title


# shrink a component so it fits inside the frame with a standard margin
def frame_safe(component: Mobject) -> Mobject:
    return fit_to_frame(component, margin=0.45)


# validate that an index falls inside zero to size minus one
def ensure_index(index: int, size: int, *, name: str = "index") -> int:
    if not 0 <= int(index) < size:
        raise IndexError(f"{name} {index} is outside 0..{size - 1}")
    return int(index)


# validate a non negative amount and optionally that it does not exceed what is available
def ensure_amount(amount: float, *, available: float | None = None) -> float:
    value = float(amount)
    if value < 0:
        raise ValueError("amount cannot be negative")
    if available is not None and value > available + 1e-9:
        raise ValueError(f"amount {value} exceeds available value {available}")
    return value


# build two stacked rows of boxes with vertical arrows linking matching columns
def two_row_flow(
    top_labels: Iterable[object],
    bottom_labels: Iterable[object],
    theme: VisualTheme,
) -> tuple[VGroup, VGroup, VGroup]:
    top = row_of_boxes(top_labels, theme, fallback=[""], color=theme.primary)
    bottom = row_of_boxes(bottom_labels, theme, fallback=[""], color=theme.secondary)
    # stretch the narrower row so both rows share the same width before stacking them
    width = max(top.width, bottom.width)
    if top.width:
        top.scale_to_fit_width(width)
    if bottom.width:
        bottom.scale_to_fit_width(width)
    VGroup(top, bottom).arrange(DOWN, buff=1.0)
    count = min(len(top), len(bottom))
    links = VGroup(
        *[
            Arrow(
                top[index].get_bottom(),
                bottom[index].get_top(),
                buff=0.08,
                color=theme.accent,
                stroke_width=2,
            )
            for index in range(count)
        ]
    )
    return top, bottom, links


__all__ = [
    "arrow_between",
    "arrows_for_row",
    "circle_node",
    "ensure_amount",
    "ensure_index",
    "frame_safe",
    "label_for",
    "labeled_box",
    "normalized_labels",
    "require_theme",
    "row_of_boxes",
    "title_above",
    "two_row_flow",
]
