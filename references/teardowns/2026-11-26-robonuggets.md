# RoboNuggets (Jay E) B-roll teardown

Built from frames only: 28 contact sheets (one frame per 4 s), ~20 bursts at 2 to 6 fps, five native 30 fps bursts, and full-resolution stills from 720p downloads (November 26, 2026 · the working files were scratch and are not kept). The video files carry no audio, so nothing here claims sync to speech, music or sound effects.

| ID | Title (short) | Upload | Length |
|---|---|---|---|
| OMfU5iq8Eik (n93) | 7 MASSIVE Claude Features | Nov 22 2026 | 15:23 |
| IMSYKloMFrE (n94) | Engineers Stopped Building AI Agents | Nov 23 2026 | 16:33 |
| nT_oMAIZ3P4 (n95) | NEW Rules of Claude Code | Nov 24 2026 | 11:49 |

Timestamps below are `videoID m:ss`, read from the burned-in sheet labels or burst labels.

## Your five "at a glance" observations, checked

1. **"Webcam in a small rounded rectangle bottom-left, always on during B-roll": mostly right.**
   - Confirmed: it is a fixed portrait card, bottom-left, on screen in about 90% of sampled frames.
   - Correction: it drops out on the chapter-grid cards that open each chapter (OMfU5iq8Eik 1:07.5 to 1:10.5) and on some third-party clips and slides.
2. **"B-roll is mostly real screen recordings of the Claude app on a dark background": partly right.**
   - Claude UI captures dominate OMfU5iq8Eik.
   - IMSYKloMFrE, and nT_oMAIZ3P4 even more so, lean on illustrated scenes (isometric, wireframe, cut-out).
   - Some "recordings" show signs of being rebuilt UI rather than real captures (section 2).
3. **"Floating app icons with labels and arrows into a product card": confirmed.** OMfU5iq8Eik 2:36 to 2:44: the icons stagger in, dotted orange connectors fan in to the Claude icon, and the icons then fly into a Claude window.
4. **"A UI region popped forward and enlarged while the rest dims, around OMfU5iq8Eik 4:34": confirmed, and it is the single most-used device.** Frame timing and dim value are in section 3.
5. **"A voice-dictation waveform overlay": confirmed, but it is the app's own dictation pill lifted with the spotlight, not a separate graphic.**
   - OMfU5iq8Eik 4:22: Claude's dictation pill (an ✕, waveform bars and a check) is enlarged over the dimmed capture.
   - The same kind of waveform pill sits at bottom centre during the IMSYKloMFrE recorded demo (9:50).

## 0. What the frames and descriptions say about his toolchain

The frames back up what he says about Rubric and the "animate" skill.

- **Rubric.** The descriptions call it "The Command Centre for AI Agents", and it is on screen as both an editor and a "second brain" (below).
- **Rendo, an extra tool.** The descriptions also name it: "Get RENDO - The AI Harness that Auto-Edits All our Videos". No frame shows it.

What the frames show:

- **A "RUBRIC Editor by Jay E | RoboNuggets" review UI** (IMSYKloMFrE 8:55 · the same UI appears in OMfU5iq8Eik 5:42 and nT_oMAIZ3P4 4:34 inside the community promo). Visible on it:
  - a header reading "ROUND 2"
  - toggles labelled **"Teach my /cut"** and **"Teach my /animate"**
  - buttons "Copy comments for ALL clips" and "Approve V1"
  - a REELS list where each chapter is its own reel with V1/V2 versions and a duration ("01 1. Claude Meetings 90.0s", "02 Replace every app, then the artifacts 41.3s", "03 2. Claude Whiteboards 117.8s")
  - a SCRIPT panel with one word highlighted orange, and a COMMENTS panel ("Fixed in V1", 11)
  - a timeline with rows SEC, PINS, SCENE (S1 to S7), CUTS, V1, A1 and WORDS, plus "RAW 6:09.06 / EDIT 0:08.49" and a 2.5x preview speed.
  - The preview shows the OMfU5iq8Eik 2:44 shot, so this is the edit of the n93 video.
  - What this establishes: per-chapter reels, versioned review rounds, and written comments per clip.
  - Inferred from the toggle label alone: a "Teach my /animate" switch suggests the comments are fed back into the `/animate` skill. That matches what he says about refining "animate" after every round of feedback.
- **The `/animate` skill node in "RUBRIC SECOND BRAIN"** (IMSYKloMFrE 8:14). It shows the path `.claude/skills/animate/SKILL.md`, "5.1 MB · 34d ago", and connections to `/hyperframes-helper`, `/hyperframes-robo` and `CONTENT.md`.
  - Outside knowledge, not from the frames: HyperFrames is the name of an HTML-based video composition framework. If that is what these skills refer to, his animation layer is authored as HTML.
  - The frame shows only the link, not how it is used.
- **A named style library** (IMSYKloMFrE 14:54 and 15:06). The tiles are labelled "Listicle house", "Isometric", "Contour relief 3D", "Halftone dot matrix", "Vox news cut-outs", "Chip chain", "One-take canvas" and "Comic pages". The "Isometric" tile's own text reads "Claude Motion (Anthropic's cloud)" vs "HyperFrames or your own rig (your computer)".
- **The house design system, shown on screen** (IMSYKloMFrE 11:46, "/robo artifact skill"):
  - swatches #f2efe8, #1c1a16, #ff6b1a, #e8e2d2, #3f9a5b, #3b78c4, #d9940f
  - typefaces **Outfit** and **DOTO**.
