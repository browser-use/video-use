# Penrose quaternion multiplication table

This is a video-use starter based on Penrose's official
`group-theory/quaternion-multiplication-table` example. It keeps the useful
constraint-authored grid while making the files self-contained for an animation
slot.

Render it from the repository root:

```bash
python helpers/render_illustration.py penrose \
  assets/illustrations/penrose-quaternion-table/quaternion-table.trio.json \
  -o /tmp/quaternion-table.svg
```

Edit `quaternions.substance` to change the mathematical facts and labels. Edit
`MultiplicationTable.style` to change layout, palette, spacing, and typography.
Keep the variation fixed for repeatable layout. Import the result with Manim's
`SVGMobject`, then animate row/column/cell emphasis rather than rebuilding the
table as dozens of independent Manim rectangles.

Origin: https://github.com/penrose/penrose/tree/main/packages/examples/src/group-theory
Penrose is MIT licensed; see the upstream repository for its license.
