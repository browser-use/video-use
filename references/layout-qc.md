# Layout QC for generated UI, typography, and illustrations

Hard rule 14: generated layouts are collision-free at every critical frame.

For procedural UI and typography, compute rectangles from the renderer's actual
text metrics instead of estimating them. Parent related elements so a subtitle,
button label, icon, and moving control share one layout calculation. Register
exclusive content regions for primary copy and controls; moving objects must
route around them or become part of them intentionally. Draw decorative layers
before foreground content, or mask them out of protected regions. Check these
constraints for every generated frame or animation keyframe before encoding.
Call `helpers/layout_qc.py` on a critical-frame manifest, or import its
`validate_frame` function in a code-generated renderer to check every frame.
Only foreground elements that must not collide belong in the manifest; declare
an intentional intersection explicitly with `allow_overlap_with`.

## Manifest

```bash
python helpers/layout_qc.py edit/layout_manifest.json
```

```json
{
  "canvas": {"width": 1920, "height": 1080},
  "frames": [
    {"time": 4.0, "elements": [
      {"id": "title", "rect": {"x": 120, "y": 80, "width": 900, "height": 96}},
      {"id": "cursor", "rect": {"x": 400, "y": 300, "width": 32, "height": 32},
       "allow_overlap_with": ["button"]}
    ]}
  ]
}
```

Rectangles are delivery-canvas pixels. Only collision-sensitive foreground
elements belong in the manifest. An element that leaves the canvas, or
intersects another element with positive area without an explicit
`allow_overlap_with` on either side, fails the run. Code-generated renderers can
call `validate_frame` directly for every frame instead of writing a manifest.

## Review

For every generated UI, typography, or illustration scene, extract
full-resolution stills at its entry, most crowded frame, animation extrema, and
settled state. A small filmstrip is not sufficient for layout approval. Watch
dynamic transitions at 1x when an element crosses the frame.
