# Overlay compositions, protected regions, and the preflight gate

`helpers/render.py` validates every overlay's spatial and temporal contract
before it starts an expensive render. This file documents that contract; the
EDL example in `SKILL.md` shows the fields in place.

## Rectangles

All rectangles are normalized to the output frame: `{"x", "y", "width",
"height"}` as fractions in `0..1`, origin top-left. A rectangle must stay
inside the frame. Overlays without `composition`, `layout`, or `rect` keep the
legacy full-frame behavior and are not checked for collisions.

## Named layouts

| Layout | Rect | Use |
|---|---|---|
| `full` | whole canvas | cutaways; stops above the caption rail when captions exist |
| `center` | 0.18, 0.15, 0.64 × 0.58 | inset panel with a title band above |
| `left` / `right` | 0.04 or 0.52, 0.17, 0.44 × 0.57 | one half of a split |
| `pip_left` / `pip_center` / `pip_right` | y 0.50, 0.30 × 0.28 | small picture-in-picture |
| `custom` | your `rect` | anything else |

## Compositions

A composition names the editorial relationship, not just coordinates:

- `cutaway` owns layout `full` and intentionally replaces the base canvas. It
  is the only composition allowed to cover a protected region.
- `split_left` / `split_right` own layouts `left` / `right`; the footage sits on
  the named side and the illustration occupies the companion region.
- `picture_in_picture` requires `layout: pip_left|pip_center|pip_right` or a
  custom `rect` in genuine negative space.

A composition cannot be combined with a different named layout or a custom
rect. Use `fit: cover` for an intentional crop, `fit: contain` when every source
pixel matters.

## Protected regions

Declare each underlying illustration's occupied area and active window in
`protected_regions`:

```json
{"owner": "base_matrix", "start_in_output": 30.0, "duration": 8.0,
 "rect": {"x": 0.08, "y": 0.18, "width": 0.84, "height": 0.56}}
```

The renderer rejects any split or picture-in-picture overlay whose rect
intersects a protected region during the same output interval. Two overlays
that overlap in both space and time are also rejected unless one sets
`allow_overlap: true`.

## Caption rail

When subtitles exist, `captions.safe_region` (default: bottom 16% of the
frame) is reserved. A `full` layout is shortened to stop above it; any other
named or custom rect that intersects it is rejected, so captions never cover
footage or illustrations.

## Preflight gate

Run the cheap insertion check before a full composite:

```bash
python helpers/render.py <edl> -o <edit>/overlay_preflight.png \
  --preflight-overlays --preflight-base <base-video>
```

It draws entry, middle, and exit frames for each overlay with the overlay rect,
the protected regions, and the caption rail marked. Inspect it, fix the EDL,
and only then render the preview.
