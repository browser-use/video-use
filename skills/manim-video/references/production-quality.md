# Production Quality Checklist

Standards for publication-ready original explainers.

## Before Code

- [ ] Research is sourced and claims are verified.
- [ ] Narration states a misconception, progression, thesis, and visual payoff.
- [ ] `edit/visual_plan.md` contains the required teaching contract, chapter map,
  beat table, payoff frame, and optional-footage rationale.
- [ ] Semantically named objects and carried state are identified.
- [ ] Coupled representations share a calculation or `LinkedValue`.
- [ ] One `VisualTheme` defines color and title/body/label font roles.
- [ ] Target resolution, aspect ratio, caption rail, and protected regions are
  known.

## Text and Frame Safety

```python
# Keep edge text inside a meaningful safe margin.
label.to_edge(DOWN, buff=0.5)

# Transform copy occupying the same semantic role.
self.play(ReplacementTransform(note1, note2))

# Fit authored copy before rendering.
if text.width > config.frame_width - 1.0:
    text.scale_to_fit_width(config.frame_width - 1.0)
```

- Keep labels at `font_size=24` or larger and titles at 36 or larger; keep at most four foreground groups on screen at any critical frame.
- Use the approved typography; monospace is optional, not universal.
- Test selected fonts on the render host.
- Run `assert_inside_frame()` and `assert_no_overlap()` for every chapter ending.
- Treat clipped titles, labels, equations, and payoff copy as failed renders.

## Spatial and Semantic Continuity

The default 16:9 frame is about 14.2 by 8 units. Reserve real margins, protected
caption space, and enough negative space for attention shifts.

- Important objects persist across beats instead of being recreated.
- A concept keeps its semantic color and approximate position.
- Camera moves only when changing scale or focus teaches something.
- Dim context instead of deleting it when the viewer still needs orientation.
- Remove an object only when completion, replacement, loss, or scope change makes
  that departure meaningful.
- Dense diagrams can exceed six total objects when hierarchy and focus keep the
  active relationship legible; there is no arbitrary object-count limit.

## Animation Quality

- Every animation constructs, transforms, transfers, compares, or reveals a
  causal relationship.
- Do not rotate animation types, dominant colors, or layouts merely for variety.
- Simultaneous motion is coordinated around one causal event.
- Holds exist for reading, comparison, prediction, or payoff. Holds longer than
  three seconds record their purpose.
- Hard cuts, fades, and transform bridges are all valid when editorially
  motivated. Fading every mobject at every boundary is not a cleanup rule.
- The last meaningful state remains visible through the final spoken words.

## Color and Typography

- The shared theme has sufficient contrast on its actual background.
- Muted context remains visible but subordinate.
- Accent denotes the same teaching role across chapters.
- Warning is reserved for exceptions, failures, or risk.
- Font roles remain stable and render cleanly at delivery size.
- No external creator's palette, typography, scene code, or branding is copied.

## Data and Linked Representations

- Axis scales and breaks are honest and labeled when needed.
- Notable values come from calculations rather than duplicated literals.
- Counters, plots, arrows, matrices, and labels agree at initial, intermediate,
  and final states.
- Tests or assertions cover derived matrix products, percentages, balances, and
  other authored values.

## Before Production Render

- [ ] Every chapter class renders by itself at low quality.
- [ ] `begin_beat()` or `next_section()` names internal teaching beats.
- [ ] Targeted preview artifacts include video, first/final frames, contact sheet,
  media metadata, status, and available section metadata.
- [ ] Text-heavy frames have a medium-quality inspection.
- [ ] Safe-frame checks pass.
- [ ] The payoff frame matches `edit/visual_plan.md`.
- [ ] Sourced footage, if any, has provenance, rights evidence, and a written
  reason it is better than illustration.

## Complete-Video Review

- [ ] Watch the full video at 1x; isolated chapter previews are not the quality
  gate.
- [ ] The teaching thesis and final payoff are clear.
- [ ] Important objects persist across multiple beats.
- [ ] Motion reveals relationships instead of decorating.
- [ ] Linked representations remain consistent.
- [ ] Camera and opacity guide attention.
- [ ] Visual language and pacing remain coherent.
- [ ] The video does not repeatedly reset into title-and-card slides.
- [ ] There are no factual errors, invalid values, clipped teaching content,
  caption collisions, broken renders, missing audio, or incorrect delivery
  properties.
- [ ] Existing EDL overlay order, protected regions, captions-last behavior, and
  delivery validation still pass.