- **The wildcard rule** (IMSYKloMFrE 12:02 to 12:10, `wildcard/SKILL.md`):
  - "One rule: keep the palette and the voice, change the shape. On a video scene, the shape is the style itself, and it has to be new."
  - The next line, read from an upscaled crop: "...on a dark video its dark mode: near-black ground `#131311`, cards `#1b1a16`, cream `#e8e2d2` and white..."

## 1. Layout grammar and the webcam card

The whole show is one layout. A dark stage fills the frame, and a portrait webcam card sits bottom-left over it.

**Card geometry** (measured from luminance profiles in OMfU5iq8Eik 1:50, IMSYKloMFrE 11:46 and 14:46, and nT_oMAIZ3P4 3:09 and 6:06 · identical in all three videos):

| Property | Value at 1280x720 |
|---|---|
| Box | x 12 to 190, y 424 to 698 (178 x 274 px, about 2:3 portrait) |
| Size | 14% of frame width, 38% of frame height |
| Margins | 12 px left, about 22 px bottom |
| Corner radius | about 24 to 26 px |
| Edge | a 1 px light rim on the top and right edges |
| Outer halo | a soft glow about 20 px wide that is lighter than the background, not a dark drop shadow |

**Behaviour:**

- **Always the same place and size.** It never moves, scales or swaps corners in any burst.
- **The B-roll avoids it.** Content is laid out in the area to its right (x ≈ 200 to 1270). Screen captures are placed right of centre so the card never covers UI. For example, the OMfU5iq8Eik 1:50 window spans x 271 to 1209.
- **It drops out for three kinds of shot:**
  - the chapter cards that open each chapter, in all three videos (OMfU5iq8Eik 1:07.5 to 1:10.5, 6:02, 7:26, 10:54 · IMSYKloMFrE 5:34, 7:02, 9:26, 10:26 · nT_oMAIZ3P4 2:58, 6:46, 9:10, and the cut at 1:02.70 in the 30 fps burst). It stays on for the opening teaser grid (OMfU5iq8Eik 0:38) and the closing recap grid (14:42)
  - some third-party material (Lydia Hallie talk, nT_oMAIZ3P4 0:06 to 0:14, 1:18, 8:26 · Barry Zhang slides, IMSYKloMFrE 3:10 to 3:46)
  - the intro bio block.
  - It does **not** always drop for clips: it stays on during the Boris Cherny clip (IMSYKloMFrE 13:46) and the talk clip at IMSYKloMFrE 0:02.
- **Full-frame face is rare and structural.** It appears only in:
  - the bio beat about 45 to 60 s in. This has a serif name lower third "Jay / ENRIQUEZ" at OMfU5iq8Eik 0:46, a LinkedIn-style "Education / University of Technology Sydney" card overlaid at OMfU5iq8Eik 0:50 and nT_oMAIZ3P4 0:56, and a brand-logo collage beside him at nT_oMAIZ3P4 0:54 and IMSYKloMFrE 1:14
  - the free-guide plug
  - the sign-off.
- **The free-guide plug template.** The face is shown large in a rounded "squircle" frame at left with a fanned stack of the PDF guide at right (OMfU5iq8Eik 0:58, nT_oMAIZ3P4 2:46, IMSYKloMFrE 6:42). In nT_oMAIZ3P4 2:50 to 2:54, a "PDF guide" pill and small icons pop off the stack.
- **Transitions in and out of the face** use a heavy gaussian blur (OMfU5iq8Eik 13:10, IMSYKloMFrE 12:30).

## 2. How screen captures are framed

- **Ground.** The capture sits on the dark stage, never full-bleed during teaching segments. The stage is mostly #1c1a16. A dot-grid "terrain" made of halftone dots forms a soft hilly silhouette across the lower 40% of the frame, slightly lighter than the ground (visible behind everything in IMSYKloMFrE 11:46 and 14:54).
- **Two grounds.** Some scenes sit on a darker #13100f to #131310 instead: OMfU5iq8Eik 1:50 (titled capture), OMfU5iq8Eik 0:38 (chapter grid) and nT_oMAIZ3P4 1:03 (isometric chapter). Sampled at the same points, both grounds appear in all three videos. The on-screen wildcard text names #131311 as the ground and #1b1a16 for cards, which fits.
- **Header strip.** A centred title above the capture names the feature, e.g. "Claude Meetings" plus an orange "Beta" pill with the Claude asterisk mark (OMfU5iq8Eik 1:14 to 2:34). Later this becomes a two-line title plus a dimmer subtitle, e.g. "Dashboard / a page you share with your team" (OMfU5iq8Eik 8:26) and "Scrollyteller / a data story your team scrolls through" (8:38).
- **Size and edges.** Captures are large (70 to 75% of frame width) with the app's own window chrome and corner radius. When the app UI is light, the capture reads as a bright panel on the dark stage. No added device frame or browser mockup appears.
- **Left index rail.** For sub-items, a vertical rail of small cards at top-left numbers them, and the active one gets an orange outline (OMfU5iq8Eik 8:26 to 9:06: Dashboard, Scrollyteller, Visual short, Visual analysis).
  - How the rail fills (8:22.67 to 8:25.33 at 3 fps): a large label card ("Dashboard" with icon, orange border) blurs in at centre over the dimmed capture, holds about 2 s, then shrinks and travels into the rail slot. The title strip then fades in.
