"""Semantic components for mathematical explanations."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

import numpy as np
from manim import (
    AnimationGroup,
    Arrow,
    Axes,
    DashedLine,
    Dot,
    DOWN,
    LEFT,
    NumberLine,
    Rectangle,
    RIGHT,
    Transform,
    UP,
    VGroup,
)

try:
    from ..teaching import SemanticMobject, VisualTheme
except ImportError:
    from teaching import SemanticMobject, VisualTheme

from ._common import ensure_amount, ensure_index, frame_safe, label_for, title_above


# a number line with a marker dot that moves between values
class NumberLineModel(SemanticMobject):
    # validate the range and build the line and marker
    def __init__(
        self,
        theme: VisualTheme,
        *,
        x_range: Sequence[float] = (-5, 5, 1),
        value: float = 0.0,
        label: object = "",
        length: float = 8.4,
    ) -> None:
        super().__init__(theme)
        if len(x_range) not in {2, 3} or float(x_range[1]) <= float(x_range[0]):
            raise ValueError("x_range must have increasing start and end values")
        line = NumberLine(x_range=list(x_range), length=length, color=theme.muted)
        self.minimum = float(x_range[0])
        self.maximum = float(x_range[1])
        self.value = float(value)
        if not self.minimum <= self.value <= self.maximum:
            raise ValueError("initial value is outside the number line")
        marker = Dot(line.n2p(self.value), color=theme.accent, radius=0.10)
        caption = title_above(label, line, theme)
        self.register_part("line", line)
        self.register_part("marker", marker)
        self.register_part("label", caption)
        self.register_anchor("minimum", lambda: line.n2p(self.minimum))
        self.register_anchor("maximum", lambda: line.n2p(self.maximum))
        self.register_anchor("value", marker)
        frame_safe(self)

    # validate the target and return an animation that slides the marker to it
    def move_value(self, value: float) -> Any:
        target = float(value)
        if not self.minimum <= target <= self.maximum:
            raise ValueError("target value is outside the number line")
        self.value = target
        return self.part("marker").animate.move_to(self.part("line").n2p(target))


# axes with arrows for a set of vectors that a matrix can transform
class VectorMap(SemanticMobject):
    # build the axes and one arrow per vector from the origin
    def __init__(
        self,
        theme: VisualTheme,
        vectors: Iterable[Sequence[float]] | None = None,
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        raw_vectors = list(vectors) if vectors is not None else [(1.5, 0.5), (-0.5, 1.4)]
        self.vectors = [np.asarray((*vector[:2], 0.0), dtype=float) for vector in raw_vectors]
        axes = Axes(
            x_range=[-3, 3, 1],
            y_range=[-2.3, 2.3, 1],
            x_length=7,
            y_length=4.6,
            axis_config={"color": theme.muted, "stroke_opacity": 0.5},
            tips=False,
        )
        arrows = VGroup(
            *[
                Arrow(
                    axes.c2p(0, 0),
                    axes.c2p(vector[0], vector[1]),
                    buff=0,
                    color=theme.primary if index % 2 == 0 else theme.secondary,
                    stroke_width=3,
                )
                for index, vector in enumerate(self.vectors)
            ]
        )
        caption = title_above(label, axes, theme)
        self.register_part("axes", axes)
        self.register_part("vectors", arrows)
        self.register_part("label", caption)
        self.register_anchor("origin", lambda: axes.c2p(0, 0))
        frame_safe(self)

    # multiply every vector by a two by two matrix and return the animation that redraws the arrows
    def apply_matrix(self, matrix: Sequence[Sequence[float]]) -> AnimationGroup:
        transform = np.asarray(matrix, dtype=float)
        if transform.shape != (2, 2):
            raise ValueError("vector map transformations require a 2 by 2 matrix")
        axes = self.part("axes")
        new_vectors = [transform @ vector[:2] for vector in self.vectors]
        animations = []
        for index, (arrow, vector) in enumerate(zip(self.part("vectors"), new_vectors)):
            target = Arrow(
                axes.c2p(0, 0),
                axes.c2p(vector[0], vector[1]),
                buff=0,
                color=theme_color(self.theme, index),
                stroke_width=3,
            )
            animations.append(Transform(arrow, target))
        self.vectors = [np.array([vector[0], vector[1], 0.0]) for vector in new_vectors]
        return AnimationGroup(*animations, lag_ratio=0)


# alternate between primary and secondary colors by index
def theme_color(theme: VisualTheme, index: int) -> str:
    return theme.primary if index % 2 == 0 else theme.secondary


# a two by two matrix grid beside axes showing an input arrow and its transformed output
class MatrixMap(SemanticMobject):
    # validate the matrix and build the grid the axes and the input and output arrows
    def __init__(
        self,
        theme: VisualTheme,
        matrix: Sequence[Sequence[float]] = ((1, 0), (0, 1)),
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        values = np.asarray(matrix, dtype=float)
        if values.shape != (2, 2):
            raise ValueError("MatrixMap currently visualizes 2 by 2 transformations")
        self.matrix = values
        cells = VGroup(
            *[
                VGroup(
                    Rectangle(
                        width=0.82,
                        height=0.68,
                        color=theme.primary,
                        fill_color=theme.primary,
                        fill_opacity=0.10,
                    ),
                    label_for(f"{value:g}", theme, size=20, width=0.64),
                )
                for value in values.flat
            ]
        ).arrange_in_grid(rows=2, cols=2, buff=0.08)
        axes = Axes(
            x_range=[-2.5, 2.5, 1],
            y_range=[-2, 2, 1],
            x_length=4.5,
            y_length=3.6,
            tips=False,
            axis_config={"color": theme.muted, "stroke_opacity": 0.45},
        )
        cells.next_to(axes, LEFT, buff=0.65)
        input_arrow = Arrow(axes.c2p(0, 0), axes.c2p(1, 0.75), buff=0, color=theme.secondary)
        output_arrow = Arrow(axes.c2p(0, 0), axes.c2p(1, 0.75), buff=0, color=theme.accent)
        caption = title_above(label, VGroup(cells, axes), theme)
        self.register_part("matrix", cells)
        self.register_part("axes", axes)
        self.register_part("input", input_arrow)
        self.register_part("output", output_arrow)
        self.register_part("label", caption)
        self.register_anchor("input", input_arrow)
        self.register_anchor("output", output_arrow)
        frame_safe(self)

    # multiply a vector by the matrix and return the animation that moves both arrows
    def apply_to(self, vector: Sequence[float]) -> AnimationGroup:
        raw = np.asarray(vector, dtype=float)
        if raw.shape != (2,):
            raise ValueError("matrix input must contain exactly two values")
        result = self.matrix @ raw
        axes = self.part("axes")
        input_target = Arrow(axes.c2p(0, 0), axes.c2p(*raw), buff=0, color=self.theme.secondary)
        output_target = Arrow(axes.c2p(0, 0), axes.c2p(*result), buff=0, color=self.theme.accent)
        self.input_vector = raw
        self.output_vector = result
        return AnimationGroup(
            Transform(self.part("input"), input_target),
            Transform(self.part("output"), output_target),
            lag_ratio=0.15,
        )


# a plotted function with a point and a dashed guide that track one x value
class LinkedPlot(SemanticMobject):
    # validate the range and build the axes graph point and guide
    def __init__(
        self,
        theme: VisualTheme,
        function: Callable[[float], float] = lambda x: x,
        *,
        x_range: Sequence[float] = (-3, 3),
        y_range: Sequence[float] = (-2, 2),
        x_value: float = 0.0,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        self.function = function
        self.x_range = (float(x_range[0]), float(x_range[1]))
        if self.x_range[1] <= self.x_range[0]:
            raise ValueError("plot x range must increase")
        axes = Axes(
            x_range=[*self.x_range, 1],
            y_range=[float(y_range[0]), float(y_range[1]), 1],
            x_length=7,
            y_length=4,
            tips=False,
            axis_config={"color": theme.muted, "stroke_opacity": 0.5},
        )
        graph = axes.plot(function, x_range=self.x_range, color=theme.primary)
        self.x_value = float(x_value)
        point = Dot(axes.c2p(self.x_value, function(self.x_value)), color=theme.accent)
        guide = DashedLine(
            axes.c2p(self.x_value, 0),
            point.get_center(),
            color=theme.secondary,
        )
        caption = title_above(label, axes, theme)
        self.register_part("axes", axes)
        self.register_part("graph", graph)
        self.register_part("point", point)
        self.register_part("guide", guide)
        self.register_part("label", caption)
        self.register_anchor("point", point)
        frame_safe(self)

    # validate the x value and return the animation that moves the point and guide
    def set_x(self, x_value: float) -> AnimationGroup:
        value = float(x_value)
        if not self.x_range[0] <= value <= self.x_range[1]:
            raise ValueError("x value is outside the plot range")
        axes = self.part("axes")
        point = Dot(axes.c2p(value, self.function(value)), color=self.theme.accent)
        guide = DashedLine(axes.c2p(value, 0), point.get_center(), color=self.theme.secondary)
        self.x_value = value
        return AnimationGroup(
            Transform(self.part("point"), point),
            Transform(self.part("guide"), guide),
            lag_ratio=0,
        )


# bars showing a probability distribution that can shift mass between outcomes
class ProbabilityMass(SemanticMobject):
    # validate probabilities and labels and build the bars
    def __init__(
        self,
        theme: VisualTheme,
        probabilities: Sequence[float] = (0.25, 0.5, 0.25),
        *,
        labels: Iterable[object] | None = None,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        self.probabilities = [float(value) for value in probabilities]
        if not self.probabilities or any(value < 0 for value in self.probabilities):
            raise ValueError("probabilities must be a non-empty list of non-negative values")
        total = sum(self.probabilities)
        if not np.isclose(total, 1.0, atol=1e-8):
            raise ValueError(f"probabilities must sum to one, got {total:g}")
        self.labels = [str(value) for value in labels] if labels is not None else [str(i) for i in range(len(self.probabilities))]
        if len(self.labels) != len(self.probabilities):
            raise ValueError("probability labels must match the number of values")
        bars = self._bars()
        caption = title_above(label, bars, theme)
        self.register_part("bars", bars)
        self.register_part("label", caption)
        for index, bar in enumerate(bars):
            self.register_anchor(f"mass_{index}", bar)
        frame_safe(self)

    # build one bar per outcome with a percent label above and a name label below
    def _bars(self) -> VGroup:
        bars = VGroup()
        for index, (name, value) in enumerate(zip(self.labels, self.probabilities)):
            # bar height scales with probability and keeps a tiny floor so zero bars stay visible
            height = max(0.02, value * 3.4)
            bar = Rectangle(
                width=0.86,
                height=height,
                color=theme_color(self.theme, index),
                fill_color=theme_color(self.theme, index),
                fill_opacity=0.55,
            )
            value_label = label_for(f"{value:.0%}", self.theme, size=19, width=0.8)
            name_label = label_for(name, self.theme, size=18, width=0.86)
            value_label.next_to(bar, UP, buff=0.1)
            name_label.next_to(bar, DOWN, buff=0.12)
            bars.add(VGroup(bar, value_label, name_label))
        bars.arrange(RIGHT, buff=0.32, aligned_edge=DOWN)
        return bars

    # move probability mass between outcomes and return the transform that redraws the bars
    def transfer_probability(self, source: int, target: int, amount: float) -> Transform:
        source_index = ensure_index(source, len(self.probabilities), name="source")
        target_index = ensure_index(target, len(self.probabilities), name="target")
        if source_index == target_index:
            raise ValueError("probability source and target must differ")
        value = ensure_amount(amount, available=self.probabilities[source_index])
        self.probabilities[source_index] -= value
        self.probabilities[target_index] += value
        return Transform(self.part("bars"), self._bars())


__all__ = [
    "LinkedPlot",
    "MatrixMap",
    "NumberLineModel",
    "ProbabilityMass",
    "VectorMap",
]
