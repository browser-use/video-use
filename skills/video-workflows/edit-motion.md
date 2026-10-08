# Authored clocks and pose registration

Use `helpers/edit_motion.py` when an edit needs a variable speed ramp, a held
source frame, or two reviewed poses registered to the same canvas. These are
planning and image primitives for an authored compositor. They do not decode or
encode media, track a person, generate intermediate motion, or process audio.
This helper does not replace a video renderer or constant-speed derivative
encoder. It has no dependency on other helpers; it uses the standard library,
NumPy and Pillow.

## Source-time planning

```python
from helpers.edit_motion import plan_clock

plan = plan_clock(
    # Small illustrative nonuniform display clock, in seconds.
    timestamps=["10", "10.04", "10.12", "10.20", "10.30", "10.42", "10.50", "10.60"],
    source_end="10.70",
    fps=10,
    audio_policy="separate",
    segments=[
        dict(frames=4, source_start="10", speed_start=1, speed_end=2),
        dict(frames=2, source_start="10.60", speed_start=0, speed_end=0),
    ],
)
```

Each segment specifies its integer output frame count, explicit source start and
two endpoint speeds. Speeds are source seconds per output second. Equal speeds
give a constant rate; two zero speeds give a hold. For duration `D = frames/fps`
and local output time `t`, the requested source time is:

`source_start + speed_start*t + (speed_end-speed_start)*t*t/(2*D)`

This integrates a **linear speed ramp**. It is not linear interpolation between
source endpoints and not an arbitrary easing curve. The exclusive end boundary
is `source_start + D*(speed_start+speed_end)/2`. Output samples occur at
`0/fps, 1/fps, ..., (frames-1)/fps`; they never include `D`. A continuous next
segment starts exactly at that boundary. To jump, skip or reorder source time,
set `cut_before=True` on the next segment. The first segment cannot declare a cut.
No gap is inserted on the output clock.

Supply decimal or fraction strings, or `fractions.Fraction`, for exact authored
clocks. JSON numbers are accepted through their decimal spelling. An already
rounded expression such as `0.1 + 0.2` is not silently changed to `3/10`; a
continuous-boundary mismatch is rejected. Rational frame rates such as
`"30000/1001"` retain their exact clock. Returned times and frame rates are
rational strings, consumable with `Fraction(value)`.

Use the actual decoded, display-order timestamps, not `frame_index / average_fps`.
For example, obtain integer `best_effort_timestamp` values and the video stream's
`time_base` with FFprobe, then multiply using `Fraction`. Keep any nonzero or
negative origin explicit and use that same origin for segments and anchor poses.
Do not silently mix source, staged-excerpt and output clocks.

Each frame covers `[its PTS, next PTS)`. The last covers `[last PTS, source_end)`;
the explicit end must be measured from that frame's real duration or a reviewed
bounded interval, never guessed from average frame rate. Timestamps must be
finite and strictly increasing. Missing or duplicate PTS need an upstream clock
decision; this helper refuses to guess. A request exactly on a PTS selects that
frame. A request between PTS selects the preceding displayed frame. There is no
nearest-frame rounding, extrapolation or end clamping.

The result's `frames` array supplies:

- `output_frame` and exact `output_time`;
- requested `source_time`;
- `source_frame`, its `source_pts`, and exclusive `source_frame_end`.

`source_frame` indexes the supplied timestamp table. Use the complete source
table, or retain a parallel global-index table when supplying a bounded subset.
An index into a subset is not automatically a global decoder frame number.
Record the immutable source SHA-256, source geometry and timestamp-table origin
alongside the plan; this pure function does not open media or verify its identity.

Decode the selected original frames without another implicit FPS conversion,
then feed these indices to the compositor. Repeated indices are deliberate held
frames; skipped indices are dropped frames. Slowing 24 fps footage does not
create additional real poses. Tracks and mattes must follow each row's selected
source frame, not its output frame index.

Bounds are 1–120 output fps, at most 120 output seconds, 1–1000 segments,
1–1,000,000 source timestamps and speeds 0–16. Reverse ramps are unsupported;
an explicit cut may return to an earlier source time. Source timestamps must
lie within ±86400 seconds.

`audio_policy` is mandatory: `silent` asserts there is no source sound to keep;
`separate` means an independently reviewed soundtrack is required. The result
always says `audio_processed=False`. Neither setting discards, retimes or
validates speech. Keep dialogue/action sync on normal-time sections and author
any soundtrack separately. Never apply this picture map to words by accident.

