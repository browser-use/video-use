# Caption images and word cards

Use these helpers to render authored text as transparent images or video layers.
They provide rendering primitives; this PR does not wire them into the existing
video renderer or the ASS caption module from PR #147. Apply captions last when
compositing a final video. Text and its timing must come from the chosen speech or
an explicitly authored title; these tools do not transcribe audio.

Pillow and NumPy are base dependencies. FFmpeg is required for transparent movie
export and reading the FFconcat image timeline. The bundled Alfa Slab One font includes its Open Font License file under
`assets/fonts/`; no companion skill is required to use them.

## Styled SRT caption images

`caption_raster.py` is a Python API. From a project that can import the repository's
helpers, build a transparent cue image as follows:

```python
from pathlib import Path
from helpers.caption_raster import CaptionCue, render_caption_image

render_caption_image(
    CaptionCue(0, 1, "Readable words"),
    Path("/footage/edit/caption.png"),
    width=640,
    height=360,
    config={"font_size": 36, "max_width": 0.8, "y": 0.72, "fill": "#FFFFFF"},
)
```

The API measures and wraps all words, shrinks within declared font-size limits,
and rejects text that cannot fit. Position and width values in 0–1 are canvas
fractions; larger values are pixels. Supported styling includes font choice,
outline, shadow and rounded backgrounds. Colors use RGB or RGBA hex values.
Existing output images are rejected. The default font is bundled Alfa Slab One;
a configured font must exist and be loadable.

`build_caption_track(srt_path, output_dir, width=..., height=...,
total_duration=..., config=...)` writes cue PNGs, a transparent blank and a
`captions.ffconcat` timeline. Use a new or empty output directory. This command
does not delete previous images. The track keeps blank gaps and clips cues to the
declared duration. Short cues are not extended to an arbitrary minimum length.
The FFconcat image timebase supports millisecond SRT boundaries. A final blank
sentinel is included; cap the consumer to `total_duration` when encoding.

Overlapping SRT cues are processed sequentially rather than displayed together.
Malformed blocks and nonpositive cues can be skipped by the parser, so review
input SRTs rather than treating parsing as strict validation. `build_master_srt`
can translate source transcript timestamps through selected ranges, but it uses
declared range durations, not measured encoded segment durations. It does not fix
the legacy rendered-segment caption-offset issue in #161.

`choose_caption_renderer` reports `libass` or `pil` for a caller to use. Automatic
selection uses PIL for raster-specific styles or missing libass support. Explicit
libass selection also falls back with a warning if the subtitles filter is absent.
A caller requesting libass must handle that returned choice; this API does not
perform final compositing itself.

## Frame-addressed word cards

A minimal card manifest uses frame intervals with the start included and end
excluded. Line positions are relative to the picture region, not the full canvas:

```json
{
  "fps": 24,
  "total_frames": 24,
  "picture": [0, 0, 640, 360],
  "font_layout": "basic",
  "fonts": {"body": "@assets/fonts/AlfaSlabOne.ttf"},
  "cards": [{
    "id": "title",
    "start_frame": 0,
    "end_frame": 24,
    "lines": [{"text": "HELLO", "font": "body", "x": 80, "y": 130,
               "cap_height": 48, "color": "#FFFFFF"}],
    "animation": {"entry_frames": 6, "scale_from": 0.8}
  }]
}
```

```sh
python helpers/cards.py /footage/edit/cards.json --frame 12 --out /footage/edit/card.png
python helpers/cards.py /footage/edit/cards.json --out /footage/edit/cards.mov
```

A still export also writes measured bounds to a JSON sidecar. A movie uses the
manifest frame rate, defaults to 30 fps if omitted, and writes transparent QTRLE
frames in a MOV container. New output and sidecar/log paths are required. The movie
contains no audio and its dimensions are the picture region's width and height.

Cards support measured glyph sizing, fitted or curved lines, gradients, entry
scale/blur, outlines, shadows and supplied occlusion masks. `card_support_xyxy`
records support before masking, while `visible_support_xyxy` records remaining
pixels. Masks may be single images, frame filename patterns, or reviewed polygon
sequences. They must match picture-region dimensions. The helper does not detect
or segment subjects automatically.

`font_layout: raqm` explicitly requires a Pillow build with RAQM; it fails rather
than silently switching layout engines. Basic layout and the per-character curved
text path are not proof of correct shaping for all scripts. Inspect glyphs, curves
and supporting shadows at native size. Line rectangles are checked for clipping,
but support effects at canvas edges and collisions between separate cards still
require review. A generated movie alone does not establish readability or sync.

## Tests

```sh
python -m pytest tests/test_caption_raster.py tests/test_cards.py
```

Tests cover styled text, fit failures, gaps, short cue timestamps in a real encoded
track, active frame intervals, measured bounds, subject masks, explicit font layout,
24 fps QTRLE export, decoded alpha, and protection of existing images and sidecars.
RAQM availability is tested through its failure path; successful complex-script
shaping requires a separately equipped environment and visual review.
