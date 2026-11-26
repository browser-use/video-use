---
name: serious-ai-video-edit
description: Edit any video by conversation. Transcribe, cut, color grade, generate overlay animations, burn subtitles, for talking heads, montages, tutorials, travel, interviews. No presets, no menus. Ask questions, confirm the plan, execute, iterate, persist. Production-correctness rules are hard; everything else is artistic freedom.
---

# Serious AI Video Edit

## Principle

1. **LLM reasons from raw transcript + on-demand visuals.** The only derived artifact that earns its keep is a packed phrase-level transcript (`takes_packed.md`). Everything else (filler tagging, retake detection, shot classification, emphasis scoring) you derive at decision time.
2. **Audio is primary, visuals follow.** Cut candidates come from speech boundaries and silence gaps. Drill into visuals only at decision points.
3. **Ask → confirm → execute → iterate → persist.** Never touch the cut until the user has confirmed the strategy in plain English.
4. **Generalize.** Do not assume what kind of video this is. Look at the material, ask the user, then edit.
5. **Artistic freedom is the default.** Every specific value, preset, font, color, duration, pitch structure, and technique in this document is a *worked example* from one proven video, not a mandate. Read them to understand what's possible and why each worked. Then make your own taste calls based on what the material actually is and what the user actually wants. **The only things you MUST do are in the Hard Rules section below.** Everything else is yours.
6. **Invent freely.** If the material calls for a technique not described here (split-screen, picture-in-picture, lower-third identity cards, reaction cuts, speed ramps, freeze frames, crossfades, match cuts, L-cuts, J-cuts, speed ramps over breath, whatever) build it. The helpers are ffmpeg and PIL. They can do anything the format supports. Do not wait for permission.
7. **Verify your own output before showing it to the user.** If you wouldn't ship it, don't present it.

## Hard Rules (production correctness, non-negotiable)

These are the things where deviation produces silent failures or broken output. They are not taste, they are correctness. Memorize them.

1. **Subtitles are applied LAST in the filter chain**, after every overlay. Otherwise overlays hide captions. Silent failure.
2. **Per-segment video extract → lossless `-c copy` concat**, not single-pass filtergraph. Otherwise you double-encode every segment when overlays are added. **Audio is never per-segment.** `render.py` cuts it once from the source into one continuous track, each range starting at its `-ss` point and lasting the segment's real frame count divided by the output rate, and encodes it to AAC once at the end, as ADTS with the 1024-sample encoder lead-in trimmed, so the file carries no priming edit list and Spotify and YouTube take it as is. Per-segment AAC files joined with `-c copy` drifted the audio off the picture by 13 ms a join (Ep 333, November 11, 2026).
3. **Audio fades at every segment boundary**, 10 ms in and out inside that continuous track (`afade=t=in:st=0:d=0.01,afade=t=out:st={dur-0.01}:d=0.01`). Otherwise audible pops at every cut.
4. **Overlays use `setpts=PTS-STARTPTS+T/TB`** to shift the overlay's frame 0 to its window start. Otherwise you see the middle of the animation during the overlay window.
5. **Master SRT uses output-timeline offsets**: `output_time = word.start - segment_start + segment_offset`. Otherwise captions misalign after segment concat.
6. **Never cut inside a word.** Snap every cut edge to a word boundary from the transcript (Scribe or local Whisper).
7. **Pad every cut edge.** Working window: 30 to 200ms. ASR timestamps drift 50 to 100ms, padding absorbs the drift. Tighter for fast-paced, looser for cinematic.
8. **Word-level verbatim ASR only.** Never SRT/phrase mode (loses sub-second gap data). Never normalized fillers (loses editorial signal).
9. **Cache transcripts per source.** Never re-transcribe unless the source file itself changed.
10. **Parallel sub-agents for multiple animations.** Never sequential. Spawn N at once via the `Agent` tool; total wall time ≈ slowest one.
11. **Strategy confirmation before execution.** Never touch the cut until the user has approved the plain-English plan.
12. **All session outputs in `<videos_dir>/edit/`.** Never write inside the `serious-ai-video-edit/` project directory.

