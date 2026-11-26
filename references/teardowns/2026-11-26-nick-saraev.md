# Nick Saraev B-roll teardown

Written November 26, 2026 from two videos:

- **4TnNitqm8uM**, "Claude is eating the world" (Nov 20, 2026, 9:54). An essay video with custom motion-graphic slides.
- **F7QcpCl0Lg4**, "This NEW Claude Update Literally Changed Everything" (Nov 25, 2026, 6:27). A product-demo video.

Timestamps below are `video id m:ss`. I shorten the ids to **4TnN** and **F7Qc**.

## Method and limits

- I read all 10 contact sheets (one frame every 4 s).
- I pulled 35 full-resolution stills and 41 frame bursts at 1.5 to 30 fps. Seven of the bursts are native 30 fps frames.
- I measured per-frame motion and sampled pixel colors with small scripts. The working files were scratch and are not kept.
- The files are 720p and have **no audio**. Any "lands on a beat" claim is inferred from timing only. I could not hear the speech.
- n = 2, and the formats differ (essay vs demo). A difference between the two videos is not evidence of a trend.
- Colors are sampled from compressed 720p frames, so treat each hex as ±10 per channel. Thin strokes come out blended with the background, so I report only fills and thick marks as reliable.

---

## 1. The frame system (the master template)

Every B-roll frame in both videos sits on the same layout.

- **Inset panel.** The B-roll is a rounded rectangle inset about 21 px from every edge at 1280x720 (about 1.7%), with corners of roughly 10 px. Examples: 4TnN 2:05 and F7Qc 3:18.
  - The margin around the panel is not a flat fill. Brightened 2x, it shows a soft blurred image: gray at the top and a warm blob at bottom center.
  - The margin reads about #111113 to #151719 in Nov 20 and about #24282A in Nov 25. I could not establish the source of the blurred image.
