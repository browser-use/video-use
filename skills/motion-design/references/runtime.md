# Composable browser motion primitives

`../runtime/motion.mjs` is a dependency-free ES module. It handles numeric sampling, transform composition, and text measurement. It does not interpret prompts, select scenes, prescribe a palette, or generate creative direction. Use it from Canvas, DOM, SVG, or a renderer adapter; every function is optional.

## Time and controls

`keyframes([{time, value, ease?}, ...])` compiles a pure sampler. Values can be numbers or flat objects with any matching finite numeric properties. Times are strictly increasing and may be nonuniform or negative. Sampling before/after the range holds the first/last value. `ease` on a key controls its outgoing segment. Available curves: `linear`, `inCubic`, `outCubic`, `inOutCubic`, `smooth`, `outBack`; a custom pure function is also accepted. Overshoot is retained. Numeric angle controls interpolate numerically: author the intended rotation direction explicitly, including a full revolution when needed.

`interpolatePose(from, to, amount)` interpolates matching arbitrary named numeric properties. It intentionally does not guess how strings, colors, nested objects, or scene nodes should blend. Represent a color as channels or supply a renderer-specific conversion when appropriate.

`clip(time, {start, duration, rate=1})` returns `{active, progress, time}`. Activity uses a half-open global interval `[start, start+duration)`, preventing two adjacent shots from both claiming a boundary. Progress holds at 0/1 outside the interval; local time is `progress * duration * rate`. Duration and rate must be positive. Nested clips can consume the parent's local time.

`seededRandom(seed)` produces a repeatable sequence for a string/number seed. Generate composition data once when constructing the scene. Do not advance that generator inside `window.seek`: that would make the scene depend on seek history. Deterministic frame sampling means the same authored scene reproduces correctly; it does not mean that a natural-language prompt must select a fixed scene.

## Transform relationships

`matrix2D({x,y,rotation,scaleX,scaleY,skewX,skewY,anchorX,anchorY})` returns a Canvas/SVG affine array `[a,b,c,d,e,f]`. Angles are radians. An anchor is expressed in local coordinates and maps to `(x,y)` in its parent. `compose2D(parent, local)` applies local then parent. `apply2D(matrix, {x,y})` maps a point, useful for attachments or masks. `withTransform(context, transform, draw)` scopes a Canvas group and restores the parent even when the drawing callback throws. Nest groups as needed; there is no mandatory scene schema.

## Typography

Await local font loading before calling `measureText(context, text, style)` or `fitText(context, text, options)`. Styles include `family`, `weight`, `tracking` (pixels), `lineHeight` (font-size multiplier), and measurement `size`. Fitting options include required `width`, optional `height`, `minSize`, and `maxSize`. Explicit newlines are preserved; automatic word wrapping is deliberately an authoring decision. Fit results include size, width, height, per-line widths, and `fits`. A false result means even the minimum size exceeds the box; do not silently clip required content.

Tracking counts Unicode grapheme clusters where `Intl.Segmenter` is available. Tracking-aware drawing remains the scene's job. Height is an intentional line box (`size * lineHeight`) rather than the visible ink bounds; allow for the chosen font's ascenders and descenders when composing precise crops. Optical alignment and deliberate letter overlap still need rendered review.

Keep wording, pacing, and art direction in the authored project. Run the runtime checks with `node --test tests/test_motion_runtime.mjs` from the repository root.

## Media and articulated rigs

Read [media-and-rigs.md](media-and-rigs.md) for image cropping, masks, explicit cel holds, media seek readiness, and two-bone inverse kinematics. Those modules compose with the time, pose, and parent-transform helpers above; they do not require a shared scene template.

These modules require no npm packages. Browser media and Canvas functions need their native browser APIs; numeric sampling and geometry also run in Node. They can be used with the optional [Capture renderer PR](https://github.com/browser-use/video-use/pull/186) or another renderer that drives absolute time.
