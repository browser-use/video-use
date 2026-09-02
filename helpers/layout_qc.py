#!/usr/bin/env python3
"""Fail code-generated video layouts on unintended component collisions.

Animation code can call :func:`validate_frame` for every rendered frame, or
write a compact JSON manifest containing critical frames and validate it with:

    python helpers/layout_qc.py edit/layout_manifest.json

Only collision-sensitive foreground elements belong in the manifest. Declare
intentional intersections with ``allow_overlap_with`` on either element.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


# error type raised for any layout problem so callers can catch one thing
class LayoutQCError(ValueError):
    """Raised when a generated scene contains an invalid layout."""


# axis aligned rectangle in canvas pixels with helpers for its far edges
@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    # x coordinate of the right edge
    @property
    def right(self) -> float:
        return self.x + self.width

    # y coordinate of the bottom edge
    @property
    def bottom(self) -> float:
        return self.y + self.height

    # true only when the two rects share positive area so touching edges do not count
    def intersects(self, other: "Rect") -> bool:
        return (
            self.x < other.right
            and self.right > other.x
            and self.y < other.bottom
            and self.bottom > other.y
        )


# coerce a value to a finite float or raise a labeled qc error
def _number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LayoutQCError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise LayoutQCError(f"{label} must be finite")
    return number


# build a rect from a dict and reject missing keys or non positive sizes
def _rect(value: Any, label: str) -> Rect:
    if not isinstance(value, dict):
        raise LayoutQCError(f"{label} must be an object")
    try:
        rect = Rect(
            x=_number(value["x"], f"{label}.x"),
            y=_number(value["y"], f"{label}.y"),
            width=_number(value["width"], f"{label}.width"),
            height=_number(value["height"], f"{label}.height"),
        )
    except KeyError as exc:
        raise LayoutQCError(f"{label} requires x, y, width, and height") from exc
    if rect.width <= 0 or rect.height <= 0:
        raise LayoutQCError(f"{label} width and height must be positive")
    return rect


# read the allow_overlap_with list of an element as a set of ids
def _allowed_ids(element: dict[str, Any], label: str) -> set[str]:
    raw = element.get("allow_overlap_with", [])
    if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
        raise LayoutQCError(f"{label}.allow_overlap_with must be a list of element ids")
    return set(raw)


# validate one frame of rectangles against the canvas and against each other
def validate_frame(
    elements: Iterable[dict[str, Any]],
    *,
    width: float,
    height: float,
    time: float | None = None,
) -> None:
    """Validate one frame of measured foreground rectangles.

    Rectangles use delivery-canvas pixels. Edges may touch, but positive-area
    intersections require an explicit ``allow_overlap_with`` declaration.
    """

    canvas_width = _number(width, "canvas.width")
    canvas_height = _number(height, "canvas.height")
    if canvas_width <= 0 or canvas_height <= 0:
        raise LayoutQCError("canvas width and height must be positive")
    suffix = f" at {time:.3f}s" if time is not None else ""
    measured: list[tuple[str, Rect, set[str]]] = []
    seen: set[str] = set()
    problems: list[str] = []

    # first pass parses every element and collects shape problems without stopping early
    for index, raw in enumerate(elements):
        label = f"element {index}"
        if not isinstance(raw, dict):
            problems.append(f"{label} must be an object{suffix}")
            continue
        element_id = str(raw.get("id") or "").strip()
        if not element_id:
            problems.append(f"{label} requires an id{suffix}")
            continue
        if element_id in seen:
            problems.append(f"duplicate element id '{element_id}'{suffix}")
            continue
        seen.add(element_id)
        try:
            rect = _rect(raw.get("rect"), f"element '{element_id}'.rect")
            allowed = _allowed_ids(raw, f"element '{element_id}'")
        except LayoutQCError as exc:
            problems.append(f"{exc}{suffix}")
            continue
        if rect.x < 0 or rect.y < 0 or rect.right > canvas_width or rect.bottom > canvas_height:
            problems.append(f"element '{element_id}' leaves the canvas{suffix}")
        measured.append((element_id, rect, allowed))

    # second pass compares every unordered pair and skips pairs declared as intentional overlaps
    for index, (first_id, first_rect, first_allowed) in enumerate(measured):
        for second_id, second_rect, second_allowed in measured[index + 1 :]:
            if second_id in first_allowed or first_id in second_allowed:
                continue
            if first_rect.intersects(second_rect):
                problems.append(
                    f"elements '{first_id}' and '{second_id}' overlap{suffix}"
                )

    if problems:
        formatted = "\n".join(f"- {problem}" for problem in problems)
        raise LayoutQCError(f"layout QC failed:\n{formatted}")


# validate a whole manifest by running validate_frame on each frame and return counts
def validate_manifest(payload: Any) -> tuple[int, int]:
    """Validate a JSON layout manifest and return frame/element counts."""

    if not isinstance(payload, dict):
        raise LayoutQCError("layout manifest must be an object")
    canvas = payload.get("canvas")
    if not isinstance(canvas, dict):
        raise LayoutQCError("layout manifest requires a canvas object")
    width = _number(canvas.get("width"), "canvas.width")
    height = _number(canvas.get("height"), "canvas.height")
    frames = payload.get("frames")
    if not isinstance(frames, list) or not frames:
        raise LayoutQCError("layout manifest requires at least one frame")

    element_count = 0
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            raise LayoutQCError(f"frame {index} must be an object")
        time = _number(frame.get("time"), f"frame {index}.time")
        elements = frame.get("elements")
        if not isinstance(elements, list):
            raise LayoutQCError(f"frame {index}.elements must be a list")
        validate_frame(elements, width=width, height=height, time=time)
        element_count += len(elements)
    return len(frames), element_count


# command line entry that loads a manifest file and prints a pass summary
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    frames, elements = validate_manifest(payload)
    print(f"layout QC passed: {frames} frame(s), {elements} measured element(s)")


if __name__ == "__main__":
    main()