- **Browser chrome on every web source.** A dark top bar (#16181A) carries three dim traffic-light dots on the left and a centered pill (#26282C) holding a small **monospace label**.
  - The label is edited copy, not a raw URL bar. Examples: "reddit.com · Anthropic's grim reaper week" (4TnN 6:02), "x.com/felixrieseberg · Nov 24, 2026" (F7Qc 3:18), "sherwood.news/tech/anthropic-launches-claude-design-..." (4TnN 0:30).
  - So the pill doubles as the source credit and, on Nov 25, the date stamp.
- **Face-cam card.** It sits at x 1146 to 1268 and y 257 to 460 at 720p (measured on stills 4TnN 1:56 and 2:05). That's about 122x203 px, or 9.5% of frame width by 28% of frame height, in a 0.6 portrait ratio.
  - It is vertically centered and about 12 px from the right edge, so it overlaps the panel's right border.
  - It has rounded corners and shows the live face-cam feed, so hand gestures are visible inside it (4TnN 2:14, F7Qc 1:46).
  - It is present in every sampled B-roll frame of both videos.
- **Content is centered on the space left of the card, not on the frame.** Slide titles center at x≈553 rather than 640 (4TnN 1:50 "What the lab sees", 4:18 "How they pitch it", 2:45 "After you opt out"). That is the midpoint of x 22 to 1085.
- **Dark slide plate (Nov 20 only).** A near-black panel (#090907 to #0F0F0D, slightly warm) with a planet-horizon arc across the top: a thin orange-peach rim (#DA8059 to #E5895E) under a blue halo (#2F446D), fading to #20191B below.
  - The arc looks the same on every dark slide I sampled, from 1:10 to 9:46.
  - It **does not scale with the push-in**. During 5:43.4 to 5:47.5, when the split-flap board grows 4.6%, the rim stays at y 53/52 at x 640, y 70 at x 300 and y 106 at x 100.
  - So the plate is a fixed background layer, and only the content above it moves.

## 2. Motion style and easing

The overall feel is calm. Cuts are instant, elements arrive with short eased entrances, and the content creeps forward with a slow linear push-in while the background plate stays fixed. In the cuts and entrances I examined, nothing bounces, overshoots, flies in from off-screen, or spins.

| Motion | Example (sample rate) | What the frames show |
|---|---|---|
| Slow push-in on held slides | 4TnN 5:43.4 to 5:47.5, split-flap board (stills at 1 s spacing) | Width 784 to 820 px in 4.1 s: a steady 9 px/s, about 1.1% scale per second, **linear** (constant increments). The arc behind it doesn't move. |
| Same on headline slides | 4TnN 2:02.4 to 2:06.5 (stills) | Text block 582 to 608 px wide (about +1.1%/s). It scales around the content center, x≈550. |
| Push-in on full-page documents | 4TnN 2:11 to 2:15 (stills) · 4TnN 0:06 to 0:08.7 (3 fps, visible growth) | About +0.4%/s. |
| Documents, Nov 25 | F7Qc 0:22 to 0:26, 0:03.5 to 0:07, 5:36 to 5:52 (stills) | 0:22 to 0:26 grows about +0.6%/s. 0:03.5 to 0:07 doesn't move. 5:36 to 5:52 drifts about 1 px/s left. |
| Callout cards hold still | 4TnN 0:17.6 to 0:20.5 (stills) | Box edges fixed at x 238 to 1041. No drift. |
| Callout card entrance | 4TnN 2:07.47 to 2:07.85 (30 fps) | Cut to the sharp page at 2:07.47. Over the next ~0.1 s the page begins to blur and the excerpt appears in a lighter box. Once its border is measurable, the card grows from 720 to 803 px wide over 0.27 s, with per-frame increments of 31, 20, 14, 8, 6, 0, 4, 0. That is a **strong ease-out**. It starts around 0.90 scale and settles with no overshoot. |
| Yellow highlight sweep | 4TnN 2:08.00 to 2:08.80 (30 fps) | Left to right, about 0.67 s for one full line. Per-frame area gain falls from about 1000 to about 100 px², so it is **ease-out**. A short second line then follows in about 0.1 s. It starts about 0.15 s after the card settles. |
| Underline draw | F7Qc 5:33.60 to 5:34.5 (30 fps) · 4TnN 2:15.4 to 2:16.5 (4 fps) | Left to right. In Nov 25 the speed decays from 27 px/frame to about 9 px/frame (**ease-out**). An underline takes about 1 s whether it spans one line or two (Nov 20 drew two lines in about 1.1 s). Start time is cue-driven: 0.77 s after the cut at F7Qc 5:33, but 5.1 s after the cut at 4TnN 2:10.3. |
| Toggle switch | 4TnN 4:14.10 to 4:14.33 (30 fps) | Knob x 922 to 950 in 0.23 s, with increments 1, 1, 3, 5, 8, 6, 3, 1: **ease-in-out**. The track turns coral in the last 2 frames. The counter beside it snaps 0 to 1 on the same frame (4:14.27). |
| Split-flap text | 4TnN 5:42.70 to 5:43.30 (10 fps) | Hard cut onto a board already scrambling random glyphs. The cells settle left to right in about 0.6 s. |
| Element fade-in | 4TnN 1:49.7 to 1:49.9 (10 fps) | The pipeline node "Apps" fades from ghost to full in about 0.2 s. Labels come up gray first, then white. |
| Line draw-on | 4TnN 2:20.5 to 2:22.5 (2 fps) | Plant diagram: the stem grows up and the branches grow out one at a time, with their labels fading in after each branch. |
| Type-on of headline lines | 4TnN 2:02.20 to 2:02.30 (10 fps) · 9:35.33 to 9:36.33 (6 fps) | Each line reveals left to right in about 0.1 to 0.3 s ("turn it (" half-drawn at 2:02.20 · "tied to featu" at 9:35.83). |
| Sequential builds | 4TnN 1:32.0 to 1:34.0 (1.5 fps) · 1:49.5 to 1:51.5 (10 and 2 fps) · 3:42.5 to 3:43.7 (6 fps) | Nested boxes appear outer to inner, one every ~0.67 s: Your app, A wrapper, The model API, The AI lab. Pipeline nodes arrive about 0.5 s apart. Mock-browser tabs are added one at a time about 0.5 s apart. |
| In-place morph | 4TnN 3:44.0 to 3:44.33 (6 fps) | The mock browser's tabs collapse into a "Demand signal" tab and the page turns into "Connector use / Plugin use" cards without a cut. |
| Ambient loops while held | 4TnN 1:58 to 2:01 (2 fps), 1:50 (2 fps), 1:33 to 1:37 (1.5 fps), 4:14 to 4:18 (4 fps), 3:42.5 to 3:44 (6 fps) | Conveyor stamps pumping as items ride the belt. Bright dots travel along connector lines and along nested borders. The gear's coral dot rotates. Light streaks move along the loading bars in the mock browser. |

**Easing summary.** Entrances use ease-out, about 0.25 to 0.3 s for pops and 0.6 to 1.0 s for sweeps and underlines. State changes (toggles) use ease-in-out at about 0.23 s. Holds get a linear push-in of 0.4 to 1.1% per second on the content layer only. I did not resolve exact curve families (cubic vs quint).

## 3. Typography

All slide text is a neo-grotesque sans that looks like Inter or SF Pro. I can't identify the exact face from frames. The URL pills use a monospace face.

- **Headline slides** (4TnN 2:02 "Your chats / train the models / unless you / turn it off." and 9:35 to 9:39 "Niche gaps / tied to features / are ripe / for the picking").
  - Layout: left-aligned at x≈248 (about 19% of width), sentence case, 3 to 4 short lines.
  - Size: cap height about 65 px, so font size about 90 px, roughly 12.5% of frame height. Line pitch about 113 px (15.7% of height).
  - **Weights alternate** line by line: Light, Bold, Light, then Bold in coral.
  - A thin rule (about #4B4B4B, 1 px) runs under each line from x 248 to about 905.
  - White text has a soft bloom (glow) around it.
  - Under the coral line sits a faint blurred coral smear (4TnN 2:05, y≈630).
  - **The coral line arrives last in both cases.** How the first lines arrive varies:
    - At 9:35.33 the slide opens on "Niche gaps" alone, then "tied to features" and "are ripe" type on within about 1 s. The coral "for the picking" lands at 9:39.3, about 3 s later.
    - At 2:01.90 the first three lines are already up on the first frame after the cut, and "turn it off." types on at 2:02.2.
- **Slide titles**: about 40 to 45 px (about 6% of height), sentence case, white with bloom, centered on x≈553. Examples: "What the lab sees" (1:50), "After you opt out" (2:46).
- **Node labels**: about 26 to 30 px white. Subtitles under them are about 13 to 15 px gray, e.g. "Users ask / In the apps" (1:52).
- **Card headings**: about 32 px with list items about 18 px ("Stops / Does not stop", 2:45).
- **Single-sentence slide**: "A big one is cost." (4TnN 5:58), light or regular weight at about 100 px, centered on x≈575, with bloom.
- **Split-flap board**: uppercase, wide tracking, one glyph per tile ("OPEN WEIGHTS / 42% IN PROD.", 5:46).
- **Callout text**: re-set sans. It is about 28 px medium weight for a one-sentence headline quote (4TnN 0:30) and about 17 px regular for a paragraph (2:08).
- **Color on the key word**: usually one line, word or element per slide carries the coral accent: "turn it off." (2:02), "for the picking" (9:39), "Room for you" (7:06), "Harder to replace" (7:26).
  - Some slides use coral on a whole set instead: the four monogram tiles plus the pill at 1:10, and the four bordered cards at 3:14.
  - Everything else is white or gray.

## 4. Color palette (sampled)

| Role | Hex (approx.) | Sampled at |
|---|---|---|
| Panel background, dark slides | #090907 to #0F0F0D | 4TnN 2:05, 1:56 |
| Margin around panel | #111113 to #151719 (Nov 20) · about #24282A (Nov 25) | 4TnN 2:05 · F7Qc 0:09 |
| Horizon rim (orange) | #DA8059 to #E5895E | 4TnN 2:05 |
| Horizon halo (blue) | #2F446D | 4TnN 2:05 |
| Sky under arc | #20191B | 4TnN 2:05 |
| **Coral accent** (pill fill, toggle on) | #DA7656 to #DD7556 | 4TnN 1:10 pill, 4:18 toggle |
| Coral headline text (with bloom) | #FF8A62 to #FFA388 | 4TnN 2:05 |
| Primary text | #FFFFFF with glow | 4TnN 2:05, 1:56 |
| Neutral card fill | #1D1D1B to #232320 | 4TnN 1:56, 2:45 |
| Divider rules | about #4B4B4B | 4TnN 2:05 |
| Document paper | #FBF8F5 | 4TnN 2:18 · F7Qc 0:09 |
| Document text | #000000 to #0F0E0B | 4TnN 2:18 · F7Qc 0:09 |
| Blurred page behind callout | #CAC7C4 | 4TnN 0:30 |
| Callout card fill | #FCF9F8 | 4TnN 0:30 |
| Callout border | about #E08530, ~2 px, warm glow outside | 4TnN 0:30 |
| Highlighter | #FCD94C | 4TnN 0:30 |
| Underline | #F3651B to #F76519 (orange-red) | 4TnN 2:18 · F7Qc 0:09 |
| Browser bar / URL pill | #16181A / #26282C | 4TnN 0:30 |
| Face-cam backdrop | #A3ABC0 (Nov 20, cool light gray-blue) · #50565D (Nov 25, mid gray) | 4TnN 0:46 · F7Qc 0:02 |

The system is one warm near-black, one white, one gray, one coral accent, plus two document-only annotation colors (yellow highlighter, orange-red underline). The blue appears only in the horizon arc.

Active-card strokes (the coral outline on "Lab learns" at 1:52 or "Stops" at 2:45) are thin and anti-aliased. I didn't get a clean sample, so build them from the accent color above.

## 5. How web pages, documents and screen recordings are handled

Four distinct treatments:

1. **Callout mode** (Nov 20 only).
   - The page is shown blurred and dimmed behind a cream card with an orange border and glow.
   - The card holds the key sentence re-set in sans, larger than on the page.
   - A yellow highlighter then sweeps across the phrase that matters.
   - Examples: 4TnN 0:18 (Morningstar, "Thomson Reuters TRI, RELX REL, and Wolters Kluwer's"), 0:30 (Sherwood, "Anthropic launches"), 2:08, 2:58, 3:10, 3:26, 5:54, 6:06, 6:22.
   - The card grows out of the paragraph's position on the page. At 2:07.47 the sharp page shows the passage in the page's own small type · by 2:07.80 the same words sit in the card in larger sans, and the page has gone soft.
2. **Full-page mode** (both videos).
   - The page stays sharp and is shown large: body text about 26 px at 720p in 4TnN 2:18, and about 19 px in F7Qc 0:22.
   - An orange-red underline draws under the clause being discussed and can span several lines (4TnN 2:15, F7Qc 0:09, 4:14, 5:33 to 5:58).
   - In Nov 25 the underlines **accumulate** down the page. At F7Qc 4:16 to 4:19, "it updates automatically..." gets underlined after the later "This simplifies..." line, so the order follows something other than reading order.
   - Headline-level page titles also get underlined (4TnN 0:06 to 0:07 "Claude for Financial Services", 0:54 "Introducing Claude Whiteboards...", F7Qc 0:06 "Turn artifacts into Mac and Windows apps").
   - **Moving to another passage is a cut, not a scroll.**
     - At F7Qc 5:55.60 to 5:55.63 the view jumps in one frame to a lower crop of the same page, and the underlines from the previous crop are gone.
     - At 4TnN 0:08.67 to 0:09.00 it cuts to a different page in the same style.
3. **Media card mode** (both videos).
   - An image or video sits in a rounded card over an enlarged, blurred copy of itself, so the panel is filled with matching color.
   - Examples: meme at 4TnN 6:02 · tweet video at F7Qc 0:11 to 0:20, whose salmon backdrop is the blurred video.
   - The illustration at 4TnN 0:02.3 (CRT monitor eating devices) sits on a dark card with a warm vignette.
   - A tweet can also fill the whole panel as a white page with large text: Felix Rieseberg's post at F7Qc 3:14 to 3:22, underlined like a document.
4. **Live screen recordings** (Nov 25 mostly).
   - The real app is shown inside the panel with the cursor visible: Claude desktop, the Linnet image editor, the Sift permission dialog, and a 3D office walkthrough.
   - Examples: F7Qc 0:30 to 1:10, 1:38 to 2:58, 3:22 to 3:50, 4:54 to 5:00.
   - At F7Qc 1:46 a modal ("Sift needs to see your files") sits over a heavily blurred, glowing backdrop.
   - At F7Qc 1:05.97 to 1:06.23 the app window grows to fill the panel, and its canvas goes blank for a frame mid-way (1:06.03). That points to the app's own window resize being recorded, not an editor zoom.
   - I did not examine every recording frame by frame, so I can't rule out edit-side punch-ins elsewhere.

## 6. Callouts vocabulary

- **Callout card**: cream fill, ~2 px orange border, warm outer glow, soft drop shadow, page blurred behind it (4TnN 0:30).
- **Highlighter**: flat yellow rectangle behind the words, sweeping left to right (4TnN 0:19, 2:08).
- **Underline**: orange-red, about 3 px, rounded ends, drawn left to right, wraps across lines (4TnN 2:18 · F7Qc 0:09).
- **Active element**: coral stroke plus outer glow, or coral fill. Examples: "Lab learns" (1:52), "The AI lab" (1:34), "Stops" card with a coral check badge overlapping its corner (2:45), "Know-how" document with a magnifier (4:26).
- **Short coral dash** under a headline phrase: "Labs make the models" (1:42), "Growth hacker mindset" (9:42).
- **Check and X marks**: coral checks for the active side, gray X for the other (2:45).
- **Not seen in any sampled frame**: arrows drawn over documents, circles, hand-drawn scribbles, emoji, stickers.

## 7. Captions and on-screen words

- **No burned-in captions** of speech in either video, in any sampled face or B-roll frame.
- **No lower thirds** or name titles.
- On-screen words appear only as:
  - slide titles
  - diagram labels
  - the headline-type slides
  - the split-flap board
  - the monospace source pill
- The headline slides restate one spoken idea in 3 to 4 lines rather than transcribing the speech.

## 8. Pacing

Method: I sampled each video at 4 fps. I labelled a frame as face cam when it matched a median face-cam template (the distances split cleanly into two groups below and above 20). I counted shots as frame-to-frame changes above a threshold outside the card area. For Nov 20 I used the threshold that caught all six cuts I could see between 1:42 and 2:12 (1:44, 1:49.5, 1:56.3, 2:02, 2:07.5, 2:10.3).

| Measure | Nov 20 (4TnN) | Nov 25 (F7Qc) |
|---|---|---|
| Face cam share of runtime | 9.1% (54 s) | 24.5% (95 s) |
| B-roll share | 90.9% | 75.5% |
| Face blocks (count, median) | 11, median 5 s | 9, median 9 s |
| B-roll blocks between face returns (mean, median, longest) | 54 s, 45.5 s, 132 s (4:26 to 6:38) | 36.5 s, 29.5 s, 81 s (1:38 to 2:59) |
| B-roll by frame brightness | Dark frames (the dark slides) about 68% of runtime, bright frames (docs, web) about 21%, mid-tone about 2% | Bright frames about 39% (docs, tweets, and the light artifact-desktop recording at 0:27 to 0:44), dark frames about 24% (dark app recordings), mid-tone about 13% |
| Shot length inside B-roll | **About 5 s per shot** | About 10 to 11 s per source. Large source switches only, because motion inside the recordings (tweet montage at 0:11 to 0:20, 3D walkthrough at 2:04 to 2:55) defeats a frame-difference count. |
| Build steps inside a shot | Come in bursts right after the cut, 0.5 to 0.7 s apart (1:32 to 1:34, 1:49.5 to 1:51.5, 2:20.5 to 2:22.5) | Mostly underline draws on docs |

Nov 20's rhythm: open with about 2 s of face. Then a long B-roll run (44 s) with a new visual about every 5 s, short face check-ins of 3 to 10 s, and B-roll runs of up to 2 minutes 12 seconds. It closes on about 7 s of face.

## 9. Transitions

- **Face to B-roll and B-roll to face: single-frame hard cuts**, checked at native 30 fps.
  - Face at 4TnN 1:07.37, slide fully built at 1:07.40.
  - Slide at 2:26.27, face at 2:26.30.
  - Face at F7Qc 0:03.30, document at 0:03.33.
  - The face card is already on the first B-roll frame. It does not slide or scale in.
- **B-roll to B-roll: hard cuts** at the ones I checked at 10 to 30 fps (4TnN 1:49.50, 2:01.90, 2:07.47, 5:42.70, 4:13.87 · F7Qc 5:55.63).
  - The new slide either arrives fully built (1:07.40, 4:13.87) or arrives with its first element and builds the rest (1:49.5, 2:20.5, 9:35.33).
  - I saw no crossfades, wipes, whip pans or zoom transitions at any cut I examined.
- **Motion lives inside the shot**: entrances, sweeps, toggles, flaps, morphs and the slow push-in.

## 10. Face cam during B-roll

- It is always the same small live card at the right edge, vertically centered (section 1), in every sampled B-roll frame of both videos.
- It never moves, resizes, hides or enlarges in the frames I sampled.
- Diagrams are laid out to the card's left, so nothing important collides with it.
- On full-screen face shots the framing is a medium close-up, with the head centered at about 45 to 50% of frame width. I saw no punch-in zooms between jump cuts in the sampled face frames.

## 11. Recurring diagram vocabulary (Nov 20)

Most dark slides carry a title at top center and one simple diagram of 1 to 4 main elements. Type-led slides go without a title: headlines (2:02, 9:38), the split-flap (5:46), the equation tiles (7:38) and the file tree (7:42).

- **Pipelines and flows**:
  - "What the lab sees", 4 nodes with traveling dots (1:50)
  - "Where the analysis goes: Chats, Analysis, Report, Then?" (3:14)
  - "Drops create vacuums" (9:10)
  - "One paste, kept" (8:02)
  - "Where your IP goes" node graph (4:34)
- **Pyramids**: "Who builds what" in two dim tiers (1:46) · "Who takes what" with the top tier coral (9:26).
- **Trees**: plant-stem diagrams, "Similar opt-out settings" (2:22) and "AI in the economy" (9:02) · a decision tree, "The economic logic" (4:38).
- **Before and after**: "Stops / Does not stop" two-card checklist (2:46) · toggles "New startup / One-click plugin" with a 0 to 1 counter (4:14) · "A niche gap 0 month to 1 month" track (9:30).
- **Equations**: monogram tiles "An + Op + Go + OL = Same playbook" (1:10) and "Re + Ve + So = Hard to disrupt" (7:38).
- **Nesting**: "Isn't everything a wrapper?" boxes inside boxes (1:34).
- **Metaphor objects in thin white line art**:
  - lightbulb (1:42, 9:42)
  - scale (3:30)
  - conveyor with stamps (1:58)
  - gear (4:14)
  - magnifier over documents (4:26)
  - mountain with goal flag (5:26)
  - compass (8:26)
  - magnet (8:58)
  - temple with pillars (8:10)
  - bridge (7:22)
  - signpost (6:30)
  - orbit (6:50)
  - Venn diagram (5:06)
  - funnel or cone "Where to build" (7:06)
  - lock "Now open" (7:02)
- **Stats**:
  - ring gauge "25% of AI use on open models" (5:22)
  - big numerals "2 big worries" (5:34), "10,000" (9:10), "1 of many" (8:58)
  - split-flap "OPEN WEIGHTS 42% IN PROD." (5:46)
- **Mock UI** in the same dark style:
  - browser skeleton (3:46)
  - agent run log with write / run / ship steps (4:10)
  - chat app quoting "Andy Markus, AT&T" (5:38)
  - phone notifications (5:34)
  - file tree "company_sops" with the proprietary file in coral (7:42)
  - drop zone with upload bars (7:50)
  - "Who is accountable?" dialog (7:34)
  - harness model switcher (8:38)
- **Type-only slides**: the headline stack (2:02, 9:38), the dictionary entry "Demand, noun" (3:42), and the single sentence "A big one is cost." (5:58).

## 12. Reusable templates (what a renderer would need)

1. **Frame**: inset panel, browser-chrome bar with monospace source pill, fixed right-edge face card, dark horizon plate or white paper inside.
2. **Headline stack**: 3 to 4 lines, alternating Light and Bold, hairline rules, final line coral and revealed last.
3. **Title and diagram slide**: centered title at the top of the content area, a diagram built element by element, usually one coral active node, slow push-in on the content layer.
4. **Callout quote**: blurred source page, card lifted from the passage, highlighter sweep.
5. **Underline read-along**: sharp page, underlines drawn under the clauses being read, accumulating, with a hard cut to the next crop.
6. **Media card**: image or video in a rounded card over its own blurred enlargement.
7. **Stat slide**: ring gauge, big numeral, or split-flap.
8. **Mock UI**: toggle, chat, file tree, tabs or log, rendered in the dark card style, able to morph in place.

## 13. Nov 20 vs Nov 25

| | Nov 20 (essay) | Nov 25 (demo) |
|---|---|---|
| Dark motion-graphic slides | The backbone, about 68% of runtime | **None** seen |
| Document treatment | Callout cards plus highlighter, and full-page underlines | Full-page underlines only. No callout cards, no yellow highlighter. My detector's yellow hits all landed in app content (F7Qc 0:11 to 0:20, 0:57 to 1:04), which I checked by eye. |
| Screen recordings | Almost none (mock UIs are drawn) | Most of the B-roll: real app recordings with the cursor |
| Face share | 9.1% | 24.5% |
| Shot length in B-roll | About 5 s | About 10 to 11 s per source |
| Set | Black crewneck, light gray-blue backdrop (#A3ABC0), small mic low in frame | Black hoodie, mid-gray backdrop (#50565D), large mic with a glowing teal ring in frame |
| Panel margin | Darker (#111113 to #151719) | Lighter (about #24282A) |
| Shared | Inset panel, browser chrome with monospace source pill, the same face-card geometry, orange-red underline, hard cuts, slow push-in on held frames | |

Read this as a format difference: the essay uses invented diagrams, and the demo shows the real product. Two videos are not evidence of a style shift.

## 14. Corrections to the at-a-glance read

- **Near-black background with an orange and blue arc**: correct for Nov 20's slides. The arc is a fixed plate that does not scale with the push-in. It is absent from Nov 25.
- **Face cam as a small rounded portrait card on the right**: correct. It is vertically centered, about 9.5% by 28% of the frame, overlaps the panel edge, and cuts in and out with the B-roll rather than animating.
- **Thin white line diagrams with an orange glow on the active node**: correct. The accent is coral (#DA7656), not pure orange. White text also carries a bloom glow.
- **Headline with the last line in coral**: correct. It also alternates Light and Bold per line and has hairline rules. The coral line arrives last (2:02.2, 9:39.3). The earlier lines sometimes type on one by one (9:35) and sometimes arrive together on the cut (2:01.9).
- **Documents with orange box, yellow highlight, red underline and blur**: these are **two separate modes**.
  - The orange-bordered box, the yellow highlight and the blur go together (callout mode).
  - The underline is orange-red (#F3651B), not red, and it appears on sharp, unblurred pages (full-page mode).
  - The box text is re-set in sans, not a crop of the page.

## 15. The 8 principles most worth copying, ranked

1. **One fixed frame for every B-roll shot: inset panel, chrome bar with a source pill, live face card at the right edge.** It keeps the speaker present, credits every source, and makes docs, recordings and diagrams look like one show.
   *Canvas:* draw a full-screen layer, then a rounded-rect clip inset 1.7%, then a 35 px chrome bar with a monospace label pill ("domain · date"), then the face video via `drawImage` into a 9.5%×28% rounded rect at right-center. Lay content out in the box left of the card (center x = 43% of width).
2. **Hard cuts between shots, motion inside shots.** There are no transitions to tune. Attention goes to entrances and sweeps.
   *Canvas:* switch scenes on the cut frame with no blend. Give each scene its own timeline of tweens starting at local t=0.
3. **Slow linear push-in on every held frame (0.4 to 1.1% per second), content layer only.** Static slides and docs never look frozen, and the fixed background keeps it from feeling like a camera move.
   *Canvas:* draw the background plate untransformed. Then, for the content layer only, apply `ctx.setTransform(s,0,0,s,cx*(1-s),cy*(1-s))` with `s = 1 + 0.011*t` around the content center. Leave the face card and chrome untransformed too.
4. **Usually one coral accent per frame, on the thing that matters.** Everything else stays white or gray on warm black, so the eye knows where to go.
   *Canvas:* give each scene spec an `accentIds` list (normally one id). The renderer strokes or fills only those elements in #DA7656, adds a glow via `shadowBlur` 16 to 24 with `shadowColor` coral, and leaves the rest neutral.
5. **Headline stack: 3 to 4 lines, alternating Light and Bold, hairline rules, the last line coral and revealed last.** It turns one spoken claim into a poster.
   *Canvas:* lay out lines at 12.5% frame height and 15.7% pitch from x=19%. Draw 1 px rules at #4B4B4B. Reveal each line with a left-to-right clip rect at cue times, with the coral line on the last cue. Start at a 0.1 to 0.3 s ease-out reveal as a tuning value · that is an estimate, not a measurement. Add bloom with a blurred duplicate pass.
6. **Callout quote over a blurred source page, then a highlighter sweep.** The proof stays on screen while the one sentence is readable at a glance.
   *Canvas:*
   - Draw the page screenshot and ramp `ctx.filter = 'blur(Npx)'` over about 0.15 s. Start N around 8 and tune by eye · the radius was not measured.
   - Draw a cream card scaling from 0.90 to 1.0 with ease-out over 0.27 s, with a 2 px #E08530 border, a glow, and the re-set text.
   - Then fill a #FCD94C rect behind the target words, its width animated with ease-out over about 0.7 s per line.
7. **Underline read-along on sharp documents, accumulating as the clauses are discussed, then a hard cut to the next crop.** It is cheap to produce and makes real sources feel narrated.
   *Canvas:* store each underline as a list of per-line spans (x0, x1, y) with a cue time. Draw a 3 px #F3651B round-cap line whose end x eases out over about 1 s per underline from its cue. Keep earlier spans drawn until the scene cuts.
8. **Diagrams built element by element, about 0.5 to 0.7 s apart, with small ambient loops while held.** Examples are traveling dots on connectors, a rotating gear, a pumping conveyor and light streaks on bars. A held diagram keeps a pulse without new information.
   *Canvas:* give each element an `enterAt` time with a 0.2 s opacity and gray-to-white fade or a stroke draw-on (`setLineDash` / `lineDashOffset` over path length). Add looped tweens (dot position along a path modulo period) that run for the life of the scene.