Everything else in this document is a worked example. Deviate whenever the material calls for it.

## Directory layout

The skill lives in `serious-ai-video-edit/`. User footage lives wherever they put it. All session outputs go into `<videos_dir>/edit/`.

```
<videos_dir>/
├── <source files, untouched>
└── edit/
    ├── project.md               ← memory; appended every session
    ├── takes_packed.md          ← phrase-level transcripts, the LLM's primary reading view
    ├── edl.json                 ← cut decisions
    ├── transcripts/<name>.json  ← cached raw ASR JSON (Scribe or local Whisper)
    ├── animations/slot_<id>/    ← per-animation source + render + reasoning
    ├── clips_graded/            ← per-segment extracts with grade + fades
    ├── master.srt               ← output-timeline subtitles
    ├── downloads/               ← yt-dlp outputs
    ├── verify/                  ← debug frames / timeline PNGs
    ├── preview.mp4
    └── final.mp4
```

## Setup

First-time install lives in `install.md` (clone, deps, ffmpeg, skill registration, API key). Don't re-run it every session; on cold start just verify:

- **Local fork note:** `transcribe.py`/`transcribe_batch.py` default to `--engine local` (faster-whisper, on-device, free, `--model small` by default). `ELEVENLABS_API_KEY` is only needed for `--engine scribe`, which trades cost for diarization + audio-event tags. Don't ask for the key unless the user asks for Scribe specifically.
- `ffmpeg` + `ffprobe` on PATH.
- Python deps installed (`uv sync` or `pip install -e .` inside the repo).
- Node.js + npm available if the session needs HyperFrames or Remotion slots. HyperFrames currently requires Node.js 22+.
- `yt-dlp`, HyperFrames, Remotion, Manim installed only on first use.
- First-use animation setup happens inside the slot directory, never at the serious-ai-video-edit repo root. HyperFrames can be invoked with `npx --yes hyperframes ...`; Remotion can be scaffolded with `npx create-video@latest` or installed as a project-local dependency before using its `remotion render` command.
- This skill vendors `skills/manim-video/`. Read its SKILL.md when building a Manim slot.
- **`drawtext` needs the full ffmpeg build.** Homebrew's default `ffmpeg` formula ships without `drawtext` support; install the full-featured build (`ffmpeg-full` or equivalent for your platform) before attempting burned-in clocks, timers, or captions via `drawtext`.
- **`boxblur` has hard radius caps** (luma 14, chroma lower). A larger value fails silently rather than erroring, clamp explicitly rather than guessing a number that "should" work.
- **Font paths with spaces can break `drawtext`'s filter-string parsing** (a Google Drive path is a common culprit). Copy the font file to a local scratch path without spaces if `drawtext` misbehaves for no obvious reason.

Helpers (`helpers/transcribe.py`, `helpers/render.py`, etc.) live alongside this SKILL.md. Resolve their paths relative to the directory containing this file. The skill is typically symlinked at `~/.claude/skills/serious-ai-video-edit/` or `~/.codex/skills/serious-ai-video-edit/`.

## Helpers

