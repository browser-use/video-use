# Web-sourced visuals for original explainers

This is the procedure behind the `web_source.py` helper. The renderer-side
contract for compositions, protected regions, and the preflight image is in
`references/overlays.md`.

Use real footage only when it makes a narration beat clearer, more credible, or
more emotionally legible than animation. Do not force footage into every scene.

Write narration with natural **visual windows**: usually 4–8 seconds focused on
one concrete subject or action. Do not add empty pauses. Once narration duration
is locked, plan **4–6 meaningful visual refreshes per finished minute**. A
refresh may be excellent footage, a still, a new illustration state, or a
purposeful composition change. It is not a quota for web clips. A continuous
source moment reused in two layouts is still one refresh. Record each beat in
`<edit>/visual_plan.md` with output time, narration, visual intent, media type,
candidate URL + source time, layout, rights status, and review status.

Start from the narration idea, not from a pile of downloaded footage. Write a
script that naturally leaves room for concrete real-world visual windows, then
use an illustration engine for everything that does not benefit from footage.
Footage must earn its place by showing a real person whose identity matters, a
physical object, animal, place, demonstration, or source evidence that an
illustration cannot communicate as honestly. A muted person merely talking is
not useful B-roll unless identity, quotation, or authority is the point.

Use at most two or three source videos. When the plan keeps four or more web
beats, prefer at least two strong sources; using only one requires an explicit
editorial reason. Never add a weak second source only to satisfy diversity. If a
sourced beat fails relevance or rights review, replace it with an original
illustration rather than using weak evidence.

For each explainer:

1. Search concrete visual nouns/actions rather than the whole prompt:
   `python helpers/web_source.py search "<visual query>" --edit-dir <edit> --target-videos 2`.
   Set `--target-videos` to the intended final source count, from 1–3. The
   candidate manifest keeps that target while still listing backups.
2. Inspect candidates in order without downloading video:
   `python helpers/web_source.py inspect <url> --edit-dir <edit>`. The helper
   retrieves captions and writes a fast, deduplicated `transcript_packed.md`.
   **If captions are unavailable, skip immediately to the next candidate.** Do
   not spend time downloading or transcribing a source that failed this gate.
3. Read the packed transcript and choose only 2–3 candidate moments per video.
   The words narrow the search but do not prove what appears on screen.
4. Verify each chosen moment visually:
   `python helpers/web_source.py inspect <url> --edit-dir <edit> --start <s> --end <s>`.
   This downloads a low-resolution window and generates a filmstrip. Judge the
   actual start, middle, and end frames: keep the window, try the next
   transcript-derived moment, or skip the video. The helper enforces a maximum
   of three inspected windows per candidate.
5. Record the visual decision with `web_source.py select`. A kept beat must name
   its purpose, shot type, visible subject, visible action, and why footage is
   better than an illustration. Exact intervals receive a stable asset ID.
   Overlapping ranges and repeated visible descriptions warn. An exact reuse
   must use `--reuse-of <beat>` and later reference the original prepared file;
   never download or cut it again. Run `web_source.py summary` after selection;
   if four or more web beats legitimately use one source, pass
   `--one-source-reason "<editorial reason>"` so the exception is reviewable.
6. Present the strongest candidate with its URL, source interval, license or
   rights status, intended output placement, and a one-sentence editorial reason.
   Get approval before final acquisition and use.
7. Acquire the approved source with `web_source.py acquire`. Treat an unknown or
   standard platform license as reference-only unless the user has permission or
   another valid basis to use it. Preserve attribution for reusable licensed work.
8. Cut, crop, scale, and grade the selected interval with ffmpeg into an
   output-sized silent clip under `<edit>/overlays/`, then place that prepared file
   in the existing EDL `overlays` list. Keep narration audio primary. Set
   `media_kind: web` and retain `source_url`, `source_start`, `source_end`, and
   `asset_id` from the selection manifest.
9. Choose an explicit composition relationship:
   - `cutaway` intentionally replaces the teaching canvas;
   - `split_left` or `split_right` places footage on that side and the
     illustration in the companion region;
   - `picture_in_picture` uses `pip_left`, `pip_center`, `pip_right`, or a custom
     rectangle in genuine negative space.
   Use `fit: cover` for an intentional crop or `contain` when every source pixel
   matters. Do not place footage by coordinates alone without considering the
   illustration underneath it.
10. Declare each underlying illustration's occupied area and active time in EDL
   `protected_regions`. The renderer rejects any split or picture-in-picture
   overlay that collides with it. A cutaway may cover it intentionally.
11. Run the cheap insertion gate before the full render:
   `python helpers/render.py <edl> -o <edit>/overlay_preflight.png
   --preflight-overlays --preflight-base <base-video>`. Inspect entry, middle,
   and exit frames for each overlay; green marks footage, red marks protected
   illustration bounds, and amber marks the caption rail.
12. Render a preview and inspect the insertion at 1×. Check whether it teaches,
   whether its crop reads at delivery size, and whether entering and leaving the
   footage feels intentional. Ask for review before finalizing.

An original explainer requested from a topic alone is narrated by default. It
must contain audible human or generated speech unless the user explicitly asks
for a silent video. Never satisfy the narration requirement with a silent audio
file: measure the assembled track before loudness normalization and fail or
repair the edit when it has no finite loudness or no audible samples.

Narrated original explainers require captions. Generate timestamped narration,
convert the timestamps with `helpers/captions.py`, and reserve the bottom 16% of
the frame as `captions.safe_region`. Captions may not cover sourced footage or
Python/Manim illustrations. `render.py` rejects any new named/custom overlay
that intersects this rail. Read `skills/manim-video/references/concept-explainer.md`
before authoring a Manim scene: it removes redundant eyebrow text and provides
fit/overlap checks for labels and cards. Read `references/illustration-engines.md`
before choosing Penrose or CeTZ for an illustration slot.

The inverse is equally strict: if the finished audio contains no audible human
or generated voice, do not create captions, subtitle metadata, or a caption
rail. Keep headlines, labels, and calls to action inside the visual composition
as intentional typography.

If no candidate survives transcript, visual, or rights review, make the scene
animation-only. A clean illustration is better than irrelevant or risky footage.

Let the agent make the editorial choices. Do not add formulaic relevance scores,
a frame database, or a provider abstraction until real projects demonstrate a
need.
