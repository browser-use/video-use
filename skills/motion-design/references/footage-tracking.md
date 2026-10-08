# Attaching graphics to filmed planes

`helpers/motion_track.py` tracks a user-selected planar region through a video and exports measured homographies. This supports graphics attached to a wall, sheet of paper, sign, or another sufficiently textured plane. It does not segment people, infer hidden surfaces, generate an occlusion mask, or solve a 3D camera. Objects with depth, reflections, deformation, large perspective changes, blur and occlusion may break the planar assumption.

The optional dependency group is `motion-tracking`; install it deliberately when needed with `pip install '.[motion-tracking]'`. Install with `uv sync --extra motion-tracking` when using uv. FFmpeg and ffprobe must also be on PATH.

```bash
python3 helpers/motion_track.py source.mp4 -o plane-track.json \
  --roi 320,180,640,360
python3 helpers/motion_track.py source.mp4 -o plane-track.json \
  --quad '[[320,180],[920,205],[955,520],[345,540]]'
```

Coordinates are pixels in the **stored video frame**, with origin at the upper left. The decoder explicitly disables metadata autorotation. Normalize rotated phone footage into the intended display orientation before selecting points and compositing. The quad must be convex, inside the first frame, and ordered around its perimeter. An ROI is `x,y,width,height`; its far corner is `x+width-1,y+height-1`. Choose a region on the actual plane, excluding moving people and unrelated background where possible.

The helper finds image features within that first region, follows them using pyramidal Lucas–Kanade optical flow, checks that backward flow returns to the original observations, and fits a homography with RANSAC. These mechanisms are documented in the primary [OpenCV optical-flow API](https://docs.opencv.org/4.13.0/dc/d6b/group__video__track.html) and [homography API](https://docs.opencv.org/4.13.0/d9/d0c/group__calib3d.html). Inlier features retain their reference-frame positions, so exported matrices map directly from the initial frame to each subsequent observation.

## Data and failure contract

Schema version 1 records the source hash, stored width and height, decoded frame count, exact rational frame-rate metadata and actual presentation timestamps from ffprobe. `frame.time` is relative to the first observed timestamp; `frame.sourceTime` retains the original source timestamp. Variable frame timing is preserved; do not substitute `index / averageFps` when sampling.

Frame times are derived from integer presentation ticks multiplied by the rational stream time base. The first timestamp is subtracted before converting to floating-point seconds, preserving frame-boundary sampling even with a nonzero source offset. If integer ticks are unavailable, the helper explicitly records rounded timestamp fallback in `source.timestampPrecision`. `source.duration` measures coverage from the first observed frame to the stream end; `streamDuration` and `streamStartTime` preserve the separate source quantities. Integer duration ticks are preferred over rounded duration text.

Each frame has `initialized`, `tracked`, or `lost` status. Successful frames contain a 3×3 `homography`, transformed four-point `quad`, surviving feature count, RANSAC inlier count, reprojection error and a confidence indicator. Matrix rows use the standard projective mapping:

```text
denominator = h20*x + h21*y + h22
mappedX = (h00*x + h01*y + h02) / denominator
mappedY = (h10*x + h11*y + h12) / denominator
```

Confidence combines the fraction of initial features still supported by the plane and median reprojection error. It is a diagnostic, **not a calibrated probability that the plane is correct**. Repeated textures or an unrelated coherent foreground can still produce a convincing wrong track. Inspect the actual corners and an attached graphic through the entire shot.

If feature evidence, inlier support or numerical geometry fails, the helper writes `lost`, a reason, and null geometry. It stays lost for the remainder of that run, including if similar-looking content later returns. It does not extrapolate or silently reacquire. Hide the overlay during lost spans; split the source and explicitly initialize a new region when tracking should restart. Long clips may run out of initial features because the helper does not replenish them with unverified new points.

## Compose and inspect

Keep the authored overlay in the reference frame's coordinate system and apply the homography when compositing. A graphic designed in its own local rectangle needs an additional initial mapping into the selected source plane. Respect source scaling, cropping, display orientation and sample aspect ratio in the final composition. Add separately authored masks when real objects should pass in front; a plane track itself contains no occlusion information.

Track at the source presentation times. When the render timeline differs, map render time back to source time before sampling. Holding the latest observation within its frame interval is conservative; do not interpolate through lost observations. Inspect the first attachment, peak perspective, fastest motion, partial occlusion and final attachment. Numeric tests on synthetic planes establish transform recovery, not production quality on arbitrary live action.

Validation covers independent textured surfaces with known translation, opposite direction, rotation and scaling; nonuniform timestamps; featureless regions; disappearance without hallucinated continuation; and full decode of an encoded source. These tests are synthetic evidence, not a claim that a live-action tracking showcase has been completed.

## Browser consumer

The dependency-free ES module `skills/motion-design/runtime/tracking.mjs` exports `createTrackSampler(track)`, `applyHomography(H, point)` and `homographyToMatrix3d(H, placement)`.

`createTrackSampler` snapshots the input and returns `{visible, frame, reason?}` for any requested time, including backward seeks. It holds observations on `[frame.time, next.time)`, hides lost observations immediately, and hides the overlay outside coverage. The final observation uses `source.duration`, falling back to a nominal frame period if coverage is missing. Returned geometry is copied so drawing code cannot mutate future samples.

`applyHomography` maps a reference-frame point into the observed frame. `homographyToMatrix3d` produces CSS column-major values for an overlay authored at full source dimensions with `transform-origin: 0 0`. Supply `sourceWidth`, `sourceHeight`, `outputWidth`, `outputHeight`, and optional `offsetX`/`offsetY` to match the placed video, including letterboxing or crop offsets.

These helpers do not import the Animation or Capture modules. The caller must independently await video decode readiness and map its playback clock to the track before drawing. Playback position alone does not prove exact decoded-frame identity.

The CLI refuses existing output paths, including symlinks, to preserve source media and earlier results. Choose a new filename for a new attempt.

Run `python -m pytest tests/test_motion_track.py` and `node --test tests/test_motion_tracking_runtime.mjs`. The Python suite needs OpenCV; decode tests additionally need FFmpeg/ffprobe.
