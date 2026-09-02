"""Semantic components for software systems and data architecture."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from manim import (
    AnimationGroup,
    Arrow,
    Circle,
    Dot,
    DOWN,
    FadeIn,
    MoveAlongPath,
    RIGHT,
    Transform,
    UP,
    VGroup,
)

try:
    from ..teaching import SemanticMobject, VisualTheme
except ImportError:
    from teaching import SemanticMobject, VisualTheme

from ._common import (
    arrows_for_row,
    ensure_index,
    frame_safe,
    label_for,
    labeled_box,
    normalized_labels,
    row_of_boxes,
    title_above,
)


# a row of stages with a request dot that advances across them
class RequestFlow(SemanticMobject):
    # build the stage boxes links and the request dot
    def __init__(
        self,
        theme: VisualTheme,
        stages: Iterable[object] | None = None,
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        self.stages = normalized_labels(stages, fallback=["client", "service", "database"])
        nodes = row_of_boxes(
            self.stages,
            theme,
            fallback=[""],
            color=theme.primary,
            box_width=1.65,
        )
        links = arrows_for_row(nodes, theme)
        request = Dot(nodes[0].get_top() + UP * 0.30, color=theme.accent, radius=0.10)
        caption = title_above(label, VGroup(nodes, links), theme)
        self.request_stage = 0
        self.register_part("stages", nodes)
        self.register_part("links", links)
        self.register_part("request", request)
        self.register_part("label", caption)
        for index, stage in enumerate(nodes):
            self.register_anchor(f"stage_{index}", stage)
        frame_safe(self)

    # move the request to the next stage or an explicit destination
    def advance_request(self, destination: int | None = None) -> Any:
        target = self.request_stage + 1 if destination is None else int(destination)
        target = ensure_index(target, len(self.stages), name="request destination")
        self.request_stage = target
        return self.part("request").animate.move_to(
            self.part("stages")[target].get_top() + UP * 0.30
        )

    # move the request back to the first stage
    def reset_request(self) -> Any:
        self.request_stage = 0
        return self.part("request").animate.move_to(
            self.part("stages")[0].get_top() + UP * 0.30
        )


# services arranged in an ellipse with directed connections and animated requests
class ServiceGraph(SemanticMobject):
    # validate services and connections then place the nodes and draw the links
    def __init__(
        self,
        theme: VisualTheme,
        services: Iterable[object] | None = None,
        connections: Iterable[Sequence[object]] | None = None,
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        self.services = normalized_labels(services, fallback=["api", "cache", "db"])
        self.service_index = {name: index for index, name in enumerate(self.services)}
        if len(self.service_index) != len(self.services):
            raise ValueError("service names must be unique")
        nodes = VGroup(
            *[labeled_box(service, theme, width=1.5, color=theme.primary) for service in self.services]
        )
        # a lone service sits at the center and otherwise nodes spread around an ellipse
        if len(nodes) == 1:
            nodes[0].move_to([0, 0, 0])
        else:
            import numpy as np

            for index, node in enumerate(nodes):
                angle = np.pi / 2 - 2 * np.pi * index / len(nodes)
                node.move_to([2.2 * np.cos(angle), 1.55 * np.sin(angle), 0])
        default_connections = list(zip(self.services, self.services[1:]))
        self.connections = [
            (str(start), str(end))
            for start, end in (connections if connections is not None else default_connections)
        ]
        links = VGroup()
        for start, end in self.connections:
            if start not in self.service_index or end not in self.service_index:
                raise ValueError(f"connection {start!r}->{end!r} names an unknown service")
            links.add(
                Arrow(
                    nodes[self.service_index[start]].get_center(),
                    nodes[self.service_index[end]].get_center(),
                    buff=0.82,
                    color=theme.muted,
                    stroke_width=2,
                )
            )
        requests = VGroup()
        caption = title_above(label, VGroup(nodes, links), theme)
        self.register_part("links", links)
        self.register_part("services", nodes)
        self.register_part("requests", requests)
        self.register_part("label", caption)
        for name, service in zip(self.services, nodes):
            self.register_anchor(f"service_{name}", service)
        frame_safe(self)

    # spawn a request dot and return an animation that moves it along the connection
    def send_request(self, source: object, target: object) -> AnimationGroup:
        connection = (str(source), str(target))
        try:
            link_index = self.connections.index(connection)
        except ValueError as exc:
            raise KeyError(f"unknown service connection {connection[0]!r}->{connection[1]!r}") from exc
        link = self.part("links")[link_index]
        request = Dot(link.get_start(), color=self.theme.accent, radius=0.09)
        self.part("requests").add(request)
        return AnimationGroup(
            FadeIn(request, run_time=0.12),
            MoveAlongPath(request, link),
            lag_ratio=0.15,
        )


# a fixed capacity row of slots with front and back markers
class QueueModel(SemanticMobject):
    # validate capacity and items then build the slots and markers
    def __init__(
        self,
        theme: VisualTheme,
        items: Iterable[object] | None = None,
        *,
        capacity: int = 6,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        if capacity < 1:
            raise ValueError("queue capacity must be positive")
        self.capacity = int(capacity)
        self.items = [str(value) for value in items] if items is not None else ["r1", "r2", "r3"]
        if len(self.items) > self.capacity:
            raise ValueError("initial queue exceeds its capacity")
        slots = self._slots()
        front = label_for("front", theme, size=17, color=theme.muted, width=0.8)
        back = label_for("back", theme, size=17, color=theme.muted, width=0.8)
        # pin the markers under the leftmost and rightmost slots
        front.next_to(slots, DOWN, buff=0.16).align_to(slots, direction=[-1, 0, 0])
        back.next_to(slots, DOWN, buff=0.16).align_to(slots, direction=[1, 0, 0])
        markers = VGroup(front, back)
        caption = title_above(label, slots, theme)
        self.register_part("slots", slots)
        self.register_part("markers", markers)
        self.register_part("label", caption)
        self.register_anchor("front", lambda: slots[0].get_center())
        self.register_anchor("back", lambda: slots[-1].get_center())
        frame_safe(self)

    # build one box per slot with filled slots colored and empty slots faded
    def _slots(self) -> VGroup:
        values = [*self.items, *([""] * (self.capacity - len(self.items)))]
        return VGroup(
            *[
                labeled_box(
                    value,
                    self.theme,
                    width=0.95,
                    height=0.72,
                    color=self.theme.secondary if index < len(self.items) else self.theme.muted,
                    fill_opacity=0.18 if index < len(self.items) else 0.03,
                )
                for index, value in enumerate(values)
            ]
        ).arrange(RIGHT, buff=0.08)

    # append an item and return the transform that redraws the slots
    def enqueue(self, request: object) -> Transform:
        if len(self.items) >= self.capacity:
            raise OverflowError("cannot enqueue into a full queue")
        self.items.append(str(request))
        return Transform(self.part("slots"), self._slots())

    # pop the front item and return it with the transform that redraws the slots
    def dequeue(self) -> tuple[str, Transform]:
        if not self.items:
            raise IndexError("cannot dequeue from an empty queue")
        request = self.items.pop(0)
        return request, Transform(self.part("slots"), self._slots())


# a row of pipeline stages with a packet that propagates across them
class DataPipeline(SemanticMobject):
    # build the stage boxes links and the packet
    def __init__(
        self,
        theme: VisualTheme,
        stages: Iterable[object] | None = None,
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        self.stages = normalized_labels(stages, fallback=["ingest", "clean", "model", "serve"])
        nodes = row_of_boxes(
            self.stages,
            theme,
            fallback=[""],
            color=theme.secondary,
            box_width=1.45,
        )
        links = arrows_for_row(nodes, theme, color=theme.primary)
        packet = Circle(
            radius=0.10,
            color=theme.accent,
            fill_color=theme.accent,
            fill_opacity=1,
        ).move_to(nodes[0].get_top() + UP * 0.28)
        caption = title_above(label, VGroup(nodes, links), theme)
        self.packet_stage = 0
        self.register_part("stages", nodes)
        self.register_part("links", links)
        self.register_part("packet", packet)
        self.register_part("label", caption)
        frame_safe(self)

    # move the packet to the next stage or an explicit destination
    def propagate(self, destination: int | None = None) -> Any:
        target = self.packet_stage + 1 if destination is None else int(destination)
        target = ensure_index(target, len(self.stages), name="pipeline destination")
        self.packet_stage = target
        return self.part("packet").animate.move_to(
            self.part("stages")[target].get_top() + UP * 0.28
        )


__all__ = ["DataPipeline", "QueueModel", "RequestFlow", "ServiceGraph"]
