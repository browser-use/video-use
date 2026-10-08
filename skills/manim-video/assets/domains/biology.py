"""Semantic components for cellular, sequence, and population processes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from manim import (
    Circle,
    Dot,
    DOWN,
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


# a cell membrane with a row of stages and a cargo dot that advances between them
class CellProcess(SemanticMobject):
    # lay out the membrane the stage boxes the links and the cargo and register them as parts
    def __init__(
        self,
        theme: VisualTheme,
        stages: Iterable[object] | None = None,
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        self.stages = normalized_labels(stages, fallback=["signal", "nucleus", "protein"])
        membrane = Circle(
            radius=2.35,
            color=theme.secondary,
            fill_color=theme.secondary,
            fill_opacity=0.05,
            stroke_width=3,
        )
        nodes = VGroup(
            *[
                labeled_box(stage, theme, width=1.35, height=0.66, color=theme.primary)
                for stage in self.stages
            ]
        ).arrange(RIGHT, buff=0.34)
        # keep the stage row narrower than the membrane
        if nodes.width > 4.1:
            nodes.scale_to_fit_width(4.1)
        links = arrows_for_row(nodes, theme, color=theme.accent)
        cargo = Dot(nodes[0].get_top() + UP * 0.25, color=theme.warning, radius=0.09)
        caption = title_above(label, membrane, theme)
        self.stage = 0
        self.register_part("membrane", membrane)
        self.register_part("stages", nodes)
        self.register_part("links", links)
        self.register_part("cargo", cargo)
        self.register_part("label", caption)
        for index, stage in enumerate(nodes):
            self.register_anchor(f"stage_{index}", stage)
        frame_safe(self)

    # move the cargo to the next stage or an explicit destination and return the animation
    def advance(self, destination: int | None = None) -> Any:
        target = self.stage + 1 if destination is None else int(destination)
        target = ensure_index(target, len(self.stages), name="cell-process destination")
        self.stage = target
        return self.part("cargo").animate.move_to(
            self.part("stages")[target].get_top() + UP * 0.25
        )


# three rows showing dna transcribed to rna and translated to protein
class SequenceProcess(SemanticMobject):
    _TRANSCRIPTION = {"A": "U", "T": "A", "C": "G", "G": "C", "U": "A"}

    # validate the dna bases and build the labeled rows for dna rna and protein
    def __init__(
        self,
        theme: VisualTheme,
        sequence: str = "ATGCGT",
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        self.dna = str(sequence).upper()
        invalid = sorted(set(self.dna) - {"A", "T", "C", "G"})
        if invalid:
            raise ValueError(f"DNA sequence contains unsupported bases: {', '.join(invalid)}")
        dna_row = self._row(self.dna, color=theme.primary)
        rna_row = self._row("" if self.dna else "", color=theme.secondary)
        protein_row = self._row("", color=theme.accent)
        labels = VGroup(
            label_for("DNA", theme, size=18, color=theme.primary, width=0.8).next_to(dna_row, direction=[-1, 0, 0], buff=0.25),
            label_for("RNA", theme, size=18, color=theme.secondary, width=0.8).next_to(rna_row, direction=[-1, 0, 0], buff=0.25),
            label_for("protein", theme, size=18, color=theme.accent, width=1.0).next_to(protein_row, direction=[-1, 0, 0], buff=0.25),
        )
        rows = VGroup(dna_row, rna_row, protein_row).arrange(DOWN, buff=0.58)
        # Reposition labels after arranging the rows.
        for text, row in zip(labels, rows):
            text.next_to(row, direction=[-1, 0, 0], buff=0.25)
        caption = title_above(label, VGroup(rows, labels), theme)
        self.rna = ""
        self.protein: list[str] = []
        self.register_part("dna", dna_row)
        self.register_part("rna", rna_row)
        self.register_part("protein", protein_row)
        self.register_part("row_labels", labels)
        self.register_part("label", caption)
        frame_safe(self)

    # build a row of small base boxes in the given color
    def _row(self, sequence: str | Sequence[str], *, color: str) -> VGroup:
        values = list(sequence) or [""]
        return VGroup(
            *[
                labeled_box(base, self.theme, width=0.58, height=0.55, color=color, font_size=18)
                for base in values
            ]
        ).arrange(RIGHT, buff=0.07)

    # fill the rna row with the complement of each dna base
    def transcribe(self) -> Transform:
        self.rna = "".join(self._TRANSCRIPTION[base] for base in self.dna)
        replacement = self._row(self.rna, color=self.theme.secondary).move_to(self.part("rna"))
        return Transform(self.part("rna"), replacement)

    # group the rna into codons and fill the protein row with one placeholder per full codon
    def translate(self) -> Transform:
        if not self.rna:
            raise RuntimeError("transcribe the sequence before translating it")
        # split into three base codons and drop any trailing partial codon
        codons = [self.rna[index:index + 3] for index in range(0, len(self.rna), 3)]
        self.protein = [f"aa{index + 1}" for index, codon in enumerate(codons) if len(codon) == 3]
        replacement = self._row(self.protein, color=self.theme.accent).move_to(self.part("protein"))
        return Transform(self.part("protein"), replacement)


# labeled compartments holding population counts with transfers between them
class PopulationFlow(SemanticMobject):
    # read the population mapping and build the compartments and links
    def __init__(
        self,
        theme: VisualTheme,
        populations: Mapping[object, float] | None = None,
        *,
        label: object = "",
    ) -> None:
        super().__init__(theme)
        source = populations if populations is not None else {"susceptible": 80, "infected": 20}
        self.names = [str(name) for name in source]
        self.counts = [float(value) for value in source.values()]
        if not self.names:
            self.names = [""]
            self.counts = [0.0]
        if any(value < 0 for value in self.counts):
            raise ValueError("population counts cannot be negative")
        compartments = self._compartments()
        links = arrows_for_row(compartments, theme, color=theme.secondary)
        caption = title_above(label, VGroup(compartments, links), theme)
        self.register_part("compartments", compartments)
        self.register_part("links", links)
        self.register_part("label", caption)
        for index, compartment in enumerate(compartments):
            self.register_anchor(f"population_{index}", compartment)
        frame_safe(self)

    # build one box per population showing its name and current count
    def _compartments(self) -> VGroup:
        return VGroup(
            *[
                labeled_box(
                    f"{name}\n{count:g}",
                    self.theme,
                    width=1.65,
                    height=1.0,
                    color=self.theme.primary if index % 2 == 0 else self.theme.secondary,
                    font_size=18,
                )
                for index, (name, count) in enumerate(zip(self.names, self.counts))
            ]
        ).arrange(RIGHT, buff=0.65)

    # move an amount from one compartment to another and return the transform that redraws them
    def transfer(self, source: int, target: int, amount: float) -> Transform:
        source_index = ensure_index(source, len(self.counts), name="source population")
        target_index = ensure_index(target, len(self.counts), name="target population")
        if source_index == target_index:
            raise ValueError("population source and target must differ")
        value = ensure_amount(amount, available=self.counts[source_index])
        self.counts[source_index] -= value
        self.counts[target_index] += value
        return Transform(self.part("compartments"), self._compartments())


__all__ = ["CellProcess", "PopulationFlow", "SequenceProcess"]
