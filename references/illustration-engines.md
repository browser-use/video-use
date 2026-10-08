# Illustration engine routing

Choose per visual beat. The engine is a means to teach the narration, not a
style badge.

| Need | First choice | Why |
|---|---|---|
| Equations changing over time, graph morphs, state transitions | Manim | Native temporal animation and mathematical objects |
| Constraint-driven mathematical structure, dense relationship tables, layouts that should solve themselves | Penrose | Separates mathematical facts from visual constraints |
| General STEM figures, geometry, apparatus, circuits, axes, diagrams, vector stills | CeTZ | Concise Typst-native vector drawing |
| UI, product screens, kinetic web layouts | HyperFrames or Remotion | Browser/React composition systems |
| Simple card, counter, or one-off annotation | PIL | Smallest useful tool |

Penrose and CeTZ primarily author **illustration assets**. They do not replace
Manim as the default temporal animation engine. Export SVG, inspect it at final
delivery size, then either:

1. use it as a still visual beat;
2. import it into Manim with `SVGMobject` for reveals and highlights; or
3. render a few deliberate states and transition between them.

Do not animate every SVG path merely because paths exist. Group the asset by the
idea the viewer should notice: a row, column, cell, force, component, or causal
step. Voiceover captions remain in the renderer's bottom rail, never inside the
illustration asset.

Read `references/penrose.md` or `references/cetz.md` only after choosing that
engine.
