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

**Virtual-cut playback** (no rendering): one `<video>` element per source file
(created once, `convertFileSrc(abs_path)`), absolutely stacked; only the active
segment's video is visible. An rAF loop drives an output-time clock:
`outputTime → find segment via prefix sums → sourceTime = seg.start + (outputTime - segOffset)`.
On segment entry: show that source's element, `currentTime = sourceTime`, `play()`.
Pre-seek the *next* segment's element 300ms before the boundary for a clean handoff
(two elements on the same source: keep one primary; small gap/jump is acceptable v1).
Pause at end. Scrub = seek by output time.

**Word snapping**: build per-source sorted word-boundary arrays from transcripts
(all `words[].start` and `.end`). Edge drags snap to the nearest boundary
(binary search), tooltip shows the snapped word and time:  `…wasted." ✂ 6.85s`.
If no transcript exists for a source, free drag (0.01s grid).

**Edits (v1)**: trim via edge drag (min 0.2s), delete segment, reorder via
drag-drop of whole blocks, select→inspector. Every commit: recompute
`total_duration_s`, atomic-write `edl.json`, append `edit_log.jsonl`.
Undo/redo = in-memory stack of EDL snapshots (also written through on undo).

**Export**: shell-execute `video-use render <edl> -o <edit_dir>/final.mp4`
(plugin-shell, already permitted); stream stdout lines into a progress overlay;
`--preview` variant behind alt-click. Requires `video-use` on PATH — show a hint
if spawn fails.

**Open project**: CLI arg (`studio <path-to-edl.json>`), file-open dialog, or
drag-drop. Remember last project in localStorage.

## Out of scope (v1)

Waveforms/filmstrips in the timeline (needs ffmpeg extraction cache — v2),
subtitle style editing, grade editing beyond preset dropdown, speed ramps,
multi-EDL tabs, Windows/Linux titlebar styling.

## Remote control (agents drive the UI)

The app serves `127.0.0.1:4860` (Sources/Studio/ControlServer.swift):

```
GET  /state                  → {"edlPath", "slices", "selection", "playing", "playhead"}
POST /cmd '{"op": ...}'      → forwarded to the webview
```

Ops: `open{path}` · `toggle` / `play` / `pause` · `seek{t}` · `select{i}` (null clears)
· `undo` / `redo` · `reload` · `export{preview?}`.

Example:
```bash
curl -s -X POST localhost:4859/cmd -d '{"op":"open","path":"/path/to/edit/edl.json"}'
curl -s -X POST localhost:4859/cmd -d '{"op":"seek","t":12.4}'
curl -s localhost:4859/state
```

`studio <path/to/edl.json>` as a CLI arg opens that project at launch.
Combined with the file contract (agent writes edl.json → UI reloads live; human edits
→ edit_log.jsonl), an agent can propose a cut, play it for the user, jump to a specific
boundary, and re-render — fully hands-off.
