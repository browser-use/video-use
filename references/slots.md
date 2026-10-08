# Isolated browser animation slots

Use `helpers/motion_slot.py` for product-launch sequences, animated UI demos,
kinetic typography and graphic transitions authored in HTML, CSS, SVG and GSAP.
A slot is a project directory under the user's edit/animations folder. The helper
creates it, records supplied assets, invokes the native HyperFrames checks and
renderer, and verifies the resulting MP4. It does not modify an EDL or install
packages automatically.

## Create and author

```sh
python helpers/motion_slot.py init --edit-dir /path/to/project/edit \
  --name launch --width 1920 --height 1080 --fps 30 --duration 8
```

Creation requires a new name and an edit directory outside the framework checkout.
Dimensions must be even integers from 64 to 8192, FPS must be an integer from
1 to 120, and duration must be 1 to 600 seconds ending on a whole frame. The
result contains index.html, MotionKit components, a package manifest, motion.json,
assets.json and a small index.motion.json assertion file.

The starter is an editable smoke composition, not a finished creative direction.
Replace its copy, visual hierarchy and choreography for the brief. MotionKit offers
DOM/SVG primitives for text, terminal windows, menus, cursors, paths and simple
geometry. Its animation helpers use a paused GSAP timeline registered with
HyperFrames. Keep animation seekable; do not introduce an independent playback
clock. Helpers for shaded spheres and halos are graphic effects, not a 3D engine.

The slot package declares HyperFrames 0.8.30 and GSAP 3.15.0. Provision those
optional Node packages and the engine browser separately. The helper prints
installation instructions if they are absent and checks both installed versions.
Retain the generated package-lock.json with the project; direct version pins alone
do not lock transitive dependencies. VIDEO_USE_MOTION_NODE_MODULES may point to an
already provisioned node_modules directory; initialization creates a slot symlink
to it, and rendering still checks the installed versions.

## Record supplied assets

```sh
python helpers/motion_slot.py asset /path/to/project/edit/animations/launch \
  /path/to/supplied/mark.svg --source-url https://example.com/source \
  --license "project supplied rights declaration"
```

This copies an explicitly supplied file under assets/ and records its byte hash,
size, source URL and rights note. Duplicate names are rejected. A rights note is
a declaration, not proof of reuse permission. Copying a file does not register it
in the animation automatically, and the helper does not download media or fonts.
The asset manifest assumes one writer; simultaneous additions are unsupported.

## Check and render

```sh
python helpers/motion_slot.py check /path/to/project/edit/animations/launch
python helpers/motion_slot.py render /path/to/project/edit/animations/launch \
  --output /path/to/project/edit/animations/launch/render_v1.mp4
```

Check runs native lint and motion checks. Render invokes the installed engine
with strict rendering and no best-effort fallback, then probes and fully decodes
the actual MP4 using ffprobe and FFmpeg. Dimensions, average FPS and decoded frame
count must match motion.json. The verification sidecar records that specification,
the encoded file hash and probe data. These checks do not prove visual quality,
audio correctness, intended choreography or correct use of assets. Review the
encoded motion and any audio before delivery.

Rendering requires new MP4 and verification filenames outside the framework.
It stages output before verification, then publishes through exclusive hard links.
A competing file is preserved and normal publication errors roll back files created
by this attempt. The two files are not one atomic transaction; abrupt interruption
can leave an incomplete set. The destination filesystem must support hard links.
Use a versioned name for each revision.

## Handoff and limits

Use the verified MP4 as a source in the existing editing workflow. The helper does
not automatically write an EDL, provide transparent WebM output, retime another
source or compose the final video. Rational noninteger FPS is deliberately rejected
by this adapter. Text shaping and font rendering depend on the runtime environment.

This addition bundles only helper code and an authored HTML/CSS/JS starter. It
contains no third-party media, font files or vendored engine packages. Broader
motion-design guidance and the separate browser-capture runtime remain in later
candidates. Tests cover offline contracts, animation math, actual MP4 verification
and output publication. The optional browser test requires provisioned Playwright
and GSAP; browser seek behavior and native HyperFrames rendering were not run for
this draft in the current environment.
