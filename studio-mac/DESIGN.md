# video-use Studio — design spec

A native macOS app (SwiftUI + AVFoundation) for reviewing and editing video-use EDLs.
Look and feel target: **Screen Studio** — near-black chrome, one indigo accent, chunky
rounded timeline blocks, Apple-grade polish.

The app contains **zero LLM calls**. The agent (Claude Code etc.) is the only
intelligence; the app and the agent communicate purely through files.

## File contract

Project = the directory containing `edl.json` (normally `<videos_dir>/edit/`).

Reads:
- `edl.json` — the cut (see SKILL.md "EDL format": sources, ranges w/ beat/quote/reason, grade, overlays, subtitles)
- `transcripts/*.json` — ElevenLabs Scribe word-level timestamps (`words[]: {text, start, end, speaker_id, type}`); keyed to sources by file stem
- `project.md` — displayed read-only in a drawer (session memory)

Writes:
- `edl.json` — atomic (write tmp + rename) on every committed human edit
- `edit_log.jsonl` — append one JSON object per edit:
  `{"ts": ISO8601, "op": "trim|move|delete|reorder|mute|speed|grade|subtitle_style", "segment": i, ...before/after}`
  This is what the agent reads (SKILL.md "Human edits") to learn taste. Never rewrite old lines.

Watches:
- `edl.json` (DispatchSource file watch). External change (agent) → reload,
  animate timeline to new state, flash "agent synced" indicator. If the user has uncommitted
  local drags, external changes queue behind a small "reload" toast instead of clobbering.

## Design tokens (from Screen Studio reference)

```
--bg-window:   #0a0a0b     window / outermost chrome
--bg-panel:    #161618     inspector, cards
--bg-elevated: #1e1e21     hover, inputs, secondary buttons
--border:      #2a2a2e     hairline borders (1px)
--text:        #f5f5f7
--text-dim:    #9b9ba3     secondary labels
--text-faint:  #5d5d66     tick labels, disabled
--accent:      #5b5bd6     indigo — primary buttons, playhead handle, effect blocks
--accent-soft: #5b5bd622   indigo tint fills
--clip:        #d99136     amber — cut-segment blocks (Screen Studio clip gold)
--clip-border: #f0b45e     selected clip outline
--danger:      #e5484d
radius: 10px blocks, 8px buttons, 14px panels; font: -apple-system/Inter, 13px base;
timeline block height 56px; spring animations (150–250ms cubic-bezier(.3,.7,.3,1)).
```

## Layout

```
┌────────────────────────────────────────────────────────────────────┐
│ TitleBar: (traffic-light inset) project name · ● agent synced      │
│           · undo/redo · [Export ⌘E indigo]                         │
├───────────────────────────────────────────────┬────────────────────┤
│ Canvas: centered 16:9 preview, rounded 12px,  │ Inspector (360px)  │
│ soft shadow, letterboxed on --bg-window       │  default: project  │
│ Floating pill controls above: grade preview   │  (grade preset,    │
│ toggle · subtitle preview toggle              │   subtitles path,  │
│                                               │   duration, source │
│ Transport: ⏮ ▶ ⏭  ·  00:12.4 / 01:27.4       │   list)            │
├───────────────────────────────────────────────┤  selected segment: │
│ Timeline (full width):                        │  "Slice editor"    │
│  ruler: output-time ticks + timestamps        │  ← Close · beat ·  │
│  playhead: 1px line + indigo circle handle    │  source · in/out · │
│  track 1 (amber): EDL ranges, contiguous,     │  quote (italic) ·  │
│    label "HOOK · 4.4s", drag L/R edges        │  agent's reason    │
│  track 2 (indigo): overlays, positioned at    │  (dim card) ·      │
│    start_in_output, width = duration          │  [Remove slice]    │
│  track 3 (thin): subtitles indicator strip    │                    │
└───────────────────────────────────────────────┴────────────────────┘
```

Timeline shows **output time** (like Screen Studio): segments are contiguous blocks;
source time appears only inside the slice editor.

## Core logic

**Virtual-cut playback via AVFoundation** (no rendering): the EDL's ranges become one
gapless `AVMutableComposition` (`Composition.swift`) — each source segment `insertTimeRange`'d
back-to-back onto shared video/audio tracks, handed to a single `AVPlayer`. Frame-accurate,
hardware-decoded, gapless, handles 4K. A per-segment `AVVideoComposition` applies each source's
orientation/scale so mixed-resolution / portrait sources render correctly. Output time ==
composition time, so `outputTime → segment via prefix sums → sourceTime = seg.start + (outputTime - segOffset)`
is used only for the timeline/inspector, not playback. Scrub = precise seek (zero tolerance) by
output time. Note: first playback of large 4K sources on slow/external storage may briefly buffer
before settling to real-time; warm playback is exactly 1×.

**Word snapping**: build per-source sorted word-boundary arrays from transcripts
(all `words[].start` and `.end`). Edge drags snap to the nearest boundary
(binary search), tooltip shows the snapped word and time:  `…wasted." ✂ 6.85s`.
If no transcript exists for a source, free drag (0.01s grid).

