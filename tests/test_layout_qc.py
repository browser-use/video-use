"""tests for the layout qc helper covering overlaps touching edges declared overlaps and canvas bounds
each test builds small element dicts in delivery canvas pixels
"""

import pytest

from helpers.layout_qc import LayoutQCError, validate_frame, validate_manifest


# build a minimal element dict with an id and a rect
def _element(element_id: str, x: int, y: int, width: int, height: int) -> dict:
    return {
        "id": element_id,
        "rect": {"x": x, "y": y, "width": width, "height": height},
    }


# two overlapping elements fail and the error names both ids and the frame time
def test_frame_rejects_unintended_component_overlap() -> None:
    elements = [
        _element("timer_text", 424, 991, 232, 66),
        _element("selection_dot", 512, 996, 56, 56),
    ]

    with pytest.raises(LayoutQCError, match="timer_text.*selection_dot.*13.000s"):
        validate_frame(elements, width=1080, height=1920, time=13.0)


# elements that share an edge without positive area are fine
def test_touching_edges_are_not_an_overlap() -> None:
    validate_frame(
        [
            _element("button", 100, 100, 300, 80),
            _element("supporting_copy", 100, 180, 300, 40),
        ],
        width=1080,
        height=1920,
    )


# an overlap passes when one element lists the other in allow_overlap_with
def test_intentional_overlap_must_be_declared() -> None:
    first = _element("badge", 100, 100, 80, 80)
    first["allow_overlap_with"] = ["icon"]
    validate_frame(
        [first, _element("icon", 120, 120, 30, 30)],
        width=1080,
        height=1920,
    )


# a manifest with two frames returns frame and element counts
def test_manifest_checks_bounds_at_each_critical_frame() -> None:
    payload = {
        "canvas": {"width": 1080, "height": 1920},
        "frames": [
            {
                "time": 31.2,
                "elements": [_element("cta", 176, 1030, 728, 138)],
            },
            {
                "time": 31.5,
                "elements": [_element("tagline", 300, 1200, 480, 40)],
            },
        ],
    }

    assert validate_manifest(payload) == (2, 2)


# an element that extends past the canvas width is reported
def test_frame_rejects_elements_outside_delivery_canvas() -> None:
    with pytest.raises(LayoutQCError, match="leaves the canvas"):
        validate_frame(
            [_element("offscreen_copy", 950, 100, 200, 40)],
            width=1080,
            height=1920,
        )
