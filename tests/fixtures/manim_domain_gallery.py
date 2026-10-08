"""gallery of tiny manim scenes used by the rendering test
each scene shows one domain component or exercises the teaching scene api
"""

from __future__ import annotations

import sys
from pathlib import Path

from manim import Circle, Cube, Dot, Scene, Sphere, Square


ASSETS = Path(__file__).parents[2] / "skills" / "manim-video" / "assets"
sys.path.insert(0, str(ASSETS))

from domains import (  # noqa: E402
    ArrayModel,
    BodySystem,
    CashFlow,
    CellProcess,
    NumberLineModel,
    RequestFlow,
)
from teaching import (  # noqa: E402
    SemanticMobject,
    TeachingScene,
    TeachingThreeDScene,
    VisualTheme,
)


THEME = VisualTheme()


# render a number line component
class MathGallery(Scene):
    # add the component and hold briefly
    def construct(self):
        self.camera.background_color = THEME.background
        self.add(NumberLineModel(THEME, label="math"))
        self.wait(0.1)


# render an array component
class ComputingGallery(Scene):
    # add the component and hold briefly
    def construct(self):
        self.camera.background_color = THEME.background
        self.add(ArrayModel(THEME, label="algorithms and AI"))
        self.wait(0.1)


# render a request flow component
class SystemsGallery(Scene):
    # add the component and hold briefly
    def construct(self):
        self.camera.background_color = THEME.background
        self.add(RequestFlow(THEME, label="software systems"))
        self.wait(0.1)


# render a body system component
class PhysicsGallery(Scene):
    # add the component and hold briefly
    def construct(self):
        self.camera.background_color = THEME.background
        self.add(BodySystem(THEME, label="physics"))
        self.wait(0.1)


# render a cell process component
class BiologyGallery(Scene):
    # add the component and hold briefly
    def construct(self):
        self.camera.background_color = THEME.background
        self.add(CellProcess(THEME, label="biology"))
        self.wait(0.1)


# render a cash flow component
class FinanceGallery(Scene):
    # add the component and hold briefly
    def construct(self):
        self.camera.background_color = THEME.background
        self.add(CashFlow(THEME, label="finance and business"))
        self.wait(0.1)


# exercise the teaching scene focus highlight transform and hold helpers
class TeachingAPIGallery(TeachingScene):
    # walk through each teaching helper with short run times
    def construct(self):
        self.theme = THEME
        self.camera.background_color = THEME.background
        focus = self.remember("focus", Dot(color=THEME.primary).shift([-1, 0, 0]))
        self.remember("context", Circle(color=THEME.secondary).shift([1, 0, 0]))
        self.begin_beat("focus")
        self.focus_on(focus, run_time=0.1)
        self.restore_context(run_time=0.1)
        self.highlight("focus", run_time=0.1)
        self.restore_highlight(run_time=0.1)
        source = SemanticMobject(THEME)
        source.register_part("dot", Dot(color=THEME.primary))
        self.remember("semantic", source)
        target = SemanticMobject(THEME)
        target.register_part("square", Square(color=THEME.accent))
        self.transform_object("semantic", target, run_time=0.1)
        self.hold(0.1, purpose="inspect transformed semantic object")


# exercise focus and restore on the three d teaching scene
class TeachingThreeDGallery(TeachingThreeDScene):
    # focus on a sphere and restore the context with short run times
    def construct(self):
        self.camera.background_color = THEME.background
        focus = self.remember("focus", Sphere(radius=0.25).shift([-0.8, 0, 0]))
        self.remember("context", Cube(side_length=0.5).shift([0.8, 0, 0]))
        self.begin_beat("three dimensional focus")
        self.focus_on(focus, run_time=0.1)
        self.restore_context(run_time=0.1)
        self.hold(0.1, purpose="inspect restored three-dimensional context")
