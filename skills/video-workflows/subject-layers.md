# Inspected subject layers

Use `helpers/subject_layers.py` for a source-derived frozen frame, artwork behind
a subject, a silhouette-controlled reveal, or a small authored parallax move.
It accepts reviewed images and mattes; it does not find subjects, create a
clean plate, decode video, track motion, invent hidden pixels, or choose a style.
The file is standalone: Python 3.10+, Pillow and NumPy only.

The helper separates file validation from frame rendering. Hash each input once,
load it once, then seek arbitrary integer frames without writing or hashing
intermediate images. Production video decoding, source-clock mapping, color
management, encoding and audio remain the surrounding editor's responsibilities.

## Coordinate and alpha contract

- `canvas` and every asset `size` are explicit `[width, height]` pixels. There is
  no implicit resize, crop, rotation from EXIF, pixel-aspect adjustment or fit.
- Image assets must be single-frame RGB or straight RGBA. Mattes must be 8-bit
  grayscale `L`: white keeps, black removes, fractional edges stay fractional.
  A matte binds the SHA256 of its exact source image file and shares its size.
  Optional `source_frame` identifies an authored source-frame index; it must
  agree on the image and matte. This metadata does not verify a video decode.
- File paths are relative to the manifest directory and cannot escape it.
  Changing bytes, source rendition or geometry requires a deliberate new hash
  and matte inspection; matching dimensions alone are insufficient.
- PNG input/output uses **straight alpha**. Array functions use finite floating
  **premultiplied RGBA**, values 0–1, with RGB≤alpha. Color and alpha receive the
  same affine sampling. This prevents hidden RGB outside the matte from leaking
  into transformed edges. Compositing uses encoded RGB, not linear-light RGB;
  the helper does not interpret ICC profiles or perform HDR/color conversion.
- Transform order: subtract source `anchor`, scale, rotate, then translate the
  anchor to canvas `position`. Coordinates describe pixel edges. Positive
  rotation is clockwise on screen. Outside-source pixels are transparent; they
  are never mirrored or stretched to fill missing coverage. Opacity applies
  after transforming. Default sampling is bilinear, consistent with
  `edit_motion.warp_pose`; bicubic and nearest are optional. Bicubic alpha
  overshoot is clipped while preserving the RGB/alpha ratio, avoiding bright
  fringes on nonsaturated edges. No automatic feather, threshold or matte
  dilation is applied.
- Layers are ordered back to front. `frames: [start, end]` is half-open and uses
  integer output indices. Frame `end` is inactive. Keyframes must cover every
  active sample, with no extrapolation. A key at `end-1` can restore the exact
  final rendered pose; a key at `end` is a virtual exit pose not itself rendered.
- Use either `pose` or `keyframes`. Pose fields are `position: [x,y]`,
  `scale: [sx,sy]`, `rotation`, and `opacity`; omitted values mean identity,
  **not inheritance from the preceding key**. Each key has `frame` and an
  outgoing `ease`: `linear`, `smoothstep`, or `hold`. Rotation interpolates the
  supplied unwrapped angles; it does not choose a shortest turn automatically.
  If you set a nonzero anchor, also set position to that anchor for an unmoved
  pose; the default position is explicitly `[0,0]` on the canvas.
- A layer's `mask` cuts its own bound image **before** that layer's transform.
  A layer's `reveal` instead transforms a separately bound silhouette onto the
  **canvas**, then clips the layer through it. `invert: true` selects outside
  the silhouette; reveal opacity applies after inversion. This permits an
  incoming frame or artwork to be revealed through an outgoing subject shape.
- Mark dialogue subtitles `role: "caption"`; all such layers must come last.
  Large editorial type intentionally occluded by a subject is `role: "image"`.
  Composite dialogue captions after every subject, wipe and picture effect.