**Edits (v1)**: trim via edge drag (min 0.2s), delete segment, reorder via
drag-drop of whole blocks, select→inspector. Every commit: recompute
`total_duration_s`, atomic-write `edl.json`, append `edit_log.jsonl`.
Undo/redo = in-memory stack of EDL snapshots (also written through on undo).

**Export**: run `video-use render <edl> -o <edit_dir>/final.mp4 --build-subtitles`
(`--preview` variant for 720p). **`--build-subtitles` is always passed** so a trimmed cut
never reuses a stale `master.srt`; render regenerates it from transcripts + current offsets and
honors `subtitle_style` (`enabled:false` → no subtitles). Requires `video-use` on PATH.

The export sheet is a proper flow, not a raw log dump:
- **Running** — indeterminate indigo progress bar + a single status line naming the current
  pipeline stage (parsed from render stdout: Extracting segments → Concatenating clips →
  Building subtitles → Compositing overlays & subtitles → Normalizing loudness), a collapsed
  "Show log" disclosure, and Cancel.
- **Success** — a clean card: output filename, size, duration, [Reveal in Finder] · [Done]. No log.
- **Failure** — the error line prominent + the log expanded.
The `$ video-use render …` echo and `[exit 0]` are gone from the default view.

**Open project**: CLI arg (`video-use Studio.app --args <path-to-edl.json>`) or ⌘O file dialog.

## Live subtitles (generated in-app, not from master.srt)

Captions are generated live from `transcripts/*.json` + the current ranges — a faithful port of
render.py `build_master_srt` (`Subtitles.swift`), so they stay correct as slices are trimmed and
match the exported burn-in. Never read `master.srt` (it goes stale the moment a cut changes).

Algorithm per range: take transcript words of `type == "word"` overlapping `[start, end)`; group
into `chunk_words`-word chunks, breaking early when a word ends in `.,!?;:`; caption output time =
`word.start - range.start + range_output_offset`; collapse whitespace, strip trailing `,;:`, then
uppercase (if enabled). Drawn as a SwiftUI overlay on the preview (centered, white bold + black
outline), positioned by `margin_v`; `size`/`margin_v` are expressed in the export's 1080p space and
scaled to the on-screen video rect. Updates with the playhead (playing **and** scrubbing).

Style controls live in the project inspector panel (show/hide, uppercase, words-per-line 1/2/3,
size, bottom margin) and persist to a **top-level `subtitle_style`** object that render.py honors
at export (exact key names):

```json
"subtitle_style": {"enabled": true, "size": 18, "margin_v": 35, "uppercase": true, "chunk_words": 2}
```

Absent → loaded with those defaults. On change: atomic-write `edl.json` + append
`{"op": "subtitle_style", ...}` to `edit_log.jsonl` (slider drags commit once on release).

## Timeline zoom / pan

Default zoom fits the whole duration to width (no clipping). Pinch (or the +/−/fit controls)
zooms in; center-anchored. When zoomed in: **hover-edge panning** — the pointer within ~80px of
the left/right edge pans continuously, speed scaling with edge depth — and **auto-follow** keeps
the playhead in a comfortable band during playback/scrub.

## Inspector follows playback

While playing, the slice editor tracks the slice under the playhead (auto-selects as boundaries
cross). Manual clicks still select; scrubbing while paused also selects the slice under the playhead.

## Files pane (toggle in the title bar)

A left sidebar listing every video file in the videos dir (edit/'s parent; same discovery as the
pipeline: extensions `.mp4/.mov/.mkv/.avi/.m4v`, skip dotfiles/`._` AppleDouble). Per row: filename,
duration (`AVURLAsset` async, cached), and a full-width source-timeline strip with amber blocks
marking exactly which ranges the cut keeps (position/width proportional to source time). Unused
files are dimmed. Clicking a kept segment selects that slice and seeks the output playhead to it.

## Out of scope (v1)

Waveforms/filmstrips in the timeline (needs ffmpeg extraction cache — v2), reorder via drag-drop,
grade editing beyond preset dropdown, speed ramps, multi-EDL tabs, Windows/Linux titlebar styling.

## Remote control (agents drive the UI)

The app serves `127.0.0.1:4860` (Sources/Studio/ControlServer.swift):

```
GET  /state                  → {"edlPath", "slices", "selection", "playing", "playhead"}
POST /cmd '{"op": ...}'      → dispatched to the app on the main thread
```

Ops: `open{path}` · `toggle` / `play` / `pause` · `seek{t}` · `select{i}` (null clears)
· `undo` / `redo` · `reload` · `export{preview?}`.

Example:
```bash
curl -s -X POST localhost:4860/cmd -d '{"op":"open","path":"/path/to/edit/edl.json"}'
curl -s -X POST localhost:4860/cmd -d '{"op":"seek","t":12.4}'
curl -s localhost:4860/state
```

`studio <path/to/edl.json>` as a CLI arg opens that project at launch.
Combined with the file contract (agent writes edl.json → UI reloads live; human edits
→ edit_log.jsonl), an agent can propose a cut, play it for the user, jump to a specific
boundary, and re-render — fully hands-off.
