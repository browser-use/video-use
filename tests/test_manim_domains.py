"""tests that every domain component is semantic frame safe and theme aware
it also checks a handful of component actions update their state
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


manim = pytest.importorskip("manim")

ASSETS = Path(__file__).parents[1] / "skills" / "manim-video" / "assets"
sys.path.insert(0, str(ASSETS))

import domains  # noqa: E402
from teaching import SemanticMobject, VisualTheme  # noqa: E402


COMPONENTS = [getattr(domains, name) for name in domains.__all__]
LABELS = ["", "short", "A label long enough to exercise component width fitting and frame safety"]


# build every component with each label length and check it stays inside the frame
@pytest.mark.parametrize("component", COMPONENTS, ids=lambda component: component.__name__)
@pytest.mark.parametrize("label", LABELS, ids=["empty", "short", "long"])
def test_domain_components_are_semantic_and_frame_safe(component: type, label: str) -> None:
    model = component(VisualTheme(), label=label)

    assert isinstance(model, SemanticMobject)
    assert model.part_names
    assert model.width <= float(manim.config.frame_width) - 0.9 + 1e-6
    assert model.height <= float(manim.config.frame_height) - 0.9 + 1e-6


# every component must reject a missing theme
def test_domain_components_require_a_shared_theme() -> None:
    for component in COMPONENTS:
        with pytest.raises(TypeError, match="VisualTheme"):
            component(None)


# call one action on several components and check the tracked state changed
def test_domain_actions_update_semantic_state() -> None:
    theme = VisualTheme()

    mass = domains.ProbabilityMass(theme)
    mass.transfer_probability(1, 0, 0.1)
    assert mass.probabilities == pytest.approx([0.35, 0.4, 0.25])

    graph = domains.GraphModel(theme)
    graph.visit_node("A")
    assert "A" in graph.visited

    queue = domains.QueueModel(theme, items=[])
    queue.enqueue("request")
    request, _animation = queue.dequeue()
    assert request == "request"

    wave = domains.WaveField(theme)
    wave.propagate(0.5)
    assert wave.phase == pytest.approx(0.5)

    sequence = domains.SequenceProcess(theme, "ATG")
    sequence.transcribe()
    assert sequence.rna == "UAC"

    timeline = domains.CompoundTimeline(theme, principal=100, rate=0.1, periods=3)
    timeline.compound(2)
    assert timeline.balance_at(2) == pytest.approx(121)