## Register two reviewed poses

```python
from helpers.edit_motion import fit_pose, warp_pose

registration = fit_pose(
    source_landmarks=[[10, 10], [30, 10]],
    target_landmarks=[[30, 25], [30, 65]],
    source_size=[64, 64],
    target_size=[128, 128],
    source_anchor={"frame": 17, "timestamp": "17/24"},
    model="similarity",
    max_error=1.0,
)
# Both use one geometry map and the same resampling mode.
registered_picture = warp_pose(source_rgba_image, registration)
registered_matte = warp_pose(source_l_matte, registration)
```

Landmarks are ordered corresponding points in **integer pixel-center
coordinates**: `(0, 0)` is the top-left pixel center, x increases right and y
increases down. They may be subpixel but must remain inside `[0, width-1]` and
`[0, height-1]`. Source and target dimensions are `[width, height]`; these are
decoded image pixels, not normalized fractions or screen-preview coordinates.
The anchor requires a source frame index and source PTS; an optional `sha256`
records the source identity. The helper records these claims without decoding
the anchor or verifying the hash. Match them to the actual frame before fitting.

`similarity` needs at least two distinct pairs and fits uniform scale, rotation
and translation. Use this for intact people/characters and the first match-cut
attempt. Additional landmarks measure how well one rigid image transform fits
the pose. `affine` needs at least three noncollinear pairs and permits shear and
nonuniform scaling; use it only where that deformation is appropriate, such as
a planar object. Neither model reflects, solves perspective, articulates limbs,
generates a missing pose or detects the correct correspondences.

The result includes a 3×3 forward `source_to_target` matrix, its inverse and every
landmark's residual in target pixels. Matrices multiply homogeneous column
vectors; for NumPy rows, use `points @ matrix[:2, :2].T + matrix[:2, 2]`.
Every residual must be no greater than the caller's `max_error` (default 2 px,
allowed 0–64). Degenerate/duplicate points, reflections, singular scales and
unstable affine fits are rejected. Supported principal scales are 1/16–16 with
anisotropy at most 16; image dimensions are 2–16384 with at most 33554432 pixels.

`warp_pose` accepts an `L` matte, `RGB` picture or `RGBA` layer and returns a new
image on the explicit target canvas. It never modifies the input. Uncovered
pixels are zero; convert RGB to RGBA before warping when transparent coverage
matters. `nearest` and `bilinear` are supported. Bilinear RGBA uses premultiplied
color; matte and alpha use the same sampling map. Small 8-bit rounding differences
in partially transparent color remain possible. No cubic ringing or invented
edge detail is promised. Do not independently resize or blur one layer after
registration if the picture and matte must remain aligned.

Pillow samples an inverse map using pixel-corner coordinates; the helper applies
the explicit half-pixel conversion from the center-based matrix. This follows
the [Pillow coordinate convention](https://pillow.readthedocs.io/en/stable/handbook/concepts.html#coordinate-system)
and [affine transform interface](https://pillow.readthedocs.io/en/stable/reference/ImageTransform.html#PIL.ImageTransform.AffineTransform).

For a near-invisible cut, inspect an outgoing and incoming **real** pose, place
both in the same target geometry, preserve useful motion handles, and review
the actual frame before/at/after the cut. Matching two landmarks does not ensure
matching silhouette, lens, lighting, perspective or velocity. Prefer another
real source frame when the pose does not fit. Record the source anchors and
residuals, then check feet/contact points, the matte edge and the native-motion
release. Composition and timing remain authored decisions.

## Read-only JSON CLI and verification

The CLI accepts the same keyword arguments as the Python functions and prints a
JSON record. It does not write files, launch FFmpeg, or modify source media.

```bash
python helpers/edit_motion.py clock edit/clock-input.json > edit/frame-map.json
python helpers/edit_motion.py pose edit/pose-input.json > edit/pose-map.json
python -m pytest -q tests/test_edit_motion.py
```

Choose new output paths for the shell redirection; the shell, not this helper,
controls overwrite behavior. Tests use only synthetic images and metadata. They
cover nonuniform PTS, exact endpoints, holds, rational-rate clocks, explicit
cuts, invalid bounds, known rotated/scaled poses, affine fits, degenerate
landmarks, half-pixel alignment, and equal warped matte/RGBA alpha. There is no
encoded-video test because this helper supplies no video renderer. A compositor
that consumes it still needs real encoded-frame, cut-boundary and audio review.