- **Recording mode.** During a "record your screen" demo, the capture gets a warm orange glow around the frame edges and an "▶▶ 8x" speed badge at bottom centre, showing the footage is sped up (IMSYKloMFrE 9:45.00 to 9:45.67).
- **Real or recreated? Unconfirmed.** Signs of real captures:
  - native cursors
  - live typing (OMfU5iq8Eik 1:06, IMSYKloMFrE 6:06)
  - app transitions such as the artifact pane collapsing in one frame at OMfU5iq8Eik 4:32.47 to 4:32.50.

  Signs of rebuilt UI:
  - a cursor labelled "You" (nT_oMAIZ3P4 3:11)
  - a permission dialog whose command swaps on a steady rhythm (`npm run build`, `npm test`, `npm run lint`, `npm run export`, nT_oMAIZ3P4 3:09 to 3:14) while check marks accumulate beneath
  - sample data with invented names (Maya, Alex, "Quillstack", "Fernbrook Wholesale").

  The safest reading is a mix of real captures and HTML recreations of the UI.

## 3. Spotlight, dim and pop-out callouts (the core device)

This is the move he uses most. **A region of the UI is lifted out of the capture, enlarged in place, and the rest of the capture is dimmed.** It is not a blur of the background and not a vignette.

- **Frame-accurate example** (OMfU5iq8Eik 4:33, 30 fps):
  - 4:33.07: plain capture.
  - 4:33.10: in a single frame, the whole capture darkens. The lifted timeline region appears already enlarged and motion-blurred.
  - 4:33.10 to 4:33.30 (about 6 frames, 0.2 s): the blur clears as it settles. That is a fast ease-out with no visible overshoot.
- **Second example** (nT_oMAIZ3P4 3:09.27 to 3:09.40, 30 fps): the "Bash command" permission dialog lifts in about 4 frames, with motion blur. Comparing full-res stills at 3:09.2 and 3:09.5, the sidebar and status bar stay at their original scale and are dimmed, so this is a pop-out, not a camera zoom.
- **Lift size.** The scale factor is set by the target size, not a fixed multiplier. The lifted element ends up spanning about 58% (nT_oMAIZ3P4 3:09.5, a scale of about 2.4x) to 68% (OMfU5iq8Eik 4:33.3) of frame width.
- **Dim amount.** A white pixel goes from #fcfcfc to about #747470 when dimmed (OMfU5iq8Eik 4:32.5 vs 4:34.0). That is a multiply of about 0.45, or a black overlay at about 55% opacity.
- **The lifted region stays live.** Date chips keep animating inside the popped timeline (OMfU5iq8Eik 4:34.00 to 4:34.50). It is a live crop of the capture, not a still.
- **Look of the lifted card.** It has a light rim, a subtle shadow, and sometimes a thin orange outline. Examples:
  - "Actions" lines (OMfU5iq8Eik 2:10)
  - the "Size: A sheet can hold..." doc line (OMfU5iq8Eik 6:50)
  - the wildcard rule line (IMSYKloMFrE 12:06)
  - "Current models usually carry on..." (nT_oMAIZ3P4 1:34).
- **Hold times** (2 fps burst, OMfU5iq8Eik 1:44 to 1:54): each pop holds about 1.5 to 2 s, then releases to the full capture before the next. Three pops happened in 10 s:
  - "Meetings Beta" sidebar item (1:45.5)
  - Google Calendar modal (1:48.0)
  - "Add Claude" button (1:51.5).
- **Variants:**
  - **Line extraction.** Code or config lines are lifted out one at a time as separate monospace chips with orange outlines, about one new chip per 0.5 s over the dimmed capture (nT_oMAIZ3P4 2:09.5 to 2:11.0, 2 fps sampling). A header pill then labels the set, "BEFORE Older models" in red (2:13). It flips to "AFTER Today's models" in green (2:15), and all five lines are struck through between the 2:16.0 and 2:16.5 samples.
  - **Dictation pill.** Claude's own voice-dictation pill (✕, waveform bars, check) is lifted over the dimmed home screen (OMfU5iq8Eik 4:22).
  - **Row of options.** The artifact-type row (Docs, Slides, Sheets, Design, Data, Whiteboard) lifts out of the Artifacts page, and one tile ("Sheets") is emphasised (OMfU5iq8Eik 3:14.5, 6:20).
  - **Toolbar lift.** The floating format bar with "Ask Claude" lifts out, then the "Ask Claude" button alone gets an orange ring (OMfU5iq8Eik 6:22, 6:43 to 6:46).
  - **Marker highlight.** A translucent orange marker sweeps behind key words inside a lifted line (OMfU5iq8Eik 6:49 to 6:50, "at least 200,000 filled cells" · IMSYKloMFrE 6:10 to 6:14, "From now on", "follows Anthropic's best practices"). The highlight wipes left to right across the words.
  - **Region boxes.** Red outline boxes wrap each of several panes to show overload (nT_oMAIZ3P4 9:38, "BEFORE You're the bottleneck").

## 4. Zoom: punch-ins vs auto-zoom

