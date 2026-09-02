# CeTZ for general STEM illustrations

CeTZ is a Typst drawing package with relative coordinates, anchors, vector
shapes, paths, content, grouping, and projection tools. Use it for scientific
figures that are spatially rich but do not need Manim's continuous object
animation: geometry, apparatus, fields, coordinate plots, trees, circuits,
physical systems, and explanatory diagrams.

## Video-use workflow

1. Copy `assets/illustrations/cetz-stem-system/` into the animation slot.
2. Replace the generic nodes and curve with the actual relationship taught by
   the narration. Remove unused parts; do not turn it into a dashboard.
3. Keep `#import "@preview/cetz:0.5.2"` pinned for reproducibility.
4. Render a vector asset or high-resolution still:

   ```bash
   python helpers/render_illustration.py cetz <slot>/main.typ -o <slot>/diagram.svg
   python helpers/render_illustration.py cetz <slot>/main.typ -o <slot>/diagram.png
   ```

5. Inspect text fit and stroke weight at delivery size. Use the SVG directly in
   Manim when animation is needed. For a staged explanation, author only the
   states the viewer needs and reveal them in narration order.

The helper requires the Typst CLI; on macOS install it with `brew install typst`.
Typst downloads the pinned CeTZ package declared by the source on first compile.

Official package page: https://typst.app/universe/package/cetz

Official documentation: https://cetz-package.github.io/docs/
