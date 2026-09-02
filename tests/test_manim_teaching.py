"""tests for the semantic teaching primitives in teaching py
it covers part and anchor registration focus and restore transforms linked values and the three d scene
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest


manim = pytest.importorskip("manim")

ASSETS = Path(__file__).parents[1] / "skills" / "manim-video" / "assets"
sys.path.insert(0, str(ASSETS))

from teaching import (  # noqa: E402
    LinkedValue,
    SemanticMobject,
    TeachingScene,
    TeachingThreeDScene,
    VisualTheme,
)


# a default theme for each test
@pytest.fixture
def theme() -> VisualTheme:
    return VisualTheme()


# parts and anchors register and resolve under their names
def test_semantic_part_and_anchor_registration(theme: VisualTheme) -> None:
    model = SemanticMobject(theme)
    dot = manim.Dot([1, 2, 0])

    assert model.register_part("value", dot) is dot
    model.register_anchor("value_center", dot)

    assert model.part("value") is dot
    np.testing.assert_allclose(model.anchor("value_center"), [1, 2, 0])
    assert model.part_names == ("value",)
    assert model.anchor_names == ("value_center",)


# duplicate names and unknown lookups raise
def test_semantic_names_reject_duplicates_and_unknowns(theme: VisualTheme) -> None:
    model = SemanticMobject(theme)
    model.register_part("value", manim.Dot())
    model.register_anchor("center", [0, 0])

    with pytest.raises(ValueError, match="already registered"):
        model.register_part("value", manim.Dot())
    with pytest.raises(ValueError, match="already registered"):
        model.register_anchor("center", [1, 0, 0])
    with pytest.raises(KeyError, match="unknown part"):
        model.part("missing")
    with pytest.raises(KeyError, match="unknown anchor"):
        model.anchor("missing")


# focus dims unrelated objects and restore brings back the exact opacities
def test_focus_dims_unrelated_objects_and_restores_exact_opacity(theme: VisualTheme) -> None:
    scene = TeachingScene()
    scene.theme = theme
    focus = manim.Dot(fill_opacity=0.83, stroke_opacity=0.61)
    context = manim.Circle(fill_opacity=0.27, stroke_opacity=0.74).shift(manim.RIGHT * 2)
    scene.remember("focus", focus)
    scene.remember("context", context)
    before_fill = np.asarray(context.get_fill_opacity()).copy()
    before_stroke = np.asarray(context.get_stroke_opacity()).copy()

    scene.focus_on("focus", dim_opacity=0.11, animate=False)

    np.testing.assert_allclose(context.get_fill_opacity(), 0.11)
    np.testing.assert_allclose(context.get_stroke_opacity(), 0.11)
    scene.restore_context(animate=False)
    np.testing.assert_allclose(context.get_fill_opacity(), before_fill)
    np.testing.assert_allclose(context.get_stroke_opacity(), before_stroke)


# transform keeps the same python object and adopts the target parts and anchors
def test_transform_preserves_registry_identity_and_adopts_semantics(theme: VisualTheme) -> None:
    scene = TeachingScene()
    source = SemanticMobject(theme)
    source.register_part("dot", manim.Dot())
    target = SemanticMobject(theme)
    square = manim.Square()
    target.register_part("square", square)
    target.register_anchor("center", square)
    scene.remember("model", source)
    original_id = id(source)

    transformed = scene.transform_object("model", target, animate=False)

    assert id(transformed) == original_id
    assert scene.recall("model") is source
    assert transformed.part("square") in transformed.get_family()
    np.testing.assert_allclose(transformed.anchor("center"), [0, 0, 0])


# every dependent sees each value in order
def test_linked_value_updates_all_representations_at_each_state(theme: VisualTheme) -> None:
    linked = LinkedValue(0)
    dot = manim.Dot()
    bar = manim.Rectangle(width=0.2, height=1)
    seen: list[tuple[str, float]] = []

    linked.register(
        "dot",
        dot,
        lambda mobject, value: (
            seen.append(("dot", value)),
            mobject.move_to([value, 0, 0]),
        )[1],
    )
    linked.register(
        "bar",
        bar,
        lambda mobject, value: (
            seen.append(("bar", value)),
            mobject.stretch_to_fit_width(max(0.1, value + 0.1)),
        )[1],
    )

    for value in (0.0, 0.5, 1.0):
        linked.set_value(value)
        assert dot.get_center()[0] == pytest.approx(value)
        assert {name for name, recorded in seen[-2:] if recorded == value} == {"dot", "bar"}


# suspend blocks updates and clear detaches the updater
def test_linked_value_suspend_resume_and_clear() -> None:
    linked = LinkedValue(0)
    dot = manim.Dot()
    linked.register("dot", dot, lambda mobject, value: mobject.move_to([value, 0, 0]))

    linked.suspend().set_value(2)
    assert dot.get_center()[0] == pytest.approx(0)
    linked.resume()
    assert dot.get_center()[0] == pytest.approx(2)
    linked.clear("dot").set_value(4)
    assert dot.get_center()[0] == pytest.approx(2)


# the three d scene supports the registry and restores the camera after focus
def test_three_d_scene_supports_registry_and_immediate_focus(theme: VisualTheme) -> None:
    scene = TeachingThreeDScene()
    focus = manim.Sphere(radius=0.25)
    context = manim.Cube(side_length=0.5).shift(manim.RIGHT)
    scene.remember("focus", focus)
    scene.remember("context", context)
    original_center = np.asarray(scene.camera.frame_center).copy()

    scene.focus_on("focus", animate=False)
    scene.restore_context(animate=False)

    assert scene.recall("focus") is focus
    np.testing.assert_allclose(scene.camera.frame_center, original_center)