- **`transcribe.py <video>`** runs a single-file transcription (local Whisper by default, or Scribe). `--num-speakers N` optional. Cached. `--vocab "Name One, Name Two"` primes local Whisper with proper nouns so brand and product names are spelled right at the source.
- **`transcribe_batch.py <videos_dir>`**: 4-worker parallel transcription. Use for multi-take.
- **`pack_transcripts.py --edit-dir <dir>`**: `transcripts/*.json` → `takes_packed.md` (phrase-level, break on silence ≥ 0.5s).
- **`timeline_view.py <video> <start> <end>`** (filmstrip + waveform PNG. On-demand visual drill-down. **Not a scan tool**) use it at decision points, not constantly.
- **`render.py <edl.json> -o <out>`**: per-segment extract → concat → overlays (PTS-shifted) → subtitles LAST. `--preview` for 720p fast. `--build-subtitles` to generate master.srt inline. `--native` keeps the source resolution (4K stays 4K, VideoToolbox H.264 at 45 Mbps); the default scales to 1080p.
- **`grade.py <in> -o <out>`**: ffmpeg filter chain grade. Presets + `--filter '<raw>'` for custom.
- **`broll/`**: the B-roll kit for canvas scene pages that seek to time t. `broll-kit.js` (camera push, padded window frame, line-by-line typing of a capture, cursor, spotlight, highlighter, seeded rough.js sketch strokes), `render.js` (frames to mp4, or stills), `texts.js` (what each frame draws, for the lint), `capture.js` (a claude-replay page to a 2x still plus line boxes). Usage in `references/broll-style.md`.
- **`session_capture.py <spec.json> <out_dir>`**: a real Claude Code session cut to allowlisted verbatim excerpts, privacy-scanned, rendered offline with claude-replay (MIT, pinned), captured at 2x.
- **`session_scan.py <files>`**: the privacy scan on its own (emails, paths, private links, phones, patient identifiers, keys, a private denylist of client names).
- **`broll_lint.py <texts.json>`**: rules B1 to B5 from the feedback ledger.

For animations, create `<edit>/animations/slot_<id>/` with `Bash` and spawn a sub-agent via the `Agent` tool.

## The process

1. **Inventory.** `ffprobe` every source. `transcribe_batch.py` on the directory. `pack_transcripts.py` to produce `takes_packed.md`. Sample one or two `timeline_view`s for a visual first impression.
2. **Pre-scan for problems.** One pass over `takes_packed.md` to note verbal slips, obvious mis-speaks, or phrasings to avoid. Plain list, feed into the editor brief. Default assumption: every filler word ("um," "uh") and false start on that list gets cut. Don't ask permission for this, it's the default; only preserve one if the user has asked for a natural or unedited feel.
3. **Converse.** Describe what you see in plain English. Ask questions *shaped by the material*. Collect: content type, target length/aspect, aesthetic/brand direction, pacing feel, must-preserve moments, must-cut moments, animation and grade preferences, subtitle needs. Do not use a fixed checklist, the right questions are different every time.
4. **Propose strategy.** 4 to 8 sentences: shape, take choices, cut direction, animation plan, grade direction, subtitle style, length estimate. **Wait for confirmation.**
5. **Execute.** Produce `edl.json` via the editor sub-agent brief. Drill into `timeline_view` at ambiguous moments. Build animations in parallel sub-agents. Apply grade per-segment. Compose via `render.py`.
6. **Preview.** `render.py --preview`.
7. **Self-eval (before showing the user).** Run `timeline_view` on the **rendered output** (not the sources) at every cut boundary (±1.5s window). Check each image for:
   - Visual discontinuity / flash / jump at the cut
   - Waveform spike at the boundary (audio pop that slipped past the edge fade)
   - Subtitle hidden behind an overlay (Rule 1 violation)
   - Overlay misaligned or showing wrong frames (Rule 4 violation)

   Also sample: first 2s, last 2s, and 2 to 3 mid-points, check grade consistency, subtitle readability, overall coherence. Run `ffprobe` on the output to verify duration matches the EDL expectation.

   If anything fails: fix → re-render → re-eval. **Cap at 3 self-eval passes**: if issues remain after 3, flag them to the user rather than looping forever. Only present the preview once the self-eval passes.
8. **Iterate + persist.** Natural-language feedback, re-plan, re-render. Never re-transcribe. Final render on confirmation. Append to `project.md`.

## Cut craft (techniques)

