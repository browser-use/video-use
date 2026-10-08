# Beat sheet (`board.json`) reference

`helpers/board.py` renders a complete visual base from this file. Paths are
relative to the file's directory. Coordinates are fractions of the canvas
(0–1). Times are seconds, or word anchors when an alignment is present.

```json
{
  "version": 1,
  "width": 1920, "height": 1080, "fps": 30,
  "narration": "narration.wav",
  "alignment": "narration.alignment.json",
  "tail": 0.6,
  "music": {"file": "assets/bed.mp3", "gain_db": -20, "fade_out": 2.0},
  "sfx": "sparse",
  "captions_safe_bottom": 0.0,
  "style": {"bg": "#0E0D14", "accent": "#F5546B",
            "fonts": {"display": "auto", "label": "auto", "mono": "auto", "body": "auto"}},
  "beats": [
    {"id": "hook", "at": 0.0,
     "elements": [
       {"id": "woke", "kind": "text", "text": "If you woke up", "style": "display",
        "size": 128, "x": 0.5, "y": 0.42, "enter": "pop", "at": "word:If"}
     ]},
    {"id": "news", "at": "word:Millions",
     "elements": [
       {"id": "card", "kind": "image", "file": "assets/newsweek_card.png",
        "x": 0.03, "y": 0.5, "anchor": "left", "w": 0.6, "h": 0.8,
        "enter": "blocks", "motion": "kenburns"},
       {"id": "sticker", "kind": "text", "text": "Bricked", "style": "sticker",
        "color": "accent", "outline": "white", "rotate": -8, "x": 0.8, "y": 0.42,
        "enter": "pop", "at": "word:bricked", "allow_overlap_with": ["card"]}
     ]}
  ]
}
```

## Top level

| Field | Meaning |
|---|---|
| `width`, `height`, `fps` | Delivery canvas. `--preview` renders at half size. |
| `narration` | WAV/MP3 muxed into the output; sets the duration (`+ tail`) unless `duration` is given. |
| `alignment` | Word timestamps (`{"words": [{"text","start","end"}]}`) from `narrate.py`; enables `word:` anchors. |
| `duration` | Explicit length. Must not be shorter than the narration. |
| `music` | Path or object `{file, gain_db (-20), fade_out (2.0), source_start}`; loops if short. |
| `sfx` | `off`, `sparse` (whoosh on slides/whips, glitch on block reveals), `all` (also pops). `sfx_gain_db` default −18. |
| `captions_safe_bottom` | Fraction reserved for burned captions (use 0.16 when subtitles will be burned); elements may not enter it. |
| `style` | `bg`, `text`, `accent`, `muted`, `palette{name: hex}`, `fonts{role: "auto" | path | "path#index" | "path@Variation"}`, `card{radius, border, shadow}`, `code{bg, text, keyword, string, number, comment, function, radius}`. |

Font roles resolve automatically: `display` → Cubano if installed, else Lilita
One from the tool cache, else Arial Black / Avenir Next Condensed Heavy;
`label` → Bebas Neue / Oswald / Impact; `mono` → JetBrains Mono / Menlo; `body`
→ Rubik / Helvetica Neue. Supply a locally installed or project font you have permission to use.
No font downloader is bundled; preserve licenses if redistributing font files.

## Beats

| Field | Meaning |
|---|---|
| `id` | Unique; used in QC output and element ids. |
| `at` | Start: seconds, `"word:phrase"`, `"word:phrase#2"` (second occurrence), or `"+0.4"` relative to the previous beat start. Omit to start when the previous beat ends. |
| `end` | Optional explicit end (same forms). Default: the next beat's start, or the board end. |
| `bg` | Color string, or `{"image": path, "fit": "cover"|"contain", "motion": "kenburns", "dim": 0.45}`. |
| `elements` | Ordered list; later elements draw on top unless `z` is set. |

Beats may not overlap. Word anchors search forward from the previous beat.

Anchor matching: tokens are lower-cased and stripped of punctuation. An exact
token match wins; a prefix match ("Tamay" for "Tamay's") is used only when no
exact match exists after the search point. Multi-word phrases (`word:Every job`)
and occurrence suffixes (`word:you#2`) disambiguate repeated words.

## Authoring with `helpers/boardlib.py`

```python
import sys
from pathlib import Path
sys.path.insert(0, "<video-use>/helpers")
from boardlib import *

b = BoardBuilder(Path(__file__).resolve().parent)          # edit/
b.beat("hook", 0.0, [display("t_hook", "If you woke up", W + "woke", y=0.4)])
b.beat("title", W + "September", titlecard("Tech Brief", "Sep 2nd, 2026"))
b.beat("signoff", W + "This", endcard("SQLite"))
b.write()                                                  # edit/board.json
```

`write()` applies the genre defaults (1080p30, narration + alignment paths,
sparse SFX, `assets/bed.wav` at −22 dB, dark canvas, coral accent) and rejects
duplicate element ids; keyword arguments override any top-level field.

## Elements (common fields)

