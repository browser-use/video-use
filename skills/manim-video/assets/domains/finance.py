"""Semantic components for finance, business, and resource explanations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
from manim import (
    Arrow,
    Dot,
    DOWN,
    LEFT,
    Line,
    Polygon,
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
    ensure_amount,
    ensure_index,
    frame_safe,
    label_for,
    labeled_box,
    normalized_labels,
    title_above,
)


# account boxes showing balances with transfers between them
class CashFlow(SemanticMobject):
    # read the account mapping and build the account boxes and links
    def __init__(
        self,
        theme: VisualTheme,
        accounts: Mapping[object, float] | None = None,
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        source = accounts if accounts is not None else {"revenue": 100, "costs": 40, "cash": 60}
        self.names = [str(name) for name in source]
        self.amounts = [float(amount) for amount in source.values()]
        if not self.names:
            self.names, self.amounts = [""], [0.0]
        if any(amount < 0 for amount in self.amounts):
            raise ValueError("cash balances cannot be negative")
        accounts_group = self._accounts()
        links = arrows_for_row(accounts_group, theme, color=theme.secondary)
        caption = title_above(label, VGroup(accounts_group, links), theme)
        self.register_part("accounts", accounts_group)
        self.register_part("links", links)
        self.register_part("label", caption)
        for index, account in enumerate(accounts_group):
            self.register_anchor(f"account_{index}", account)
        frame_safe(self)

    # build one box per account showing its name and balance
    def _accounts(self) -> VGroup:
        return VGroup(
            *[
                labeled_box(
                    f"{name}\n${amount:,.0f}",
                    self.theme,
                    width=1.7,
                    height=1.0,
                    color=self.theme.primary if index % 2 == 0 else self.theme.secondary,
                    font_size=18,
                )
                for index, (name, amount) in enumerate(zip(self.names, self.amounts))
            ]
        ).arrange(RIGHT, buff=0.65)

    # move an amount between accounts and return the transform that redraws them
    def transfer(self, source: int, target: int, amount: float) -> Transform:
        source_index = ensure_index(source, len(self.amounts), name="source account")
        target_index = ensure_index(target, len(self.amounts), name="target account")
        if source_index == target_index:
            raise ValueError("cash source and target must differ")
        value = ensure_amount(amount, available=self.amounts[source_index])
        self.amounts[source_index] -= value
        self.amounts[target_index] += value
        return Transform(self.part("accounts"), self._accounts())


# a baseline with stems that grow as compound interest accumulates each period
class CompoundTimeline(SemanticMobject):
    # validate the inputs and build the baseline ticks and the starting balance stem
    def __init__(
        self,
        theme: VisualTheme,
        *,
        principal: float = 100.0,
        rate: float = 0.10,
        periods: int = 5,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        if principal < 0 or rate <= -1 or periods < 0:
            raise ValueError("principal, rate, and periods describe an invalid compound timeline")
        self.principal = float(principal)
        self.rate = float(rate)
        self.periods = int(periods)
        self.visible_periods = 0
        baseline = Line(LEFT * 4.5, RIGHT * 4.5, color=theme.muted)
        # one tick per period spread evenly along the baseline
        ticks = VGroup(
            *[
                Line(UP * 0.10, DOWN * 0.10, color=theme.muted).move_to(
                    baseline.point_from_proportion(index / max(self.periods, 1))
                )
                for index in range(self.periods + 1)
            ]
        )
        balances = self._balances(0, baseline)
        caption = title_above(label, VGroup(baseline, balances), theme)
        self.register_part("baseline", baseline)
        self.register_part("ticks", ticks)
        self.register_part("balances", balances)
        self.register_part("label", caption)
        self.register_anchor("start", lambda: baseline.get_start())
        self.register_anchor("end", lambda: baseline.get_end())
        frame_safe(self)

    # compute the compounded balance after a given period
    def balance_at(self, period: int) -> float:
        if not 0 <= int(period) <= self.periods:
            raise IndexError(f"period {period} is outside 0..{self.periods}")
        return self.principal * (1 + self.rate) ** int(period)

    # build a stem and amount label for each period up to through scaled by the final balance
    def _balances(self, through: int, baseline: Line) -> VGroup:
        values = VGroup()
        maximum = max(self.balance_at(self.periods), self.principal, 1e-9)
        for period in range(through + 1):
            # stem height scales between a small floor and the largest balance
            point = baseline.point_from_proportion(period / max(self.periods, 1))
            height = 0.25 + 1.8 * self.balance_at(period) / maximum
            stem = Line(point, point + UP * height, color=self.theme.primary, stroke_width=5)
            amount = label_for(
                f"${self.balance_at(period):,.0f}",
                self.theme,
                size=16,
                width=1.15,
            ).next_to(stem, UP, buff=0.08)
            values.add(VGroup(stem, amount))
        return values

    # reveal more periods and return the transform that redraws the stems
    def compound(self, periods: int = 1) -> Transform:
        if periods < 0:
            raise ValueError("compound step cannot be negative")
        self.visible_periods = min(self.periods, self.visible_periods + int(periods))
        replacement = self._balances(self.visible_periods, self.part("baseline"))
        return Transform(self.part("balances"), replacement)


# stacked trapezoids whose widths follow the count at each stage
class Funnel(SemanticMobject):
    # read the stage mapping and build the funnel levels
    def __init__(
        self,
        theme: VisualTheme,
        stages: Mapping[object, float] | None = None,
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        source = stages if stages is not None else {"visit": 1000, "trial": 300, "buy": 90}
        self.names = [str(name) for name in source]
        self.counts = [float(value) for value in source.values()]
        if not self.names:
            self.names, self.counts = [""], [0.0]
        if any(value < 0 for value in self.counts):
            raise ValueError("funnel counts cannot be negative")
        levels = self._levels()
        caption = title_above(label, levels, theme)
        self.register_part("stages", levels)
        self.register_part("label", caption)
        for index, stage in enumerate(levels):
            self.register_anchor(f"stage_{index}", stage)
        frame_safe(self)

    # build one trapezoid per stage with width proportional to its count
    def _levels(self) -> VGroup:
        maximum = max(max(self.counts), 1e-9)
        levels = VGroup()
        for index, (name, count) in enumerate(zip(self.names, self.counts)):
            # width scales with the count relative to the largest stage and the shape narrows toward the bottom
            width = 1.4 + 5.2 * count / maximum
            shape = Polygon(
                [-width / 2, 0.42, 0],
                [width / 2, 0.42, 0],
                [width * 0.43, -0.42, 0],
                [-width * 0.43, -0.42, 0],
                color=self.theme.primary if index % 2 == 0 else self.theme.secondary,
                fill_color=self.theme.primary if index % 2 == 0 else self.theme.secondary,
                fill_opacity=0.16 + index * 0.06,
            )
            text = label_for(f"{name}  {count:g}", self.theme, size=18, width=width - 0.2)
            levels.add(VGroup(shape, text))
        levels.arrange(DOWN, buff=0.08)
        return levels

    # move an amount between stages and return the transform that redraws the funnel
    def convert(self, source: int, target: int, amount: float) -> Transform:
        source_index = ensure_index(source, len(self.counts), name="source funnel stage")
        target_index = ensure_index(target, len(self.counts), name="target funnel stage")
        if source_index == target_index:
            raise ValueError("funnel source and target must differ")
        value = ensure_amount(amount, available=self.counts[source_index])
        self.counts[source_index] -= value
        self.counts[target_index] += value
        return Transform(self.part("stages"), self._levels())


# factors arranged in a ring with arrows around it and a pulse that circles them
class FeedbackLoop(SemanticMobject):
    # place the factor boxes on a circle and draw the ring of arrows and the pulse
    def __init__(
        self,
        theme: VisualTheme,
        factors: Iterable[object] | None = None,
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        self.factors = normalized_labels(factors, fallback=["users", "data", "quality"])
        count = len(self.factors)
        nodes = VGroup()
        # space the factors evenly around a circle whose radius grows with the count
        radius = min(2.4, 1.1 + count * 0.22)
        for index, factor in enumerate(self.factors):
            angle = np.pi / 2 - 2 * np.pi * index / max(count, 1)
            node = labeled_box(factor, theme, width=1.35, height=0.66, color=theme.primary)
            node.move_to([radius * np.cos(angle), radius * np.sin(angle), 0])
            nodes.add(node)
        # link each factor to the next and wrap the last one back to the first
        links = VGroup(
            *[
                Arrow(
                    nodes[index].get_center(),
                    nodes[(index + 1) % count].get_center(),
                    buff=0.78,
                    color=theme.secondary,
                    stroke_width=2.4,
                )
                for index in range(count)
                if count > 1
            ]
        )
        pulse = Dot(nodes[0].get_top() + UP * 0.18, color=theme.accent, radius=0.09)
        caption = title_above(label, VGroup(nodes, links), theme)
        self.step = 0
        self.register_part("nodes", nodes)
        self.register_part("links", links)
        self.register_part("pulse", pulse)
        self.register_part("label", caption)
        frame_safe(self)

    # advance the pulse to the next factor and return the animation
    def reinforce(self) -> Any:
        self.step = (self.step + 1) % len(self.factors)
        return self.part("pulse").animate.move_to(
            self.part("nodes")[self.step].get_top() + UP * 0.18
        )


# stock boxes holding amounts with transfers between them
class ResourceFlow(SemanticMobject):
    # read the resource mapping and build the stocks and links
    def __init__(
        self,
        theme: VisualTheme,
        resources: Mapping[object, float] | None = None,
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        source = resources if resources is not None else {"input": 100, "work": 20, "output": 80}
        self.names = [str(name) for name in source]
        self.amounts = [float(value) for value in source.values()]
        if not self.names:
            self.names, self.amounts = [""], [0.0]
        if any(value < 0 for value in self.amounts):
            raise ValueError("resource amounts cannot be negative")
        stocks = self._stocks()
        links = arrows_for_row(stocks, theme, color=theme.accent)
        caption = title_above(label, VGroup(stocks, links), theme)
        self.register_part("stocks", stocks)
        self.register_part("links", links)
        self.register_part("label", caption)
        for index, stock in enumerate(stocks):
            self.register_anchor(f"stock_{index}", stock)
        frame_safe(self)

    # build one box per stock showing its name and amount
    def _stocks(self) -> VGroup:
        return VGroup(
            *[
                labeled_box(
                    f"{name}\n{amount:g}",
                    self.theme,
                    width=1.65,
                    height=1.0,
                    color=self.theme.secondary if index % 2 else self.theme.primary,
                    font_size=18,
                )
                for index, (name, amount) in enumerate(zip(self.names, self.amounts))
            ]
        ).arrange(RIGHT, buff=0.65)

    # move an amount between stocks and return the transform that redraws them
    def transfer(self, source: int, target: int, amount: float) -> Transform:
        source_index = ensure_index(source, len(self.amounts), name="source stock")
        target_index = ensure_index(target, len(self.amounts), name="target stock")
        if source_index == target_index:
            raise ValueError("resource source and target must differ")
        value = ensure_amount(amount, available=self.amounts[source_index])
        self.amounts[source_index] -= value
        self.amounts[target_index] += value
        return Transform(self.part("stocks"), self._stocks())


__all__ = ["CashFlow", "CompoundTimeline", "FeedbackLoop", "Funnel", "ResourceFlow"]