- **Cut filler words and false starts aggressively, by default.** This is the tool's core job, not an optional style. Verbatim transcription exists precisely so every "um," "uh," and false start has an accurate word boundary to cut at, don't leave them in the final render unless the user explicitly asked for a natural or unedited feel. If they do ask for that, dial back the aggressiveness in conversation for that project; it's an editorial choice made per-project, not a transcription setting.
- **Audio-first.** Candidate cuts from word boundaries and silence gaps.
- **Preserve peaks.** Laughs, punchlines, emphasis beats. Extend past punchlines to include reactions, the laugh IS the beat.
- **Speaker handoffs** benefit from air between utterances. Common values: 400 to 600ms. Less for fast-paced, more for cinematic. Taste call.
- **Audio events as signals.** `(laughs)`, `(sighs)`, `(applause)` mark beats. Extend past them.
- **Silence gaps are cut candidates.** Silences ≥400ms are usually the cleanest. 150 to 400ms phrase boundaries are usable with a visual check. <150ms is unsafe (mid-phrase).
- **Example cut padding** (the launch video shipped with this): 50ms before the first kept word, 80ms after the last. Tighter for montage energy, looser for documentary. Stay in the 30 to 200ms working window (Hard Rule 7).
- **Never reason audio and video independently.** Every cut must work on both tracks.

## Long-form sources & verification

Hard-won lessons from editing long single-take sources (screen recordings, demos, interviews over ~20 minutes) where a mistake costs a full re-render cycle, not a quick fix. These are the class of bug that looks fine in isolation and only shows up when you check the actual output.