Bounds reject malformed values, missing sources, mismatched masks, duplicate
layer IDs, uncovered clocks and excessive image allocations. Canvas/assets are
limited to 8,388,608 pixels each; total assets to 67,108,864 pixels; 128 assets/layers;
1–72,000 frames, at most 600 seconds, and fps≤120. Positive scales are 1/256–256.
These are implementation limits, not creative defaults.

## Source → layered freeze → source

First select an actual source frame, inspect its matte at native size over both
light and dark backgrounds, and prepare these local files beside `scene.json`:

- `assets/pose.png`: opaque decoded source pose, RGB or fully opaque RGBA.
- `assets/person.png`: same-size grayscale subject matte, including real gaps.
- `assets/plate.png`: authored same-size clean background. Inspect every pixel
  exposed by the move; an unmodified source frame is not a clean plate.
- `assets/artwork.png`: authored RGBA artwork/type at the same canvas size.

This example computes real file hashes. The numbers describe an editable
three-second component, not a preset title or mandatory film duration. Source
frame180 is an example; replace it with the inspected frame's actual index.

```python
import hashlib, json
from pathlib import Path
from PIL import Image

root = Path("edit/freeze")
def asset(name, kind="image", **extra):
    path = root / "assets" / name
    with Image.open(path) as image:
        size = list(image.size)
    return {"path": "assets/" + name, "kind": kind, "size": size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), **extra}

source = asset("pose.png", source_frame=180)
mask = asset("person.png", "mask", source_sha256=source["sha256"], source_frame=180)
w, h = source["size"]
cx, cy = w/2, h/2
scene = {
    "version": 1, "canvas": [w, h], "fps": 30, "frame_count": 90,
    "assets": {"source": source, "person": mask,
               "plate": asset("plate.png"), "artwork": asset("artwork.png")},
    "layers": [
        {"id": "background", "image": "plate", "frames": [0, 90]},
        {"id": "artwork-behind-subject", "image": "artwork", "frames": [0, 90],
         "keyframes": [{"frame": 0, "opacity": 0, "ease": "smoothstep"},
                       {"frame": 15}, {"frame": 72, "ease": "smoothstep"},
                       {"frame": 89, "opacity": 0}]},
        {"id": "subject", "image": "source", "mask": "person", "frames": [0, 90],
         "anchor": [cx, cy],
         "keyframes": [{"frame": 0, "position": [cx, cy], "ease": "smoothstep"},
                       {"frame": 40, "position": [cx+8, cy-3], "ease": "smoothstep"},
                       {"frame": 89, "position": [cx, cy]}]},
        # Whole-source entry/exit guarantees exact registration despite matte-edge
        # reconstruction differences. It does not conceal missing mid-move pixels.
        {"id": "registered-entry-exit", "image": "source", "frames": [0, 90],
         "keyframes": [{"frame": 0, "ease": "smoothstep"},
                       {"frame": 8, "opacity": 0},
                       {"frame": 81, "opacity": 0, "ease": "smoothstep"},
                       {"frame": 89}]}
    ]}
(root / "scene.json").write_text(json.dumps(scene, indent=2) + "\n")
```

```sh
python helpers/subject_layers.py edit/freeze/scene.json --check
python helpers/subject_layers.py edit/freeze/scene.json \
  --frames 0,1,8,15,40,72,81,88,89 --output-dir edit/freeze/proof-v1
python helpers/subject_layers.py edit/freeze/scene.json \
  --output-dir edit/freeze/frames-v1
```

Output is straight RGBA `frame-000000.png` plus `receipt.json` recording input
and output hashes, frame indices and seconds. Output directories must be new;
existing inputs/renders are never overwritten. Inspect the actual extremes,
entry/exit, holes between limbs and artwork overlap before encoding. Match the
PNG color interpretation to the live source during encoding; a freeze/live
color-space mismatch is not a matte defect.

For a subject-shaped wipe, add `reveal` to the incoming layer:

