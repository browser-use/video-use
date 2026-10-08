"""test manim concept asset support for video use"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


manim = pytest.importorskip("manim")


ASSET_PATH = (
    Path(__file__).parents[1]
    / "skills"
    / "manim-video"
    / "assets"
    / "concept_explainer.py"
)
SPEC = importlib.util.spec_from_file_location("concept_explainer_asset", ASSET_PATH)
assert SPEC is not None and SPEC.loader is not None
concept_explainer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(concept_explainer)


# measured evidence retains out of frame geometry for the canonical QC validator
def test_measured_layout_retains_clipping():
    object = manim.Rectangle(width=2, height=1).shift(manim.RIGHT * float(manim.config.frame_width) / 2)
    frame = concept_explainer.measured_layout_frame(1, {'object': object})
    assert frame['elements'][0]['rect']['x'] + frame['elements'][0]['rect']['width'] > 1920


# camera pan and zoom affect measured screen coordinates while overlaps stay visible
def test_measured_layout_camera_and_overlap():
    camera = manim.Rectangle(width=8, height=4).shift(manim.RIGHT * 3)
    object = manim.Rectangle(width=2, height=1).move_to(camera)
    frame = concept_explainer.measured_layout_frame(0, {'first': object, 'second': object.copy()},
        width=800, height=400, camera_frame=camera)
    assert frame['elements'][0]['rect'] == pytest.approx({'x': 300, 'y': 150, 'width': 200, 'height': 100})
    assert frame['elements'][0]['rect'] == frame['elements'][1]['rect']


# malformed measurement inputs fail before exporting misleading evidence
@pytest.mark.parametrize('time,objects,kwargs', [(-1, {}, {}), (float('nan'), {}, {}),
    (0, {}, {}), (0, {'': None}, {}), (0, {'name': None}, {'width': float('inf')})])
def test_measured_layout_invalid(time, objects, kwargs):
    with pytest.raises(ValueError):
        concept_explainer.measured_layout_frame(time, objects, **kwargs)