- **Validate source continuity before building anything on top of it.** If the source has a visible ground-truth timer (a screen-recorder's own timestamp, a stopwatch, another app's clock), sample it at several points across the file and confirm the offset between video position and displayed time stays constant. A growing offset means the file was auto-edited (fillers or silences already stripped) and isn't wall-clock-continuous, any burned clock or cut point built on it will be wrong no matter how carefully applied. A small constant residual (1-2s) is normal; a growing one means stop and get a truly continuous source before proceeding.
- **Long transcripts drift.** Transcribing a 20+ minute file in one pass can drift progressively (observed case: an error growing from ~11s to over 20s across a 60-minute file). When deriving cut points on a long single-take source, re-transcribe in ~5-minute chunks with a known, added offset per chunk so drift can't accumulate.
- **Silence isn't punctuation.** Don't trust a coarse silence-gap threshold as a word or sentence boundary, a half-second breath can get treated as a full pause and clip into real speech. When a specific cut needs precision, re-run silence detection at a finer threshold within a short window around the target to find the true inter-word gap.
- **Verify every cut boundary against the rendered output, not just the source transcript.** Rendering and re-encoding can introduce their own drift independent of the transcript's. Transcribe the actual rendered segment to confirm it starts and ends on the intended words before calling a cut done. `helpers/seam_check.py listen <edl.json> <render.mp4>` does this for every seam at once: it transcribes 8 s around each cut and prints the join, so a clipped word shows up as a missing or changed word next to the `|`. Run it on a `--draft` render before the full one. `helpers/seam_check.py sync <edl.json> <render.mp4> [source]` measures audio against picture at about 7 points and checks Spotify's packaging, and exits non-zero if the offset moves by more than 40 ms or the packaging fails. Run it on every master before upload.
- **A burned clock or timer must support a freeze landing mid-segment**, not only at segment boundaries, otherwise it can run straight through the true freeze point and then snap backward at the next segment cut: a visible, credibility-destroying bug. Verify by sampling the displayed value densely across the transition and confirming it never decreases.
- **Check the whole cut timeline for contiguity**, not just individual boundaries: every kept or ramped segment should start exactly where the previous one ended in source time. This single check catches dropped material and clock/pacing bugs together, worth running as a standing step on any multi-segment edit.
- **Diagnose "missing audio" by transcribing the assembled file, never by ear-scrubbing a sped-up preview.** Fast-scrub playback in common players (QuickTime included) can silently suppress audio, making a fully-covered section sound silent when it isn't.
- **A looped still image needs an end.** `-loop 1 -i card.png` into `overlay` runs until something stops it. A short `-t` with `eof_action=pass` or an `enable=between(...)` window stalled `ffmpeg-full` 8 at 0% CPU with no moov atom, and a looped mask with no `-t` repeated the last frame for 1:41 past the end of a 33-minute video (short `-t` tests hid both). For a timed card, use `-loop 1 -framerate <fps>`, fade the card's alpha in and out, and close with `overlay=shortest=1`. Any other looped input gets a `-t` equal to the full output length.
- **Bound every long render.** Pass `-progress <file>` so a stall shows in seconds, wrap the job in a hard limit (`perl -e 'alarm N; exec @ARGV' ffmpeg ...`), and look at it at about 1.5x its estimate rather than waiting on a completion notice. A job past its estimate is a bug until shown otherwise.
- **The transcript cache keys on the file name.** Re-transcribing a re-rendered file with the same name (`base_audio.wav`) silently returns the old words, so delete its JSON in `transcripts/` first.
- **Whisper `medium` drops fillers** ("you know", "um") and word edges drift about 0.3 s on Zoom audio. To verify cuts, run `large-v3` on the kept ranges, transcribe the rendered output, and find true word gaps from 10 ms RMS energy (numpy is in the skill's `.venv`).
- **Redact PII with wide, always-on bands, not fixed-position boxes**, on any screen recording where a sidebar or panel can scroll or move. A position-tracked or static box lets sensitive content (owner names, addresses, anything personally identifying, not just the presenter's own) drift in and out of frame uncovered.

## The packed transcript (primary reading view)

`pack_transcripts.py` reads all `transcripts/*.json` and produces one markdown file where each take is a list of phrase-level lines, each prefixed with its `[start-end]` time range. Phrases break on any silence ≥ 0.5s OR speaker change. This is the artifact the editor sub-agent reads to pick cuts, it gives word-boundary precision from text alone at 1/10 the tokens of raw JSON.

Example line:
```
## C0103  (duration: 43.0s, 8 phrases)
  [002.52-005.36] S0 Ninety percent of what a web agent does is completely wasted.
  [006.08-006.74] S0 We fixed this.
```

## Editor sub-agent brief (for multi-take selection)

When the task is "pick the best take of each beat across many clips," spawn a dedicated sub-agent with a brief shaped like this. The structure is load-bearing; the pitch-shape example is not.

```
You are editing a <type> video. Pick the best take of each beat and 
assemble them chronologically by beat, not by source clip order.

INPUTS:
  - takes_packed.md (time-annotated phrase-level transcripts of all takes)
  - Product/narrative context: <2 sentences from the user>
  - Speaker(s): <name, role, delivery style note>
  - Expected structure: <pick an archetype or invent one>
  - Verbal slips to avoid: <list from the pre-scan pass>
  - Target runtime: <seconds>

Common structural archetypes (pick, adapt, or invent):
  - Tech launch / demo:   HOOK → PROBLEM → SOLUTION → BENEFIT → EXAMPLE → CTA
  - Tutorial:             INTRO → SETUP → STEPS → GOTCHAS → RECAP
  - Interview:            (QUESTION → ANSWER → FOLLOWUP) repeat
  - Travel / event:       ARRIVAL → HIGHLIGHTS → QUIET MOMENTS → DEPARTURE
  - Documentary:          THESIS → EVIDENCE → COUNTERPOINT → CONCLUSION
  - Music / performance:  INTRO → VERSE → CHORUS → BRIDGE → OUTRO
  - Or invent your own.

RULES:
  - Start/end times must fall on word boundaries from the transcript.
  - Pad cut boundaries (working window 30 to 200ms).
  - Prefer silences ≥ 400ms as cut targets.
  - Cut every filler word ("um," "uh") and false start by default, unless the user asked for a natural or unedited feel for this project.
  - Unavoidable slips are kept if no better take exists. Note them in "reason".
  - If over budget, revise: drop a beat or trim tails. Report total and self-correct.

OUTPUT (JSON array, no prose):
  [{"source": "C0103", "start": 2.42, "end": 6.85, "beat": "HOOK",
    "quote": "...", "reason": "..."}, ...]

Return the final EDL and a one-line total runtime check.
```

## Color grade (when requested)

Your job is to **reason about the image**, not apply a preset. Look at a frame (via `timeline_view`), decide what's wrong, adjust one thing, look again.

Mental model is ASC CDL. Per channel: `out = (in * slope + offset) ** power`, then global saturation. `slope` → highlights, `offset` → shadows, `power` → midtones.

**Example filter chains** (`grade.py` has `--list-presets`; use them as starting points or mix your own):

- **`warm_cinematic`**: retro/technical, subtle teal/orange split, desaturated. Shipped in a real launch video. Safe for talking heads.
- **`neutral_punch`**: minimal corrective: contrast bump + gentle S-curve. No hue shifts.
- **`none`**: straight copy. Default when the user hasn't asked.

For anything else (portraiture, nature, product, music video, documentary) invent your own chain. `grade.py --filter '<raw ffmpeg>'` accepts any filter string.

Hard rules: apply **per-segment during extraction** (not post-concat, which re-encodes twice). Never go aggressive without testing skin tones.

## Subtitles (when requested)

Subtitles have three dimensions worth reasoning about: **chunking** (1/2/3/sentence per line), **case** (UPPER/Title/Natural), and **placement** (margin from bottom). The right combo depends on content.

**Worked styles**: pick, adapt, or invent:

**`bold-overlay`**: short-form tech launch, fast-paced social. 2-word chunks, UPPERCASE, break on punctuation, Helvetica 18 Bold, white-on-outline, `MarginV=35`. `render.py` ships with this as `SUB_FORCE_STYLE`.

```
FontName=Helvetica,FontSize=18,Bold=1,
PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,
BorderStyle=1,Outline=2,Shadow=0,
Alignment=2,MarginV=35
```

**`natural-sentence`** (if you invent this mode) (narrative, documentary, education. 4 to 7 word chunks, sentence case, break on natural pauses, `MarginV` of 60 to 80, larger font for readability, slightly wider max-width. No shipped force_style) design one if you need it.

Invent a third style if neither fits. Hard rules: subtitles LAST (Rule 1), output-timeline offsets (Rule 5).

## Animations (when requested)

Animations match the content and the brand. **For Serious AI podcast B-roll, read `references/broll-style.md` first**: the house style, the teardown it comes from, the shared kit in `helpers/broll/`, session capture, and the feedback ledger. For any other brand, take the palette and type from the conversation, or propose one in the strategy phase and wait for confirmation.

**Before a B-roll track renders**, run `node helpers/broll/texts.js scenes.html texts.json` and `python3 helpers/broll_lint.py texts.json --transcript <transcript.json> --page scenes.html`, and fix every hit. Each rule (B1 to B5) is a piece of past feedback that a check now holds.

**Real work on screen** (a Claude Code session Chris mentions on air) comes from `helpers/session_capture.py`, never from a screen recording: it keeps only allowlisted, verbatim excerpts and stops on any privacy hit. No PHI or client data reaches a frame.

**Tool options:**

Pick the engine per animation slot. Do not default to Remotion just because the animation is web-adjacent.

- **HyperFrames**: Browser-native HTML/CSS/GSAP video compositions: product UI motion, website-to-video or mockup-to-video captures, kinetic typography, landing-page/storyboard promos, data-driven UI states, transparent WebM overlays, and clips that need deterministic frame capture plus HyperFrames lint/validate/render checks. Best when the animation should be authored and verified like a web composition instead of a React component tree.
- **Remotion**: React/CSS compositions with component state, reusable React primitives, or an existing Remotion brand system. Best when the user specifically asks for React/Remotion or when React composition is the simpler authoring model.
- **Manim**: formal diagrams, state machines, equation derivations, graph morphs. Read `skills/manim-video/SKILL.md` and its references for depth.
- **PIL + PNG sequence + ffmpeg**: simple overlay cards: counters, typewriter text, single bar reveals, progressive draws. Fast to iterate, any aesthetic you want. The launch video used this.

For HyperFrames slots, scaffold the slot inside `edit/animations/slot_<id>/` with `npx --yes hyperframes init . --example blank --non-interactive --skip-skills`, build the HTML composition there, run the HyperFrames checks that fit the slot (`lint`, `validate`, and a draft render when practical), then produce the final overlay video with `npx --yes hyperframes render . -o render.mp4` or `--format webm -o render.webm` when alpha is required. Point the EDL overlay `file` at the actual rendered path.

For Remotion slots, keep the Remotion project isolated inside the same slot directory, scaffold with `npx create-video@latest` or install Remotion locally there, render the composition to `render.mp4` with the project-local `remotion render` command, and verify duration and dimensions with `ffprobe`.

None is mandatory. Invent hybrids if useful (e.g., PIL background with a HyperFrames or Remotion layer on top).

**Duration rules of thumb, context-dependent:**

- **Sync-to-narration explanations.** A viewer needs to parse the content at 1×. Rough floor 3s, typical 5 to 7s for simple cards, 8 to 14s for complex diagrams. The launch video shipped at 5 to 7s per simple card.
- **Beat-synced accents** (music video, fast montage). 0.5 to 2s is fine, they're visual accents, not information. The "readable at 1×" rule becomes *"recognizable at 1×,"* not *"fully parseable."*
- **Hold the final frame ≥ 1s** before the cut (universal).
- **Over voiceover:** total duration ≥ `narration_length + 1s` (universal).
- **Never parallel-reveal independent elements**: the eye can't track two new things at once. One thing, pause, next thing.

**Animation payoff timing (rule for sync-to-narration):** get the payoff word's timestamp. Start the overlay `reveal_duration` seconds earlier so the landing frame coincides with the spoken payoff word. Without this sync the animation feels disconnected.

**Easing** (universal, never `linear`, it looks robotic):

```python
def ease_out_cubic(t):    return 1 - (1 - t) ** 3
def ease_in_out_cubic(t):
    if t < 0.5: return 4 * t ** 3
    return 1 - (-2 * t + 2) ** 3 / 2
```

`ease_out_cubic` for single reveals (slow landing). `ease_in_out_cubic` for continuous draws.

**Typing text anchor trick:** center on the FULL string's width, not the partial-string width, otherwise text slides left during reveal.

**Parallel sub-agent brief**: each animation is one sub-agent spawned via the `Agent` tool. Each prompt is self-contained (sub-agents have no parent context). Include:

1. One-sentence goal: *"Build ONE animation: [spec]. Nothing else."*
2. Absolute output path (`<edit>/animations/slot_<id>/render.mp4`)
3. Exact technical spec: resolution, fps, codec, pix_fmt, CRF, duration
4. Style palette as concrete values (RGB tuples, hex, or reference to a design system)
5. Font path with index
6. Frame-by-frame timeline (what happens when, with easing)
7. Anti-list ("no chrome, no extras, no titles unless specified")
8. Code pattern reference (copy helpers inline, don't import across slots)
9. Deliverable checklist (script, render, verify duration via ffprobe, report)
10. **"Do not ask questions. If anything is ambiguous, pick the most obvious interpretation and proceed."**

One sub-agent = one file (unique filenames, parallel agents don't overwrite each other).

## Output spec

Match the source unless the user asked for something specific. Common targets: `1920×1080@24` cinematic, `1920×1080@30` screen content, `1080×1920@30` vertical social, `3840×2160@24` 4K cinema, `1080×1080@30` square. `render.py` defaults the scale to 1080p from any source; pass `--filter` or edit the extract command for other targets. Worth asking the user which delivery format matters.

## EDL format

```json
{
  "version": 1,
  "sources": {"C0103": "/abs/path/C0103.MP4", "C0108": "/abs/path/C0108.MP4"},
  "ranges": [
    {"source": "C0103", "start": 2.42, "end": 6.85,
     "beat": "HOOK", "quote": "...", "reason": "Cleanest delivery, stops before slip at 38.46."},
    {"source": "C0108", "start": 14.30, "end": 28.90,
     "beat": "SOLUTION", "quote": "...", "reason": "Only take without the false start."}
  ],
  "grade": "warm_cinematic",
  "overlays": [
    {"file": "edit/animations/slot_1/render.mp4", "start_in_output": 0.0, "duration": 5.0}
  ],
  "subtitles": "edit/master.srt",
  "total_duration_s": 87.4
}
```

`grade` is a preset name or raw ffmpeg filter. `overlays` are rendered animation clips. `subtitles` is optional and applied LAST.

A range may also carry `vf`, an ffmpeg filter applied to that range only, before any scaling, so its coordinates are source pixels. Its `t` is relative to the range's own `start`. Use it for a timed privacy blur on a screen recording, for example `"vf": "split[a][b];[b]crop=1640:210:1000:1220,scale=136:18,boxblur=2:1,scale=1640:210[c];[a][c]overlay=1000:1220:enable='between(t,262.2,518.7)'"`. Sizes passed to `scale` must be even for yuv420p.

## Publishing (when the user is about to upload)

- **A fresh YouTube upload often defaults to 360p (Auto) for a day or more**, even once 1080p exists, because YouTube's transcode ladder finishes low resolutions first and browsers' Auto-quality picker prefers whatever's ready. Don't re-upload to "fix" this, it restarts transcoding and issues a new video ID, breaking any embed already wired to the old one. It resolves on its own; tell the user viewers can select 1080p manually from the gear icon in the meantime.
- **Embedded player width affects auto-quality too.** A narrow embed (under roughly 1000px rendered width) gives YouTube a legitimate reason to serve 720p or lower even once the file's fully processed. Widen the embed container if this matters for the delivery.

## Memory, `project.md`

Append one section per session at `<edit>/project.md`:

```markdown
## Session N, YYYY-MM-DD

**Strategy:** one paragraph describing the approach
**Decisions:** take choices, cuts, grades, animations + why
**Reasoning log:** one-line rationale for non-obvious decisions
**Outstanding:** deferred items
```

On startup, read `project.md` if it exists and summarize the last session in one sentence before asking whether to continue.

## Anti-patterns

Things that consistently fail regardless of style:

- **Hierarchical pre-computed codec formats** with USABILITY / tone tags / shot layers. Over-engineering. Derive from the transcript at decision time.
- **Hand-tuned moment-scoring functions.** The LLM picks better than any heuristic you'll write.
- **Whisper SRT / phrase-level output.** Loses sub-second gap data. Always word-level verbatim.
- **Trusting local Whisper as fully verbatim.** This fork's `--engine local` primes it with a verbatim-style `initial_prompt` (see `call_whisper_local` in `helpers/transcribe.py`), which recovers most um/uh/false-starts but not all (measured ~75% recovery in testing, not 100%). For a project that leans hard on filler-word cutting, spot-check `takes_packed.md` against the source audio, or use `--engine scribe` instead.
- **Burning subtitles into base before compositing overlays.** Overlays hide them. (Hard Rule 1.)
- **Single-pass filtergraph when you have overlays.** Double re-encodes. Use per-segment extract → concat.
- **Linear animation easing.** Looks robotic. Always cubic.
- **Hard audio cuts at segment boundaries.** Audible pops. (Hard Rule 3.)
- **Typing text centered on the partial string.** Text slides left as it grows.
- **Sequential sub-agents for multiple animations.** Always parallel.
- **Editing before confirming the strategy.** Never.
- **Re-transcribing cached sources.** Immutable outputs of immutable inputs.
- **Assuming what kind of video it is.** Look first, ask second, edit last.
- **Trusting a silence gap as a word or sentence boundary at a coarse threshold.** Verify against the raw audio at fine resolution when a specific cut needs precision.
- **Trusting a derived signal (transcript timestamp, segment start time) over ground truth.** Re-verify against the rendered output or raw source before shipping a cut, clock, or overlay.
- **Dropping footage to shorten a cut instead of speeding through it**, especially proof-heavy or evidentiary material. If it needs to be shorter, ramp the speed up; don't remove it outright.
- **Building a burned clock or overlay on an unverified source.** Confirm the source is wall-clock-continuous (constant offset from any visible ground-truth timer) before deriving anything from its timeline.