- **No auto-zoom that follows clicks, and no camera zoom on captures at all.** In the click-heavy stretches I checked (OMfU5iq8Eik 1:44 to 1:54 and 6:16 to 6:46 at 1 to 2 fps · IMSYKloMFrE 9:44 to 9:48 at 6 fps), the capture holds a fixed frame while the cursor moves and clicks. Emphasis comes from the pop-outs in section 3.
- **The one fast "zoom" I checked was a pop-out.** At nT_oMAIZ3P4 3:09.3 the background UI keeps its scale (section 3).
- **Crops change per segment.** Some segments place the capture large and cropped off the frame top or right (OMfU5iq8Eik 4:23 to 4:28, 6:16). These are static choices made at a cut, not animated moves.
- **Camera moves do exist, but on graphics only.** Examples are the pull-back and track pan in the opening, and the push into a chapter tile (sections 6 and 7).

## 5. Cursor treatment

- **Plain OS arrow cursor.** It is clearly visible on light UIs (OMfU5iq8Eik 1:50, the arrow over "Packaging review"). No highlight halo, trail or smoothing was seen. Its exact scale relative to the source was not measured.
- **Click ring.** A thin circular ring appears at the click point in the recorded demo (IMSYKloMFrE 9:44.67, 9:45.17). Only seen in that demo.
- **Labelled synthetic cursor.** A cursor with a small "You" tag clicks "Allow". The button flashes yellow, and a yellow check is added to a running row of checks under the dialog: 1, then 2, 3, 4 (nT_oMAIZ3P4 3:11.3 to 3:14.7).
- **Cursor inside a pop-out.** When a popped element is clicked, the cursor is shown inside the lifted card (OMfU5iq8Eik 1:51.5 to 1:53.5 on "Add Claude").

## 6. Motion style and easing (what the frames show)

| Move | Example | Timing seen | Shape |
|---|---|---|---|
| Spotlight pop | OMfU5iq8Eik 4:33.10 | 0.2 s | instant dim · card enters enlarged and blurred, sharpens · ease-out, no overshoot |
| Card flip reveal | OMfU5iq8Eik 0:40.00 to 0:40.37 | 0.3 s plus 0.07 s settle | Y-axis 3D flip with perspective · face swaps at edge-on (0:40.10 to 0:40.13) · lands slightly tilted and straightens |
| Isometric chapter entrance | nT_oMAIZ3P4 1:02.73 to 1:03.10 | about 0.35 s | tile grows from a point (about 4 frames) · number and label blur in at right · the prop (steering wheel) springs up, peaks about 10% oversize at 1:03.00, settles by 1:03.10 (back-ease / spring overshoot) |
| Camera pull-back | OMfU5iq8Eik 0:04.37 to 0:04.97 | 0.6 s | title collage shrinks to a thumbnail on a horizontal track · slow at both ends (ease-in-out) · a huge defocused copy of the title slides across the foreground as a depth layer (0:04.80 to 0:05.17) |
| Staggered icon entrance | OMfU5iq8Eik 2:36.37 to 2:36.57 (30 fps) | 0.2 s for five icons | hard cut from the previous scene, then the icons resolve from blurred to sharp left to right, 1 to 2 frames apart · the hub icon follows at about 2:37.75 with a blur-in (4 fps sampling) |
| Connector draw-on | OMfU5iq8Eik 2:39.75 to 2:40.25 | 0.5 s | dotted orange lines draw from five icons to the Claude icon at once |
| Fly-into-container | OMfU5iq8Eik 2:43.25 to 2:44.00 | 0.75 s | a Claude window blurs in under the icon row and the icons travel down into its slots |
| Word-by-word title | nT_oMAIZ3P4 0:00 to 0:02 | 0.3 to 0.7 s per word (3 fps sampling) | each word blurs in · an inline Claude logo chip sits inside the sentence |
| Particle assemble | nT_oMAIZ3P4 0:02.00 to 0:02.67 | about 0.67 s (3 fps sampling) | orange dots scatter in and assemble into "OUTDATED" in the DOTO dot-matrix face |
| Letter scatter assemble | IMSYKloMFrE 12:21.0 to 12:24.3 | about 1.3 s (first line) and 1.7 s (second line) | letters fly in from scattered positions and lock into "As big as a library," then "or as small as one sentence." · keywords turn orange · faint repeated copies of the lines scroll behind as a texture |
| Strike-through | nT_oMAIZ3P4 2:16.0 to 2:16.5 | all five lines within 0.5 s (2 fps sampling) | lines go from plain to struck between two samples |
| Value fill | OMfU5iq8Eik 0:40.30 to 0:40.50 | 0.2 s | progress bar fills as its panel arrives |

**The common signature:**

- object entrances are short, 0.13 to 0.4 s (text reveals run longer)
- nearly every entrance passes through a gaussian blur on its way in (blur-to-sharp is the house "fade")
- objects travel between scenes instead of being cut away (title to timeline thumbnail, label card to rail, icons into window)
- holds are long relative to the moves.

## 7. Transitions

- **Blur dissolve (default).** The outgoing scene blurs heavily and fades, the empty stage holds briefly, and the incoming scene arrives blurred and sharpens.
  - OMfU5iq8Eik 3:13.25 to 3:14.00: about 0.75 s in total, with about 0.25 s empty.
  - nT_oMAIZ3P4 0:03.67 to 0:05.33: about 0.7 s empty, with the webcam card blurring out too.
  - Also seen at OMfU5iq8Eik 2:34 and IMSYKloMFrE 9:44.50.
