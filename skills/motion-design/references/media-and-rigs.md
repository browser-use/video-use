# Media and articulated motion

`runtime/media.mjs` handles arbitrary image crop geometry, decoded loading, scoped masks, held cel timing, sprite sheet rectangles and paused footage seeking. `coverRect` preserves source aspect and accepts normalized focal coordinates; `drawCover` applies it to a canvas. `withMask` restores drawing state even when a callback fails. The caller owns the mask's geometry and timing.

`celIndex(time, durations, {loop})` samples explicitly held drawings in any seek order. `spriteRect` maps a cel to an arbitrary sheet grid. These support a supplied or authored drawing family; they do not generate consistent character drawings. Save all poses and their provenance with the project. Procedural puppet interpolation and genuine frame-by-frame drawing are different production methods.

For moving footage, `seekMedia(video, seconds)` pauses playback and waits for seek/decode readiness. Await it from `window.seek` before drawing. Source time must be inside the media duration. The project must decide its mapping from film time to source time. Do not let wall-clock playback choose export frames.

`runtime/rig.mjs` exports `twoBoneIK({root,target,upper,lower,bend})`. It solves any two connected 2D segments and returns the elbow, attainable endpoint, angles and reachability. It clamps unattainable targets without stretching bone lengths. The caller can change its staging or use the reachability flag to repair a contact. This is a limb/linked-mechanism primitive, not a complete rig, physics solver, expression generator or character template.

Compose these with `motion.mjs` numeric pose tracks and transform hierarchies. Put specific anatomy, materials, expressions and shot choreography in the authored project. Keep drawing, editable appearance and the numeric performance track separate in the authored project.

## Seek precision and readiness

`seekMedia(video, time, {timeout=15000, frameEnd})` returns `{requestedTime, seekTime, landedTime}`. When `frameEnd` is supplied, it samples inside the explicit source-frame interval and also returns `frameInterval`. Use actual presentation timestamps for variable-frame-rate footage; do not infer frame intervals from nominal FPS.

Targets round forward to browser microsecond precision. Requests that round onto the source duration, or intervals too narrow to represent, fail explicitly. Post-seek playback-clock reporting may differ by up to two microseconds; that allowance never skips a new target. Playback position and decoded readiness do not prove decoded frame identity. Verify presented-frame metadata or pixels when exact source-frame identity matters, and use a server with HTTP byte-range support.

Await each call before issuing another seek on the same media element. A timeout or media error rejects the call and removes its event listeners. Browser clock precision varies; the unit tests simulate media events and do not certify every browser or codec.

Run `node --test tests/test_motion_runtime.mjs tests/test_motion_media.mjs tests/test_motion_rig.mjs` from the repository root.