| Field | Default | Meaning |
|---|---|---|
| `kind` | `text` | `text`, `image`, `video`, `code`, `emoji`, `box`, `rect` |
| `id` | `<beat>_<kind><n>` | Needed for `allow_overlap_with` |
| `at` | `0` | Seconds after the beat start, or a `word:` anchor (absolute) |
| `until` / `hold` | beat end | Explicit end (seconds after beat start, or anchor) / duration |
| `lead` | `0` | Start the entry this many seconds before the anchor so it *lands* on the word |
| `x`, `y`, `anchor` | `0.5, 0.5, center` | Anchor point placed at (x, y): `center`, `left`, `right`, `top`, `bottom`, `top-left`, … |
| `enter`, `enter_duration` | `pop` (text/emoji/box) or `fade` | `none`, `pop`, `grow`, `fade`, `slide_left/right/top/bottom`, `whip_left/right`, `blocks`, `wipe`, `type` (code only) |
| `exit`, `exit_duration` | `none` | `fade`, `pop`, `slide_*` |
| `motion`, `motion_amount` | `none`, `1` | `kenburns` (slow zoom), `float`, `shake` (first 0.35 s), `drift` |
| `rotate` | `0` | Degrees, counter-clockwise |
| `opacity`, `z` | `1`, order | |
| `sfx` | by entry | Override: `"whoosh"`, `"pop"`, `"glitch"`, or `false` |
| `allow_overlap_with` | `[]` | Ids this element may intentionally intersect |

### `text`

`text`, `style` (`display` default 120 px; `label` 64 px in a box; `sticker`
110 px with outline; `body` 48 px sentence case; `quote`; `mono`), `size`,
`color`, `box` (label box color; default white box with dark text),
`outline` / `outline_width`, `align`, `max_w` (wrap width), `uppercase`,
`strike` (color), `underline` (color), `shadow`, `line_spacing`, `padding`,
`radius`. Designed text over six words triggers a warning.

### `image`

`file`, `w`/`h` (max box, default 0.6 × 0.7), `crop` `[x,y,w,h]` (pixels or
fractions), `card` (round corners + border + shadow), `radius`, `border`,
`shadow`, `grayscale`, `tint` (multiply color), plus `blocks` entry and
`kenburns` motion. Prepare screenshots with `helpers/web_shot.py card` for
rotation and tight crops.

### `video`

`file`, `source_start`, `fit` (`cover`/`contain`), `w`/`h` (omit both for
full-frame), `card` (rounded corners), `audio` (mix the clip's own sound into
the output), `audio_gain_db`. The clip must contain enough footage for the
element's visible span. Decoding happens through ffmpeg at the board's fps.

### `code`

`code` or `file`, `lang` (python, javascript, typescript, go, rust, c, bash,
json), `size` (34), `w` (fixed card width), `title` (tab text), `enter:
"type"` with `enter_duration` for a typewriter reveal.

### `emoji`

`text` (one or more emoji), `size` (height px), `font` override. Uses Apple
Color Emoji on macOS or Noto Color Emoji on Linux.

### `box` / `rect`

Outlined highlight rectangle (`w`, `h`, `color`, `width`, `radius`) or a
filled rectangle (`w`, `h`, `color`, `radius`).

## Outputs

- `<out>.mp4` — H.264 yuv420p video with AAC audio (narration + SFX + music +
  clip audio), or a silent AAC track if nothing was mixed.
- `<out>.timeline.json` — resolved times, fonts, element rectangles, SFX.
- `<out>.layout_manifest.json` — the measured critical frames that
  `helpers/layout_qc.py` validated (settled frame of every beat and every
  element entry).
- `--contact <png>` — one thumbnail per beat at its settled frame with the
  spoken words underneath; review it before rendering at full size.
- `--frame <t>` — full-resolution still(s) for close inspection.
- `--write-edl <edl.json>` — a ready version-2 EDL with the board as its only
  source; add `--subtitles master.ass` (built with `helpers/captions.py` from
  the same alignment) to declare burned captions with provenance.

## QC that the renderer enforces

- Word anchors must exist; unknown fonts, missing files, and clips that are
  too short fail before rendering. Text that uses characters the font cannot
  draw is reported as a warning (fix the copy; nothing is substituted).
- Beats cannot overlap; the board cannot be shorter than the narration.
- Every settled frame passes `layout_qc.validate_frame`: nothing leaves the
  canvas, and elements may not intersect unless declared with
  `allow_overlap_with`. With `captions_safe_bottom` set, nothing enters the
  caption rail. `--skip-layout-qc` exists for diagnosis only.

## Submission scope and output protection

Use this board helper when a script or supplied narration needs a sequence of
timed text, image cards, code and clips. Supplied audio and alignment are accepted;
Narration, Music and the main composition renderer are not runtime prerequisites.
Emoji elements use fetch_asset.py from the Assets helpers. Layout checks use
layout_qc.py from the illustration helpers. No fonts or source media are bundled
by this addition; select installed or supplied fonts and inspect the resulting layout.

The CLI requires fresh, distinct output paths for video, timeline, layout manifest,
contact sheets, frames and an optional EDL. BoardBuilder.write likewise requires a
new board.json. Use a new project/version directory for revisions. Output artifacts
are not published as an atomic set: failed rendering may leave partial artifacts.
Layout checks measure settled states and do not prove collision-free animation.
The built-in mono audio mix is not the independent sample-addressed mixer; inspect
speech intelligibility, loudness and transitions separately. Listening review remains
pending. The broader video-explainer workflow stays in the Workflows candidate.
