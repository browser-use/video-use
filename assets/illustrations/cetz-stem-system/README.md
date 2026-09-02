# CeTZ STEM system starter

This starter is a restrained 16:9 teaching figure: three semantic nodes, causal
arrows, and one response curve. Replace the labels and relationship with the
actual concept; do not add decorative headings inside the illustration.

Render it from the repository root:

```bash
python helpers/render_illustration.py cetz \
  assets/illustrations/cetz-stem-system/main.typ \
  -o /tmp/cetz-stem-system.svg
```

CeTZ is especially useful for geometry, physics apparatus, coordinate systems,
graphs, circuits, trees, and scientific figures that should remain vector-sharp.
For animation, render staged variants or import the SVG into Manim and reveal its
semantic groups. Keep voiceover captions outside the asset.

CeTZ package page: https://typst.app/universe/package/cetz
CeTZ documentation: https://cetz-package.github.io/docs/
