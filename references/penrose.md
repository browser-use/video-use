# Penrose for structural mathematical diagrams

Use Penrose when the input is mathematical structure and the desired drawing is
best expressed through constraints: multiplication tables, incidence and set
relationships, graph layouts, geometric configurations, or a dense matrix-like
view. It is a specialized option, not the default for every linear-algebra beat.

Penrose separates a diagram into three programs:

- **Domain** declares mathematical object and predicate types.
- **Substance** declares the actual objects, relationships, and labels.
- **Style** maps those facts to shapes and constraints.

A `.trio.json` file points to all three and fixes the `variation` seed. Preserve
that seed after layout approval so later renders do not unexpectedly rearrange.

## Video-use workflow

1. Copy `assets/illustrations/penrose-quaternion-table/` into the animation slot.
2. Change the Substance facts and labels before changing visual styling.
3. Keep labels short enough to survive a 16:9 delivery frame.
4. Render:

   ```bash
   python helpers/render_illustration.py penrose \
     <slot>/quaternion-table.trio.json -o <slot>/diagram.svg
   ```

5. Inspect the SVG. If the diagram is correct, import it into Manim with
   `SVGMobject` and animate meaningful groups such as one row, one column, and
   their product cell.

`render_illustration.py` pins `@penrose/roger` 3.3.1 and caches it on first use.
Pass `--dump-steps` only when the optimization process itself is useful footage;
ordinary explainers should use the final stable SVG.

The included starter is adapted from Penrose's official
`group-theory/quaternion-multiplication-table` example:
https://penrose.cs.cmu.edu/try/?examples=group-theory/quaternion-multiplication-table

Roger CLI documentation:
https://penrose.cs.cmu.edu/docs/ref/using#command-line-interface-roger