- **Camera along a track.** The opening of OMfU5iq8Eik is one continuous canvas:
  - title collage (0:01 to 0:04)
  - pull back onto a horizontal dotted track line with small cards on it (0:05 to 0:06)
  - pan along it to an icon row (0:06.67 to 0:07.67)
  - push into the Artifacts capture (0:08).
  - A thin orange horizontal line stays at mid-frame right through these scenes (OMfU5iq8Eik 0:14, 0:34, 0:38).
- **Brand sting.** The RoboNuggets logo card on light grey is wiped off by a sweeping yellow swoosh shape, leaving the dark stage, and the webcam card returns (nT_oMAIZ3P4 1:00.67 to 1:02.33 · IMSYKloMFrE 1:22). It marks the end of the intro.
  - Not in OMfU5iq8Eik. A 2 fps check of 1:03 to 1:10.5 goes from the face card straight to the PDF prompt and the chapter grid.
- **Push into a chapter tile.** The camera dollies into the next numbered tile in the chapter grid, and the tile then flips or expands into the chapter's first scene (OMfU5iq8Eik 0:38.33 to 0:40.37).
- **Hard cuts.** Used inside the capture when the app itself changes state (OMfU5iq8Eik 4:32.47 to 4:32.50).

## 8. Typography

Four type roles:

- **Display serif** for title cards and chapter grids: "7 Killer Features", "7 ✱ Features", "7 ✱ Levels", the tile numbers, and "Jay" in the bio. It is a bookish high-contrast serif in Anthropic's style, used with a ✱ asterisk mark in place of a word space ("7 ✱ Levels"). The exact face was not identified from frames.
- **A geometric sans consistent with Outfit**, the face named on the `/robo` card at IMSYKloMFrE 11:46. Used for headers ("Claude Meetings", "Dashboard"), labels under icons, chapter labels ("Steering"), "Tip 1" tags, and lines like "Hours in the background".
  - Weights: semibold for labels, bold for large statements.
  - Headers are often paired with a smaller, dimmer subtitle line below.
- **DOTO**, a dot-matrix display face named on screen. Used for single shock words and big transitional type: "OUTDATED" (nT_oMAIZ3P4 0:02) and the dissolving "...nts." (IMSYKloMFrE 0:50). It is set in orange or white, often assembled from particles.
- **Monospace** for code, file names and prompts in pills, e.g. `/doctor prompt-audit` (nT_oMAIZ3P4 2:34), `CLAUDE.md`, `SKILL.md`, `.claude/settings.json` (nT_oMAIZ3P4 5:38).

**Text rules seen everywhere:**

- One keyword per line turns orange: "Work **only you** can do", "The **reversibility** test", "Nudging Claude back **on course**", "Out **of the loop**" (nT_oMAIZ3P4 0:50, 6:02, 1:06, 11:22).
- Big numerals are orange: "99%" (IMSYKloMFrE 0:54), "447" (OMfU5iq8Eik 14:14), "80% cut" (IMSYKloMFrE 15:58), "+32%".
- Quotes get curly quote marks, an orange keyword, and a small grey attribution line, e.g. "Boris Cherny · Y Combinator" (IMSYKloMFrE 13:50).

## 9. Colour palette

**Shown on screen** (IMSYKloMFrE 11:46, "/robo artifact skill" · IMSYKloMFrE 12:06, wildcard SKILL.md):

| Role | Hex |
|---|---|
| White / cream | #f2efe8 |
| Dark | #1c1a16 |
| Orange | #ff6b1a |
| Cream | #e8e2d2 |
| Green | #3f9a5b |
| Blue | #3b78c4 |
| Amber | #d9940f |
| Dark-video ground | #131311 |
| Cards | #1b1a16 |

**Sampled with PIL from frames.** These are approximate: the values are 4 to 8-level bins from compressed 720p video.

| Use | Approx hex | Where |
|---|---|---|
| Stage ground (most scenes) | #1c1a16 | IMSYKloMFrE 11:46, 14:54 · nT_oMAIZ3P4 6:06 · OMfU5iq8Eik 2:40 |
| Darker ground (some scenes) | #13100f to #131310 | OMfU5iq8Eik 1:50, 0:38 · nT_oMAIZ3P4 1:03 |
| Lower dot-terrain area | #23211d | same frames, y ≈ 700 |
| Chapter tile fill | #24201c to #28201c | OMfU5iq8Eik 0:38 |
| Accent orange | #f86818 to #fc6a1c | OMfU5iq8Eik 1:50 Beta pill · nT_oMAIZ3P4 1:03 numeral · OMfU5iq8Eik 13:58 "Screenshot this" pill |
| Orange marker highlight on white | #e89860 | OMfU5iq8Eik 6:50 |
| BEFORE red | #e05048 | nT_oMAIZ3P4 2:14 |
| AFTER green | #40c068 | nT_oMAIZ3P4 2:16 |
| "Approve by hand" yellow | about #d8c070 (soft sample) | nT_oMAIZ3P4 3:12 |
| Dimmed-capture white | #747470 (from #fcfcfc) | OMfU5iq8Eik 4:34 |

**Usage:**

- Orange is the default and usually the only accent.
- Green and red appear only as AFTER/BEFORE and pass/fail semantics.
- Yellow appears only for the manual-approval state and the brand sting.
- Blue and purple appear as per-level colours in the IMSYKloMFrE "7 Levels" staircase (0:46 to 0:49) and level icons (9:22).

