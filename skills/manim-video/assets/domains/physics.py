"""Semantic components for mechanics, waves, and circuits."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np
from manim import (
    AnimationGroup,
    Arrow,
    Axes,
    Circle,
    Dot,
    Line,
    ORIGIN,
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

from ._common import ensure_index, frame_safe, label_for, normalized_labels, title_above


# labeled bodies joined by lines that can move and receive force arrows
class BodySystem(SemanticMobject):
    # place the bodies at given or evenly spaced positions and connect them
    def __init__(
        self,
        theme: VisualTheme,
        bodies: Iterable[object] | None = None,
        *,
        positions: Sequence[Sequence[float]] | None = None,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        self.body_names = normalized_labels(bodies, fallback=["m1", "m2"])
        # spread the bodies evenly along the x axis centered on the origin
        if positions is None:
            positions = [
                ((index - (len(self.body_names) - 1) / 2) * 2.2, 0, 0)
                for index in range(len(self.body_names))
            ]
        if len(positions) != len(self.body_names):
            raise ValueError("body positions must match the body count")
        body_group = VGroup()
        for index, (name, position) in enumerate(zip(self.body_names, positions)):
            body = Circle(
                radius=0.48,
                color=theme.primary if index % 2 == 0 else theme.secondary,
                fill_color=theme.primary if index % 2 == 0 else theme.secondary,
                fill_opacity=0.18,
            ).move_to(position)
            body_group.add(VGroup(body, label_for(name, theme, size=20, width=0.7).move_to(body)))
        connections = VGroup(
            *[
                Line(body_group[index].get_center(), body_group[index + 1].get_center(), color=theme.muted)
                for index in range(max(0, len(body_group) - 1))
            ]
        )
        force_group = VGroup()
        caption = title_above(label, VGroup(connections, body_group), theme)
        self.register_part("connections", connections)
        self.register_part("bodies", body_group)
        self.register_part("forces", force_group)
        self.register_part("label", caption)
        for index, body in enumerate(body_group):
            self.register_anchor(f"body_{index}", body)
        frame_safe(self)

    # return an animation that moves one body to a position
    def move_body(self, index: int, position: Sequence[float]) -> Any:
        selected = ensure_index(index, len(self.body_names), name="body")
        return self.part("bodies")[selected].animate.move_to(position)

    # add a force arrow from one body and return it
    def apply_force(self, index: int, vector: Sequence[float]) -> Arrow:
        selected = ensure_index(index, len(self.body_names), name="body")
        raw = np.asarray(vector, dtype=float)
        if raw.shape == (2,):
            raw = np.append(raw, 0.0)
        if raw.shape != (3,) or np.linalg.norm(raw) == 0:
            raise ValueError("force vector must be a non-zero 2D or 3D vector")
        body = self.part("bodies")[selected]
        arrow = Arrow(
            body.get_center(),
            body.get_center() + raw,
            buff=0.08,
            color=self.theme.warning,
            stroke_width=3,
        )
        self.part("forces").add(arrow)
        return arrow


# a single force arrow from the origin with a label at its tip
class ForceVector(SemanticMobject):
    # validate the vector and build the origin dot arrow and label
    def __init__(
        self,
        theme: VisualTheme,
        vector: Sequence[float] = (2, 1),
        *,
        label: object = "F",
    ) -> None:
        super().__init__(theme)
        self.vector = self._vector(vector)
        origin = Dot(ORIGIN, color=theme.text, radius=0.07)
        arrow = Arrow(ORIGIN, self.vector, buff=0, color=theme.warning, stroke_width=4)
        caption = label_for(label, theme, size=24, color=theme.warning, width=1.4)
        caption.next_to(arrow.get_end(), UP, buff=0.12)
        self.register_part("origin", origin)
        self.register_part("vector", arrow)
        self.register_part("label", caption)
        self.register_anchor("tail", origin)
        self.register_anchor("tip", lambda: arrow.get_end())
        frame_safe(self)

    # coerce a two or three element vector to three d and reject zero vectors
    @staticmethod
    def _vector(vector: Sequence[float]) -> np.ndarray:
        raw = np.asarray(vector, dtype=float)
        if raw.shape == (2,):
            raw = np.append(raw, 0.0)
        if raw.shape != (3,) or np.linalg.norm(raw) == 0:
            raise ValueError("force vector must be a non-zero 2D or 3D vector")
        return raw

    # replace the vector and return a transform into the new arrow
    def set_vector(self, vector: Sequence[float]) -> Transform:
        self.vector = self._vector(vector)
        return Transform(
            self.part("vector"),
            Arrow(ORIGIN, self.vector, buff=0, color=self.theme.warning, stroke_width=4),
        )


# a sine wave on axes with a source dot that propagates by shifting phase
class WaveField(SemanticMobject):
    # validate amplitude and wavelength then build the axes wave and source
    def __init__(
        self,
        theme: VisualTheme,
        *,
        amplitude: float = 1.0,
        wavelength: float = 2.5,
        phase: float = 0.0,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        if amplitude <= 0 or wavelength <= 0:
            raise ValueError("wave amplitude and wavelength must be positive")
        self.amplitude = float(amplitude)
        self.wavelength = float(wavelength)
        self.phase = float(phase)
        axes = Axes(
            x_range=[-5, 5, 1],
            y_range=[-2, 2, 1],
            x_length=9,
            y_length=4,
            tips=False,
            axis_config={"color": theme.muted, "stroke_opacity": 0.35},
        )
        wave = self._wave(axes)
        source = Dot(axes.c2p(-5, self._value(-5)), color=theme.accent, radius=0.09)
        caption = title_above(label, axes, theme)
        self.register_part("axes", axes)
        self.register_part("wave", wave)
        self.register_part("source", source)
        self.register_part("label", caption)
        self.register_anchor("source", source)
        self.register_anchor("crest", lambda: axes.c2p(self.wavelength / 4 + self.phase, self.amplitude))
        frame_safe(self)

    # evaluate the sine wave at x using the current amplitude wavelength and phase
    def _value(self, x: float) -> float:
        return self.amplitude * np.sin(2 * np.pi * (x - self.phase) / self.wavelength)

    # plot the current wave on the axes
    def _wave(self, axes: Axes) -> Any:
        return axes.plot(lambda x: self._value(x), x_range=(-5, 5), color=self.theme.primary)

    # advance the phase and return the animation that redraws the wave and source
    def propagate(self, distance: float) -> AnimationGroup:
        self.phase += float(distance)
        axes = self.part("axes")
        new_wave = self._wave(axes)
        new_source = Dot(axes.c2p(-5, self._value(-5)), color=self.theme.accent, radius=0.09)
        return AnimationGroup(
            Transform(self.part("wave"), new_wave),
            Transform(self.part("source"), new_source),
            lag_ratio=0,
        )


# a rectangular wire loop with components on it and charges that flow around the top
class CircuitFlow(SemanticMobject):
    # build the wire loop place the components and spread the charges along the top wire
    def __init__(
        self,
        theme: VisualTheme,
        components: Iterable[object] | None = None,
        *,
        charge_count: int = 6,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        self.components = normalized_labels(components, fallback=["source", "load"])
        if charge_count < 1:
            raise ValueError("circuit needs at least one visible charge")
        left_x, right_x, top_y, bottom_y = -3.2, 3.2, 1.5, -1.5
        wire = VGroup(
            Line([left_x, top_y, 0], [right_x, top_y, 0], color=theme.muted),
            Line([right_x, top_y, 0], [right_x, bottom_y, 0], color=theme.muted),
            Line([right_x, bottom_y, 0], [left_x, bottom_y, 0], color=theme.muted),
            Line([left_x, bottom_y, 0], [left_x, top_y, 0], color=theme.muted),
        )
        component_group = VGroup()
        # components sit on the left right top and bottom wires in that order
        positions = [[left_x, 0, 0], [right_x, 0, 0], [0, top_y, 0], [0, bottom_y, 0]]
        for index, name in enumerate(self.components):
            box = Rectangle(
                width=1.25,
                height=0.58,
                color=theme.primary if index % 2 == 0 else theme.secondary,
                fill_color=theme.background,
                fill_opacity=1,
            ).move_to(positions[index % len(positions)])
            component_group.add(VGroup(box, label_for(name, theme, size=18, width=1.05).move_to(box)))
        # charges start evenly spaced along the top wire
        path_points = [
            [-3.2 + 6.4 * index / charge_count, top_y, 0]
            for index in range(charge_count)
        ]
        charges = VGroup(*[Dot(point, color=theme.accent, radius=0.07) for point in path_points])
        caption = title_above(label, VGroup(wire, component_group), theme)
        self.current = 0.0
        self.register_part("wire", wire)
        self.register_part("components", component_group)
        self.register_part("charges", charges)
        self.register_part("label", caption)
        frame_safe(self)

    # set the current and return an animation that colors charges by direction and strength
    def set_current(self, current: float) -> AnimationGroup:
        self.current = float(current)
        strength = min(1.0, abs(self.current))
        color = self.theme.accent if self.current >= 0 else self.theme.warning
        return AnimationGroup(
            *(charge.animate.set_color(color).set_opacity(0.25 + 0.75 * strength) for charge in self.part("charges")),
            lag_ratio=0.03,
        )

    # return a staggered animation that shifts the charges in the current direction
    def flow(self, distance: float = 0.45) -> AnimationGroup:
        direction = 1 if self.current >= 0 else -1
        return AnimationGroup(
            *(charge.animate.shift(RIGHT * distance * direction) for charge in self.part("charges")),
            lag_ratio=0.04,
        )


__all__ = ["BodySystem", "CircuitFlow", "ForceVector", "WaveField"]
