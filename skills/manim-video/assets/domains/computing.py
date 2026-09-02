"""Semantic components for algorithms, machine learning, and AI."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np
from manim import (
    AnimationGroup,
    Arrow,
    CurvedArrow,
    DOWN,
    RIGHT,
    SurroundingRectangle,
    Transform,
    VGroup,
)

try:
    from ..teaching import SemanticMobject, VisualTheme
except ImportError:
    from teaching import SemanticMobject, VisualTheme

from ._common import (
    circle_node,
    ensure_index,
    frame_safe,
    label_for,
    labeled_box,
    normalized_labels,
    title_above,
)


# a row of value cells with index labels that supports highlighting swapping and setting
class ArrayModel(SemanticMobject):
    # build the cells and the index labels under them and register anchors per index
    def __init__(
        self,
        theme: VisualTheme,
        values: Iterable[object] | None = None,
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        self.values = normalized_labels(values, fallback=["4", "1", "3", "2"])
        cells = self._make_cells()
        indices = VGroup(
            *[label_for(index, theme, size=16, color=theme.muted, width=0.7) for index in range(len(self.values))]
        ).arrange(RIGHT, buff=0.1)
        for index, cell in enumerate(cells):
            indices[index].next_to(cell, DOWN, buff=0.1)
        caption = title_above(label, cells, theme)
        self.register_part("cells", cells)
        self.register_part("indices", indices)
        self.register_part("label", caption)
        for index, cell in enumerate(cells):
            self.register_anchor(f"index_{index}", cell)
        frame_safe(self)

    # build one labeled box per value in a tight row
    def _make_cells(self) -> VGroup:
        cells = VGroup(
            *[
                labeled_box(value, self.theme, width=0.82, height=0.78, color=self.theme.primary)
                for value in self.values
            ]
        ).arrange(RIGHT, buff=0.1)
        return cells

    # return an animation that recolors one cell with the accent color
    def highlight_index(self, index: int) -> Any:
        selected = ensure_index(index, len(self.values))
        return self.part("cells")[selected].animate.set_color(self.theme.accent)

    # swap two values and return an animation that slides their cells past each other
    def swap(self, first: int, second: int) -> AnimationGroup:
        left = ensure_index(first, len(self.values), name="first index")
        right = ensure_index(second, len(self.values), name="second index")
        if left == right:
            raise ValueError("swap indices must differ")
        cells = self.part("cells")
        # copy the centers before the swap so both moves use the original positions
        left_position = cells[left].get_center().copy()
        right_position = cells[right].get_center().copy()
        self.values[left], self.values[right] = self.values[right], self.values[left]
        left_cell, right_cell = cells[left], cells[right]
        # reorder the group and the anchors so later index lookups match the new visual order
        cells.submobjects[left], cells.submobjects[right] = right_cell, left_cell
        self._semantic_anchors[f"index_{left}"] = right_cell
        self._semantic_anchors[f"index_{right}"] = left_cell
        return AnimationGroup(
            left_cell.animate.move_to(right_position),
            right_cell.animate.move_to(left_position),
            lag_ratio=0,
        )

    # replace one value and return a transform into a fresh cell in the secondary color
    def set_value(self, index: int, value: object) -> Transform:
        selected = ensure_index(index, len(self.values))
        self.values[selected] = str(value)
        replacement = labeled_box(
            value,
            self.theme,
            width=0.82,
            height=0.78,
            color=self.theme.secondary,
        ).move_to(self.part("cells")[selected])
        return Transform(self.part("cells")[selected], replacement)


# nodes arranged on a circle with directed edges between them
class GraphModel(SemanticMobject):
    # validate node names and edges then place the nodes on a circle and draw the edges
    def __init__(
        self,
        theme: VisualTheme,
        nodes: Iterable[object] | None = None,
        edges: Iterable[Sequence[object]] | None = None,
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        self.node_names = normalized_labels(nodes, fallback=["A", "B", "C", "D"])
        self.node_index = {name: index for index, name in enumerate(self.node_names)}
        if len(self.node_index) != len(self.node_names):
            raise ValueError("graph node labels must be unique")
        default_edges = list(zip(self.node_names, self.node_names[1:]))
        self.edges = [(str(start), str(end)) for start, end in (edges if edges is not None else default_edges)]
        # space the nodes evenly around a circle whose radius grows with the node count
        count = len(self.node_names)
        radius = min(2.25, 0.7 + count * 0.25)
        node_group = VGroup()
        for index, name in enumerate(self.node_names):
            angle = np.pi / 2 - 2 * np.pi * index / max(count, 1)
            node = circle_node(name, theme, radius=0.39)
            node.move_to([radius * np.cos(angle), radius * np.sin(angle), 0])
            node_group.add(node)
        edge_group = VGroup()
        for start, end in self.edges:
            if start not in self.node_index or end not in self.node_index:
                raise ValueError(f"edge {start!r}->{end!r} names an unknown graph node")
            source = node_group[self.node_index[start]]
            target = node_group[self.node_index[end]]
            edge_group.add(
                Arrow(
                    source.get_center(),
                    target.get_center(),
                    buff=0.45,
                    color=theme.muted,
                    stroke_width=2,
                    max_tip_length_to_length_ratio=0.16,
                )
            )
        caption = title_above(label, VGroup(edge_group, node_group), theme)
        self.visited: set[str] = set()
        self.register_part("edges", edge_group)
        self.register_part("nodes", node_group)
        self.register_part("label", caption)
        for name, node in zip(self.node_names, node_group):
            self.register_anchor(f"node_{name}", node)
        frame_safe(self)

    # mark a node as visited and return an animation that recolors it
    def visit_node(self, name: object) -> Any:
        key = str(name)
        if key not in self.node_index:
            raise KeyError(f"unknown graph node {key!r}")
        self.visited.add(key)
        return self.part("nodes")[self.node_index[key]].animate.set_color(self.theme.accent)

    # return an animation that emphasizes one directed edge
    def traverse(self, start: object, end: object) -> Any:
        edge = (str(start), str(end))
        try:
            index = self.edges.index(edge)
        except ValueError as exc:
            raise KeyError(f"unknown directed graph edge {edge[0]!r}->{edge[1]!r}") from exc
        return self.part("edges")[index].animate.set_color(self.theme.secondary).set_stroke(width=4)


# a row of states with transition arrows and a marker on the current state
class StateMachine(SemanticMobject):
    # validate states and transitions then build the nodes arrows and current marker
    def __init__(
        self,
        theme: VisualTheme,
        states: Iterable[object] | None = None,
        transitions: Iterable[Sequence[object]] | None = None,
        *,
        initial: object | None = None,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        self.states = normalized_labels(states, fallback=["idle", "work", "done"])
        self.state_index = {state: index for index, state in enumerate(self.states)}
        if len(self.state_index) != len(self.states):
            raise ValueError("state names must be unique")
        self.transitions = [
            (str(start), str(end))
            for start, end in (
                transitions if transitions is not None else zip(self.states, self.states[1:])
            )
        ]
        nodes = VGroup(*[circle_node(state, theme, radius=0.48) for state in self.states]).arrange(RIGHT, buff=1.0)
        arrows = VGroup()
        for start, end in self.transitions:
            if start not in self.state_index or end not in self.state_index:
                raise ValueError(f"transition {start!r}->{end!r} names an unknown state")
            arrows.add(
                Arrow(
                    nodes[self.state_index[start]].get_right(),
                    nodes[self.state_index[end]].get_left(),
                    buff=0.08,
                    color=theme.muted,
                    stroke_width=2,
                )
            )
        first = str(initial) if initial is not None else self.states[0]
        if first not in self.state_index:
            raise ValueError(f"unknown initial state {first!r}")
        self.current_state = first
        marker = SurroundingRectangle(
            nodes[self.state_index[first]],
            color=theme.accent,
            buff=0.10,
            corner_radius=0.16,
        )
        caption = title_above(label, VGroup(nodes, arrows), theme)
        self.register_part("transitions", arrows)
        self.register_part("states", nodes)
        self.register_part("current", marker)
        self.register_part("label", caption)
        frame_safe(self)

    # follow a valid transition and return a transform that moves the marker
    def transition_to(self, state: object) -> Transform:
        destination = str(state)
        if destination not in self.state_index:
            raise KeyError(f"unknown state {destination!r}")
        if (self.current_state, destination) not in self.transitions:
            raise ValueError(f"no transition from {self.current_state!r} to {destination!r}")
        self.current_state = destination
        marker = SurroundingRectangle(
            self.part("states")[self.state_index[destination]],
            color=self.theme.accent,
            buff=0.10,
            corner_radius=0.16,
        )
        return Transform(self.part("current"), marker)


# a row of tokens with curved attention links drawn on demand
class TokenFlow(SemanticMobject):
    # build the token boxes and an empty link group
    def __init__(
        self,
        theme: VisualTheme,
        tokens: Iterable[object] | None = None,
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        self.tokens = normalized_labels(tokens, fallback=["The", "cat", "sat"])
        token_nodes = VGroup(
            *[labeled_box(token, theme, width=1.25, height=0.7, color=theme.primary) for token in self.tokens]
        ).arrange(RIGHT, buff=0.38)
        links = VGroup()
        caption = title_above(label, token_nodes, theme)
        self.register_part("tokens", token_nodes)
        self.register_part("context_links", links)
        self.register_part("label", caption)
        for index, token in enumerate(token_nodes):
            self.register_anchor(f"token_{index}", token)
        frame_safe(self)

    # validate attention weights and return a transform that draws links weighted by attention
    def mix_context(self, target: int, weights: Sequence[float]) -> Transform:
        target_index = ensure_index(target, len(self.tokens), name="target token")
        if len(weights) != len(self.tokens):
            raise ValueError("attention weights must match the token count")
        values = [float(weight) for weight in weights]
        if any(weight < 0 for weight in values) or not np.isclose(sum(values), 1.0, atol=1e-8):
            raise ValueError("attention weights must be non-negative and sum to one")
        tokens = self.part("tokens")
        links = VGroup()
        # draw a curved arrow from each other token whose weight is positive with thickness and opacity from the weight
        for index, weight in enumerate(values):
            if index == target_index or weight <= 0:
                continue
            links.add(
                CurvedArrow(
                    tokens[index].get_top(),
                    tokens[target_index].get_top(),
                    angle=-0.45 if index < target_index else 0.45,
                    color=self.theme.accent,
                    stroke_width=1.5 + 6 * weight,
                    stroke_opacity=0.25 + 0.75 * weight,
                )
            )
        self.context_weights = values
        return Transform(self.part("context_links"), links)


# a vertical column of neurons whose color and opacity follow their activation
class NeuralLayer(SemanticMobject):
    # build the neuron circles with optional labels
    def __init__(
        self,
        theme: VisualTheme,
        size: int = 5,
        *,
        labels: Iterable[object] | None = None,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        if size < 1:
            raise ValueError("neural layer size must be positive")
        names = [str(value) for value in labels] if labels is not None else [""] * size
        if len(names) != size:
            raise ValueError("neuron labels must match layer size")
        neurons = VGroup(
            *[circle_node(name, theme, radius=0.30, color=theme.primary) for name in names]
        ).arrange(DOWN, buff=0.28)
        caption = title_above(label, neurons, theme)
        self.activations = [0.0] * size
        self.register_part("neurons", neurons)
        self.register_part("label", caption)
        for index, neuron in enumerate(neurons):
            self.register_anchor(f"neuron_{index}", neuron)
        frame_safe(self)

    # validate activations and return a staggered animation that recolors each neuron
    def activate(self, values: Sequence[float]) -> AnimationGroup:
        if len(values) != len(self.activations):
            raise ValueError("activation values must match layer size")
        normalized = [float(value) for value in values]
        if any(not 0 <= value <= 1 for value in normalized):
            raise ValueError("activation values must be between zero and one")
        self.activations = normalized
        animations = []
        for neuron, value in zip(self.part("neurons"), normalized):
            color = self.theme.accent if value >= 0.5 else self.theme.primary
            animations.append(neuron.animate.set_color(color).set_opacity(0.25 + 0.75 * value))
        return AnimationGroup(*animations, lag_ratio=0.05)


__all__ = [
    "ArrayModel",
    "GraphModel",
    "NeuralLayer",
    "StateMachine",
    "TokenFlow",
]