## 10. Arrows, connectors and labels

- **Labels under icons.** App logos float as rounded-square app icons with an Outfit label under each (Granola, Otter, Miro, Google Sheets, Power BI · OMfU5iq8Eik 2:38).
- **Dotted orange connectors.** Fan-in from several icons to one hub icon (OMfU5iq8Eik 2:40) and tree fan-out from one hub to children (IMSYKloMFrE 1:50, "Claude Code" to six skill icons, each child then getting an orange badge at 1:54). Also used in the "Master session" crown tree (nT_oMAIZ3P4 9:54 to 10:02).
- **Straight arrows with a "becomes" meaning.**
  - "Miro, FigJam, Excalidraw → Claude Whiteboards" (OMfU5iq8Eik 3:30), with plan-tier dots below (Pro, Max, Ultra, Team, Enterprise).
  - "Import → Claude Whiteboards" with a red ✕ on the struck "Import" path (OMfU5iq8Eik 5:02 to 5:06).
  - "fx Gross charges → $41,230" rows (OMfU5iq8Eik 10:14).
- **Hand-drawn style rings.** Orange ellipses circle a target (OMfU5iq8Eik 4:46, "Launch day" · nT_oMAIZ3P4 8:14 to 8:22). A cursive annotation with a curved arrow, "Anthropic Technical Team", appears at nT_oMAIZ3P4 0:14.
- **Lower-third chips.** For people in clips: name in bold plus role in grey, bottom-left of the clip ("Lydia Hallie · Claude Code team, Anthropic", nT_oMAIZ3P4 1:18 · "Barry Zhang · Member of Technical Staff, Anthropic", IMSYKloMFrE 1:34). Cut-out portrait photos with an orange outline appear in the "Vox news cut-outs" style (IMSYKloMFrE 2:02, nT_oMAIZ3P4 0:50).
- **State pills.**
  - "Beta", "NEW", "BEFORE", "AFTER", "Tip 1", "Level 2"
  - "soon?" and "only" under unsupported or supported items (OMfU5iq8Eik 1:42)
  - Active/Cancelled status pills with strike-through on the app name, flipping one row at a time (OMfU5iq8Eik 3:02 to 3:10).

## 11. Captions and on-screen words

- **No running subtitles.** No word-by-word caption track appears in any sampled frame.
- **Each beat gets a short title**, 2 to 6 words in Outfit, at top centre ("The go-ahead", "Deserves your decision", "Actions you can't undo", "A harder rule" · nT_oMAIZ3P4 3:06 to 5:02). Sometimes a subtitle line sits below. These read as paraphrased beat names, not transcripts.
- **Prompts are typed on screen in a Claude-style input box,** often with an orange "Screenshot this" pill (camera icon plus text) at the top right (OMfU5iq8Eik 11:42, 13:58 · nT_oMAIZ3P4 6:38 · IMSYKloMFrE 7:26). Key phrases inside the prompt get the orange marker.
- **Source credits sit small and grey, usually at the bottom.**
  - Checked at full resolution:
    - "Sources: Claude Help Center, Google Drive Help" (OMfU5iq8Eik 6:58, bottom-right)
    - "Anthropic's own example" (nT_oMAIZ3P4 5:42, under the code card).
  - Read from 384 px thumbnails, so approximate:
    - "Backdrops: Anthropic, theme-examples plugin" (OMfU5iq8Eik 12:14)
    - "Artificial Analysis...captured 20 Nov 2026" (OMfU5iq8Eik 13:18).
- **Corrections are on screen too.** Read from a thumbnail: "*Correction: Google Sheets holds up to 20 million cells (Google Help)" (OMfU5iq8Eik 7:02).

## 12. Recurring templates

1. **Cold-open title collage.** A big serif title with the Claude asterisk and a green "NEW" tag. Live mini-captures of the features are arranged around it, and their contents keep playing ($214K, +32%). It pulls back onto a track (OMfU5iq8Eik 0:00 to 0:05).
2. **Numbered chapter grid, "7 ✱ Features" / "7 ✱ Levels".**
   - Layout: two rows (4 + 3) of dark rounded tiles with a dotted texture and big serif numerals.
   - Completed chapters show an icon and name · the current one has an orange or chapter-coloured glowing border · the rest are dimmed numbers (OMfU5iq8Eik 1:10, 6:02, 7:26, 10:54, 14:42 · IMSYKloMFrE 5:34, 5:46, 7:02, 9:26, 10:26, 12:26).
   - The grid returns at every chapter boundary as a progress map. The webcam card is hidden on these chapter-opening grids but stays on for the opening teaser grid (OMfU5iq8Eik 0:38) and the closing recap (14:42).
   - In IMSYKloMFrE each level has its own colour and icon (green "Do it once, skillify", amber "Record your screen", purple "Skills others built").
