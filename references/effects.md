# Canvas effects and reviewed masks

These helpers provide canvas filters, generated graphic images, frame-addressed
transforms and manually seeded mask tracking. They are standalone building blocks;
the current public render CLI is not wired to these new contracts in this PR.
The later Rendering change connects them to the full composition pipeline.

Pillow and NumPy are base dependencies. Install the existing `editing` extra for
OpenCV. Encoding and probing require FFmpeg and ffprobe. No new assets, model
weights or downloaded fonts are included.

## Canvas and static graphics

`helpers/visuals.py` is a Python API. `build_treatment_filters` returns FFmpeg filter
fragments, an output label and the output dimensions. Canvas `fit` is `contain`,
`cover` or `blur`; blur is an explicit choice. Optional focus crops use zoom and
horizontal/vertical focus. Dimensions round down to even pixels.

```python
from helpers.visuals import build_treatment_filters
parts, label, size = build_treatment_filters(
    "[0:v]", {"canvas": {"width": 1080, "height": 1920, "fit": "contain"}},
    fallback_width=1920, fallback_height=1080,
)
```

`render_graphic_layers` creates transparent PNGs and timing entries for `text`,
`box`, `line` and supplied `image` graphics. Give each graphic a start and positive
finite duration or end. Use a new or empty output directory. Previously generated
images are not deleted. A failed later graphic can leave earlier outputs; retry
in a fresh directory after fixing the specification.

Text wraps and shrinks within its declared width and line count, and checks for
canvas clipping. Use `font_path` to select a specific available font. System font
fallbacks can produce different typography across machines; the final fallback
is the licensed Alfa Slab One already bundled by Captions. Values between zero and
one are fractions for graphics coordinates and sizes; effect transforms below
use pixels. Inspect output at delivery size.

## Frame transforms and layers

`helpers/effects.py` supplies `validate_effects`, `validate_layers`, `transform`
and `LayerCompositor`. Validate inputs before evaluating frames. A transform can
set `x`, `y`, `scale`, `rotation`, `opacity`, `blur` or horizontal sinusoidal
`displacement`. Properties are constants or curves:

```json
{"x": {"points": [[0, -30], [12, 0]], "easing": "cubic_out"}, "opacity": 1}
```

Curve frames use the output picture clock. Supported easing is linear, cubic_out,
smoothstep and hold. Optional shutter sampling averages transformed copies of the
same source image; it does not interpolate motion between different source frames.
Transforms resample premultiplied RGBA to preserve edge color through transparency.

Layers run from `start_frame` inclusive to `end_frame` exclusive and compose in
array order from back to front. Supply either an image or a declared source,
optional mask/clip/fill, and effects. Images and staged foreground movies must
match picture-region dimensions. Moving layers need independently sourced media
with provenance; reference-only sources are refused. Supply foreground movies
already trimmed and sized for their active interval. Call the compositor for
each output frame in order; its video readers are sequential. Always close it.

`render_layers` encodes a supplied base and staged foreground layers without audio.
`stage_mapped` resamples native source frames using a nondecreasing `time_map` and
nearest-frame selection. Neither adds optical-flow interpolation or audio retiming.
Both use the manifest `fps` (default 30), refuse existing output/log files and can
leave partial files if an encode fails. Sources must be outside generated outputs.
The full renderer still owns staging, complete manifest validation and delivery.

## Manually seeded tracking

Create a seed document for prepared picture-sized footage:

```json
{"frame": 0, "polygon": [[20, 20], [80, 20], [80, 80], [20, 80]]}
```

```sh
python helpers/track_mask.py edit/prepared.mp4 edit/seed.json --out edit/track.json
```

The tracker follows the polygon vertices in both directions from the seed using
optical flow. Invalid polygons and frame arrays fail early. Uncertain frames are
flagged, and uncertainty stays flagged for the rest of that propagation direction.
Inspect all frames and correct or reseed flagged spans. This is not automatic
subject segmentation or proof of a correct silhouette. The CLI keeps grayscale
frames in memory, so use bounded shots. `refine_mask` can refine an existing mask
with supplied protection for thin foreground details; review its edges too.

## Checks

The tests execute actual canvas filters, retime known synthetic source frames,
verify encoded frame counts and rates, check alpha/color and layer boundaries,
measure polygon translation, flag lost tracks and preserve existing files.
They do not establish subjective motion quality or segmentation quality on
arbitrary footage.
