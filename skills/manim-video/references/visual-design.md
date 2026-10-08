# Visual Design Principles

## Core Principles

1. **Geometry before algebra** — show the shape or mechanism before notation when
   that progression helps the topic.
2. **Opacity directs attention** — keep the active relationship strongest and
   preserve context at a lower salience.
3. **Beats change understanding** — a beat introduces a state change or a new
   relationship, not merely a new card.
4. **Spatial consistency** — the same concept occupies the same region unless
   movement itself explains something.
5. **Color equals meaning** — assign theme roles to concepts rather than rotating
   colors between chapters.
6. **Progressive disclosure** — construct the simplest useful state first and add
   complexity as the narration earns it.
7. **Transform instead of reset** — maintain object identity when showing how two
   states or representations connect.
8. **Deliberate holds** — pause for reading, comparison, prediction, or payoff.
9. **Visual weight balance** — make the central teaching object large enough to
   own the content area without filling space decoratively.
10. **Consistent motion vocabulary** — reuse movement that has the same meaning.
11. **Direction-specific contrast** — validate the approved palette on its actual
    background rather than assuming every explainer must be dark.
12. **Intentional empty space** — leave enough room for hierarchy, camera focus,
    and captions.

## Layout Patterns

These are compositional starting points, not fixed templates:

- **Single model** — one persistent object with local labels and a payoff state.
- **Linked representations** — equation, geometry, chart, or counter driven by
  the same value.
- **Before and after** — shared inputs and measures, with a visible causal change.
- **System flow** — stable components connected by a request, force, resource, or
  information transfer.
- **Nested focus** — a complete system stays visible while camera and opacity
  isolate a mechanism.
- **Progressive construction** — parts appear only as their relationship becomes
  relevant.

## Semantic Theme

`VisualTheme` defines background, text, muted, primary, secondary, accent,
warning, and title/body/label font roles. Choose those values from the approved
visual direction. Reuse the same theme across chapters and pass it to every
domain component.

Before production:

- Check contrast at the real delivery size and background.
- Confirm the accent remains legible when context is dimmed.
- Reserve warning for exceptions, failures, or risk.
- Confirm semantic roles do not change meaning later in the video.
- Avoid copying another creator's branding, palette, or typography.

## Typography

Typography follows the approved visual direction. Proportional and monospace
fonts are both valid:

- Use proportional display or text faces when they suit the tone and render
  cleanly on the target host.
- Use monospace for code, fixed-width data, or an intentional technical voice.
- Use `MathTex` for mathematical notation that benefits from LaTeX.
- Keep ordinary text at `font_size=18` or larger.
- Define title, body, and label fonts once in `VisualTheme`.
- Fit long labels to their allotted width and run frame-safety checks.
- Preview text-heavy frames at medium quality because low-quality output can hide
  spacing and rasterization defects.

```python
THEME = VisualTheme(
    background="#F6F3EC",
    text="#1D2430",
    muted="#7A8190",
    primary="#315C9B",
    secondary="#3F8A78",
    accent="#D48C24",
    warning="#B84A4A",
    title_font="Source Serif 4",
    body_font="Inter",
    label_font="Inter",
)
```

## Attention Checklist

For every planned payoff or dense frame:

1. What is the one relationship to notice?
2. Which persistent objects are context?
3. Should focus use position, scale, camera, opacity, or a highlight?
4. Do all linked representations agree?
5. Is every label readable and inside the safe frame?
6. Is caption space still clear?
