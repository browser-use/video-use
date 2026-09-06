---
name: manim-video
description: Create or edit mathematical and technical animations with Manim, including equation derivations, algorithms, data stories, and animated diagrams.
metadata:
  version: 1.0.0
---

# Manim Video

Build a clear animated explanation and verify the rendered output. Match the user's audience, mathematical content, style, duration, and existing assets.

## Scope and completion

For a new video or substantial narrative redesign, articulate the narrative arc and create a short `plan.md` before coding. For a small text, color, timing, or geometry change, reuse the existing plan and inspect only affected scenes and dependencies; do not restart the whole planning process.

Keep scenes independently renderable. Iterate with draft renders, then render the requested final quality. Inspect the affected output for clipping, overlap, incorrect math, illegible labels, and timing; fix observed failures before delivery. Stitch scenes and add audio only when the requested output requires it. Reuse verified setup; check dependencies on first use or after environment errors.

## References by operation

Read only the reference relevant to the scene or failure. Paths below are relative to this skill folder.

- For concrete palette, scene-code, render/stitch examples or experimental approaches, consult [production examples](references/production-examples.md); these are starting points, not fixed visual or timing rules. A proportional font is acceptable when the actual render has correct kerning and is readable.
- For initial setup, use `scripts/setup.sh` when dependencies are not yet verified. The bundled guides target Manim Community Edition; check the installed version before relying on version-specific APIs.

## References

| File | Contents |
|------|----------|
| `references/animations.md` | Core animations, rate functions, composition, `.animate` syntax, timing patterns |
| `references/mobjects.md` | Text, shapes, VGroup/Group, positioning, styling, custom mobjects |
| `references/visual-design.md` | 12 design principles, opacity layering, layout templates, color palettes |
| `references/equations.md` | LaTeX in Manim, TransformMatchingTex, derivation patterns |
| `references/graphs-and-data.md` | Axes, plotting, BarChart, animated data, algorithm visualization |
| `references/camera-and-3d.md` | MovingCameraScene, ThreeDScene, 3D surfaces, camera control |
| `references/scene-planning.md` | Narrative arcs, layout templates, scene transitions, planning template |
| `references/rendering.md` | CLI reference, quality presets, ffmpeg, voiceover workflow, GIF export |
| `references/troubleshooting.md` | LaTeX errors, animation errors, common mistakes, debugging |
| `references/animation-design-thinking.md` | When to animate vs show static, decomposition, pacing, narration sync |
| `references/updaters-and-trackers.md` | ValueTracker, add_updater, always_redraw, time-based updaters, patterns |
| `references/paper-explainer.md` | Turning research papers into animations — workflow, templates, domain patterns |
| `references/decorations.md` | SurroundingRectangle, Brace, arrows, DashedLine, Angle, annotation lifecycle |
| `references/production-quality.md` | Pre-code, pre-render, post-render checklists, spatial layout, color, tempo |