3. **Isometric chapter card** (nT_oMAIZ3P4 only). A single dark isometric tile with one orange prop on it (steering wheel, check-in-box, clipboard, server with blocks), plus a huge orange numeral and an Outfit label: "1 Steering", "2 Approving", "4 Verifying", "5 Starting sessions" (1:03, 2:58, 6:46, 9:10). Category overview at 0:18 to 0:42: five isometric cubes with icons that turn from grey to red or green.
4. **"Tip N" tag plus title.** An orange "Tip 1" pill followed by the tip title (nT_oMAIZ3P4 1:38 "Tip 1 Use the latest models", 7:50, 8:46). Inside a tip, the pill shrinks to a top-centre breadcrumb (nT_oMAIZ3P4 10:02 to 10:58).
5. **BEFORE / AFTER pair.** Red "BEFORE" and green "AFTER" pills on side-by-side panels or the same scene re-labelled (nT_oMAIZ3P4 1:26, 2:13 to 2:18, 5:06 to 5:14, 6:54 to 7:14).
6. **Stat comparison bars.** Label rows with thick rounded bars and a big orange or green number (OMfU5iq8Eik 6:54 "at least 200,000 cells" vs "up to 20 million* cells" · OMfU5iq8Eik 14:30 "Fable on every message" vs "Sonnet, with Fable as advisor" · nT_oMAIZ3P4 3:46 to 3:54 "People checking 14%" vs "Auto mode checking 89%").
7. **Free-guide plug.** Face squircle plus a fanned PDF stack, then the PDF attached in a Claude prompt box with a typed question (OMfU5iq8Eik 0:58 to 1:06 · IMSYKloMFrE 6:42 to 6:50).
8. **Bio intro.** Serif name lower third, LinkedIn-style education card, brand logos (Virgin, HelloFresh, Microsoft, Knorr, Ogilvy, Hellmann's, Lipton), the RoboLabs site, and a community world map with photo pins, then the yellow brand sting.
9. **Community promo block.** Mid-video: about 4:10 to 4:50 in nT_oMAIZ3P4, 5:18 to 5:58 in OMfU5iq8Eik, and 8:26 to 9:10 in IMSYKloMFrE. It shows Skool pages, module cards, the Rubric promo, the Rubric Editor, reviews and member-win screenshots. Each screenshot shows full-bleed or large with a pop-out (OMfU5iq8Eik 5:18 to 5:58 · nT_oMAIZ3P4 4:10 to 4:50 · IMSYKloMFrE 8:26 to 9:10).
10. **Recap checklist.** The chapter names as a list with green ticks, shown over a YouTube end-screen layout (nT_oMAIZ3P4 11:30 to 11:38). OMfU5iq8Eik ends instead with the grid collapsing into isometric orange blocks (14:50 to 15:02).

## 13. Illustration styles in rotation

These match the style-library names shown at IMSYKloMFrE 14:54 to 15:06:

- **Isometric:** dark slabs, orange props, a small orange "space invader" robot mascot (nT_oMAIZ3P4 throughout · OMfU5iq8Eik 13:34 to 14:18).
- **Line-art wireframe stacks** with leader-line labels (IMSYKloMFrE 0:14 to 0:30: "the chip / the operating system / the apps").
- **Contour relief terrain** with a dotted path to a flag (IMSYKloMFrE 1:06, 5:26 to 5:30, 16:06).
- **Halftone dot matrix:** dot-field people grids and dot type (IMSYKloMFrE 0:50 to 0:58, 1:26, 9:18).
- **Vox-style cut-outs:** paper-texture strips, torn labels, cut-out portraits (nT_oMAIZ3P4 6:02 "The reversibility test", 7:54 to 8:22).
- **Flat UI diagram cards**, built from the same tokens as the app captures (IMSYKloMFrE 10:42 to 11:38, customer-database fan-out).

## 14. Pacing

**Method:**

- **Face vs B-roll.** Hand classification of every 4 s sample (231, 177 and 248 frames). An automated webcam-card detector misfired on light full-bleed pages, so its flags were re-checked by eye.
- **Change cadence.** Measured with `robo/beats.py`: the B-roll area (x ≥ 220) sampled at 4 fps · an "event" is when the given share of pixels changes by more than 24 grey levels from the previous sample, with at least 0.75 s between events.
- **Calibration.** Against a hand-read window (OMfU5iq8Eik 2:30 to 2:45: 7 changes by eye vs 5 to 6 detected), the detector misses small icon and line entrances. Real cadence is somewhat faster than the numbers below.

| | OMfU5iq8Eik | IMSYKloMFrE | nT_oMAIZ3P4 |
|---|---|---|---|
| Full-frame face or large face card | 6/231 = 2.6% | 4/248 = 1.6% | 5/177 = 2.8% |
| Webcam card visible | 92% | 91% | 89% |
| B-roll occupying the stage | ~96% | ~97% | ~95% |
| Change events/min, ≥5% of pixels | 21.4 | 16.7 | 17.8 |
| Median gap, ≥5% events | 2.0 s | 2.0 s | 2.1 s |
| Median gap, ≥15% events (scene-scale) | 3.0 s | 3.2 s | 2.8 s |
| Mean gap, ≥15% events (proxy for average shot length) | 4.6 s | 9.8 s | 9.3 s |
| Quarter-seconds with no change | 64% | 67% | 51% |

**Reading:**

- Effectively the whole runtime is B-roll with the presenter in a corner card.
- Something new enters every 2 s or so.
- A scene-scale change happens about every 3 s at the median, with longer holds on dense captures pulling the mean to 5 to 10 s.
- From the 4 s sheets, almost no two consecutive samples show the same composition.
- nT_oMAIZ3P4 moves the most continuously (fewest still quarter-seconds), which fits its illustration-heavy build.

## 15. How the three videos differ (in upload order)

**Shared by all three:**

- the stage, dot terrain and webcam card geometry
- the orange accent and Outfit labels
- the spotlight pop-out
- blur-dissolve transitions
- the free-guide plug
- the community promo block.

**What changes:**

- **n93, OMfU5iq8Eik (Nov 22).**
  - Capture-led: most segments are a titled Claude capture with pop-outs, and illustration appears mainly at the end (isometric blocks, 13:34 to 15:02).
  - Chapter navigation is the serif "7 ✱ Features" grid with a 3D tile flip.
  - The opening is a continuous camera-on-a-track move.
  - Bio has the serif name lower third · no yellow sting (checked at 2 fps over 1:03 to 1:10.5).
- **n94, IMSYKloMFrE (Nov 23).**
  - More concept illustration: wireframe stacks, contour terrain, halftone dot type, cut-outs, flat diagram fan-outs.
  - The same grid template becomes "7 ✱ Levels" with a colour and icon per level.
  - Adds the yellow brand sting.
  - Shows its own machinery on screen: Rubric second brain, the `/animate` node, the style library, `/robo` and wildcard.
- **n95, nT_oMAIZ3P4 (Nov 24).**
  - Illustration-led: isometric scenes carry most chapters, and captures become supporting inserts.
  - The chapter card switches to the isometric tile plus orange numeral.
  - Adds "Tip N" tags, BEFORE/AFTER pills and the particle-assembled DOTO hook word.
  - The webcam card hides on the Lydia Hallie talk footage. It hides on chapter cards too, as in the other two videos.

Whether the n95 changes are refinement or variety is not settled by the frames. Two explanations are visible on screen:

- per-round comments with "Teach my /animate" (IMSYKloMFrE 8:55)
- the wildcard rule, "keep the palette and the voice, change the shape" (IMSYKloMFrE 12:06).

Both are consistent with what changed. The palette, type and layout did not change, while scene shapes did.

## 16. The 8 principles most worth copying, ranked

Every build note assumes a renderer that seeks to time t and screenshots. Each effect must be a pure function of t: no CSS transitions, no `requestAnimationFrame` state, and particles seeded from a fixed PRNG.

1. **One locked stage with a fixed corner webcam card.**
   - Spec: #1c1a16 ground, a halftone dot terrain at the bottom, card at x 12 / y 424 / 178x274 / radius 24 / 1 px light rim / soft light halo.
   - Build: draw the card at a constant rect every frame from the camera video's frame at t, and keep a content safe-zone of x ≥ 200 that every scene lays out inside.
2. **Spotlight pop-out instead of zoom.**
   - Spec: dim the capture to about 45% brightness and redraw a source rect from the same live capture, scaled until it spans about 58 to 68% of frame width, with a light rim and optional orange outline.
   - Build: `k = easeOutCubic(clamp((t-t0)/0.2))` · draw the capture with `filter: brightness(0.45)`, then `drawImage(capture, srcRect, lerpRect(srcRect, dstRect, k))` with `blur((1-k)*8px)` · hold 1.5 to 2 s, then reverse.
3. **Blur is the house fade.**
   - Spec: every entrance goes from blurred and transparent to sharp in 0.13 to 0.4 s · scene changes blur out, hold the empty stage about 0.25 to 0.7 s, then blur in.
   - Build: one `enter(t, t0, dur)` helper returns `{alpha: k, blur: (1-k)*12, scale: 0.96+0.04k}`, applied via `ctx.filter` and `globalAlpha` for every element.
4. **Things travel, they don't cut.**
   - Spec: a label card shrinks into its rail slot, icons fly into the app window, the title collapses into a track thumbnail.
   - Build: give shared elements a stable ID, and between scenes A and B interpolate their rect from A's layout to B's with `easeInOutCubic` over 0.6 to 0.75 s, cross-fading only the non-shared elements.
5. **A chapter map that returns at every boundary.**
   - Spec: a 4 + 3 tile grid with past chapters named and iconed, the current one glowing orange, the rest dim · push the camera into the next tile and flip it to reveal the chapter.
   - Build: a camera transform `scale(lerp(1,2.4,k))` toward the tile centre, then a Y-axis flip faked with `scaleX(cos(πk))` (swap the face when `cos < 0`) over 0.3 s, ending with a 2 to 3 degree tilt that settles in 0.07 s.
6. **One accent, one keyword.**
   - Spec: orange #ff6b1a is the only accent · every title line colours exactly one keyword orange · red/green only for BEFORE/AFTER · numerals big and orange.
   - Build: the beat spec carries `{text, accent: "word"}`, and the renderer splits the string and fills the accent run with the token. Enforce "max one accent run per line" with a lint test on the spec.
7. **Three type roles with fixed jobs.**
   - Spec: serif for titles and chapter numerals, Outfit for every label, DOTO for single shock words assembled from particles, mono for code and filenames.
   - Build: load the fonts before frame 0 (`document.fonts.load`). For the DOTO assemble, rasterise the word to an offscreen canvas, sample lit dot centres, and lerp each from a seeded random start with per-dot delay `seed%0.3` over 0.67 s.
8. **Small overshoot on illustration props, plain ease-out on UI.**
   - Spec: props on isometric tiles spring about 10% past full size and settle within about 0.1 s (nT_oMAIZ3P4 1:03.00) · the UI pop-outs checked at 30 fps showed no overshoot.
   - Build: `easeOutBack(k, s=1.4)` for illustration props only, `easeOutCubic` for everything that is UI, picked by an element `kind` field so the rule is enforced by code rather than taste.