```json
{"mask": "person", "invert": false, "anchor": [540, 960],
 "keyframes": [{"frame": 0, "position": [540, 960]},
               {"frame": 89, "position": [1200, 960]}]}
```

Those coordinates require a 1080×1920 matte and a layer active on `[0,90)`;
adapt them to actual source/canvas geometry. A moving silhouette does not
automatically cover the full canvas or remember previously swept pixels. It
is the instantaneous authored matte. Arrange the real source motion, crop and
handoff so coverage supports the cut; inspect all boundary frames. Do not
extend a coat/hand contour into rows where no inspected subject exists.

## Callable production boundary

```python
import json
import numpy as np
from pathlib import Path
from helpers.subject_layers import validate_config, load_assets, render_frame

path = Path("edit/freeze/scene.json")
spec = validate_config(json.loads(path.read_text()))
buffers = load_assets(spec, path.parent)  # file hashes/decode once
rgba_frame = np.asarray(render_frame(spec, buffers, 40))
```

For already decoded production arrays, `premultiply(uint8_rgb_or_rgba)` and
`mask_alpha(uint8_or_float_mask)` prepare buffers; `render_frame(spec, buffers,
index)` consumes them without disk IO. Its return is a straight RGBA PIL image.
When replacing streamed buffers, the caller must enforce source frame identity
and matching inspected mask; a frozen PNG hash does not attest different video
frames. Geometry is checked each call. Low-level `masked_layer`, `over`,
`transform_layer`, `transform_mask`, `pose_at`, and `straight_image` are available
without any manifest. Their array operations preserve inputs.

For direct low-level composition, place artwork over the background, then the
masked source over that result. Only after all picture effects should dialogue
captions be composited. All layers must share the destination canvas before
`over`; transform both RGB and alpha together, never resize a matte separately
with unrelated coordinates. To preserve physical contact between two cutouts,
give them the same anchor/motion or explicitly author a contact-preserving rig.

## Aligning with edit_motion

`edit_motion.fit_pose` returns a source-to-target affine matrix in **integer
pixel-center coordinates**: the top-left pixel center is `(0,0)`. Subject-layer
poses use **pixel-edge coordinates**: that center is `(0.5,0.5)`. Do not copy a
center-based translation into an edge-based rotated/scaled pose unchanged.
For column-vector forward matrices, convert with:

```python
shift = np.array([[1, 0, .5], [0, 1, .5], [0, 0, 1]], dtype=float)
matrix_edges = shift @ matrix_centers @ np.linalg.inv(shift)
```

Thus `M_edges = T(+0.5,+0.5) M_centers T(-0.5,-0.5)`. The inverse conversion
reverses those shifts. A center-based anchor/landmark likewise adds 0.5 on both
axes when expressed as an edge coordinate. Rotation/scale make that correction
matter; pure integer translation alone can hide a convention error. The helper
accepts position/scale/rotation, not arbitrary shear; do not silently decompose
a sheared affine matrix into an inexact pose.

The simplest integration is to use `edit_motion.warp_pose` on the source image
and its inspected matte into the target canvas, then layer those fitted buffers
with **identity subject-layer poses** (`anchor:[0,0]`, `position:[0,0]`,
`scale:[1,1]`, `rotation:0`). Do not apply the fitted transform a second time.
For filtered RGBA cutouts, combine source/matte before fitting so premultiplication
preserves color edges. A separately fitted grayscale matte can control a reveal;
its target geometry must match the fitted image. If saving fitted image/mask
files, bind their new hashes and dimensions; retain original fit/source evidence
in the project. Subsequent authored parallax is an additional deliberate
canvas-space move, not another application of the source fit.

Independent foreground/background transforms create 2D parallax, not a recovered
3D scene. A cutout carries only visible source pixels; arms do not reveal hidden
torso, and moving a person exposes background holes. Supply an inspected clean
plate and restrict motion to its supported area. This helper intentionally
leaves holes transparent rather than hallucinating or mirroring a repair.
