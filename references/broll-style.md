# B-roll style for the Get Serious podcast

How Serious AI builds podcast B-roll, why, and how it gets better every week. Get Serious is Chris Duff's weekly show on enterprise AI (YouTube @seriousai). The episodes are talking head with no screen share, so B-roll covers about 60% of the runtime with Chris in a head card at top right.

Started November 26, 2026, from a frame-by-frame teardown of two creators whose B-roll keeps improving week over week. The full teardowns, with timestamps, sit beside this file in `teardowns/`. RoboNuggets (Jay E) builds his with a paid tool called Rubric and a Claude skill he calls "animate", and a review screen in his own videos shows a "Teach my /animate" toggle beside the comments on each clip (IMSYKloMFrE 8:55). The feedback ledger at the bottom of this file is our version of that loop.

## Contents

- The house style: the rules every scene follows
- What we took from whom
- The kit: `helpers/broll/`
- Session capture: real work on screen
- Whiteboard scenes
- The weekly loop and the feedback ledger

## The house style

1. **The brand, not the creators' palettes.** Serious Navy `#0D1C37`, Surveyor Orange `#FF5A1F`, Ink `#090519`, white, off-white `#F7F7F7`, the navy ramp. Montserrat for anything that reads as a headline or label, Inter for small text. Lint rule B4 holds this. The capture of a real app keeps that app's own colors, the way a document page keeps its own.
2. **One keyword per line is orange,** with a sketched orange underline on whiteboard scenes. Everything else is navy or white.
3. **Something new arrives about every 2 seconds** and the scene itself changes about every 3 to 10. A stretch where nothing moves for 5 seconds is a gap to fill with the next spoken beat. Each new element lands on the word that names it (find the word's start in the transcript, start the entrance there).
4. **Entrances blur in** (`BK.enter`, 0.3 s, ease-out). Strokes draw on. Nothing pops in from nothing, nothing bounces on UI. A held frame drifts in slowly (`BK.drift`, about 0.5 to 0.8% a second), and a held diagram keeps a small loop running (`BK.flow`).
5. **Things travel instead of cutting.** When the story moves something (the checks into the cloud), animate that move. Cut only when the scene changes topic.
6. **Show the real thing when Chris mentions it.** A real Claude Code session (session capture), a real document page with the passage highlighted or underlined as he reads it, a real number he said. A drawn stand-in is second best. Every real source carries its pill in the window bar: what it is and its date.
7. **Callouts lift and dim.** To point at one line of a capture, lift it out enlarged with an orange rim while the rest dims to about 45% (`BK.popout`), then sweep the highlighter behind the words as he says them. Camera pushes (`BK.camera`) are for whiteboards and wide screen recordings.
8. **Every number on screen is one Chris says** (B1), no dashes or banned words on screen (B2), nothing flashes by (B3), nothing sits under the head card (B5).
9. **Warm beats stay on his face.** Stories about people, credit to a team, the turn into a pledge or a close. That one is judgment, so it stays prose.

## What we took from whom

**RoboNuggets** (`teardowns/2026-11-26-robonuggets.md`):
- The pop-out callout: dim the capture to about 45%, lift the region enlarged with a blur that clears in 0.2 s, hold 1.5 to 2 s (OMfU5iq8Eik 4:33).
- Blur as the house entrance and transition (OMfU5iq8Eik 3:13).
- Elements that travel between scenes rather than cut (OMfU5iq8Eik 2:43, icons fly into the app window).
- The highlighter sweep behind key words in a lifted line (OMfU5iq8Eik 6:49).
- One accent color and one orange keyword per line (nT_oMAIZ3P4 0:50, 6:02).
- B-roll on screen for about 95% of the runtime with the face in a fixed corner card, something new every 2 s.
- A review loop where every round's comments teach the animation skill, and a "wildcard" rule that keeps palette and voice fixed while the scene shapes change (IMSYKloMFrE 12:06).

Not taken: his palette, his fonts (Outfit, DOTO), the dot terrain, the serif chapter grid. Those are his brand.

**Nick Saraev** (`teardowns/2026-11-26-nick-saraev.md`):
- One frame for every shot, with a source pill in the window bar ("domain · date") so every real source credits itself (4TnNitqm8uM 2:10, F7QcpCl0Lg4 3:18).
- A slow push-in on every held frame, 0.4 to 1.1% a second, on the content only, so a still never looks frozen.
- The headline stack: big left-aligned lines with the last line in the accent color, revealed last ("turn it off.", 4TnNitqm8uM 2:06).
- Real documents two ways: a blurred page under a quote card with a highlighter sweep (4TnNitqm8uM 2:58), or a sharp page with an orange underline that reads along (4TnNitqm8uM 2:18).
- Diagrams built one element at a time, 0.5 to 0.7 s apart, with a small ambient loop (dots travelling a connector) while they hold.
- Usually one accent per frame, on the thing that matters.

Not taken: his warm-black arc background and coral accent, and his hard cuts between shots (we keep a quick blur or fade, from RoboNuggets). His face cam is on screen 9% of the Nov 20 essay and 25% of the Nov 25 demo, against under 3% for RoboNuggets. Ours sits near 40%, which the warm-beats rule decides.

## The kit: `helpers/broll/`

A scene page is one HTML canvas where every frame is a pure function of time, so headless Chrome can render any frame in any order. The page loads `broll/vendor/rough.js` and `broll/broll-kit.js` (copy or symlink the folder next to the page as `kit/`), exposes `window.ready`, `window.DUR`, `window.seek(ct)`, and `window.rawAt(ct)` when its clock is offset from the raw recording.

| Piece | Use |
|---|---|
| `BK.camera(g, t, keys, W, H)` | ease-in-out push and pan between keyframes `{t, x, y, z, dur}` |
| `BK.frame(g, img, crop, box, o)` | a capture in a padded window with a title bar and 12 px corners |
| `BK.typeLines(g, lines, map, K, p, bg)` | type a capture's real text in, line by line, from `layout.js` |
| `BK.cursor(g, t, path)` | pointer that glides between points, with an orange ring on click |
| `BK.popout(g, img, crop, from, to, k, W, H)` | lift a region out, enlarged, and dim the rest |
| `BK.marker(g, rect, p)` | highlighter sweep, left to right |
| `BK.spotlight(g, rect, a, W, H)` | dim all but one region, in place |
| `BK.enter(g, t, t0, draw)` | blur-in entrance |
| `BK.drift(g, t, t0, cx, cy)` | slow push-in on a held frame |
| `BK.flow(g, t, p0, mid, p2)` | dots travelling a connector while a diagram holds |
| `BK.sketch(seed)` + `BK.ink(g, shape, p)` | rough.js shapes with a fixed seed, drawn stroke by stroke |

Render with `SCENES=scenes.html node helpers/broll/render.js 3840 2160 30 out.mp4 [t0 t1]` (split long tracks into four parallel chunks and join them with ffmpeg concat, `-c copy`), or `... still 12,40 prefix` for stills. Fonts come from `Serious AI/Media and Assets/Claude Design/` (Montserrat and Inter variable TTFs) into the page's `fonts/`.

**Before rendering**, `node helpers/broll/texts.js scenes.html texts.json`, then `python3 helpers/broll_lint.py texts.json --transcript <ep>-transcript.json --page scenes.html`. Fix every hit.

## Session capture: real work on screen

When Chris mentions work he did with Claude ("my Claude checked itself for the check"), find that session and show it. Never a screen recording, because a recording shows whatever was on screen.

1. Find the session: `~/.claude/projects/<project>/<id>.jsonl`, searched by date and phrase. Read the turns around the moment.
2. Write a spec: the session path, and for each turn to show, its timestamp prefix, role, and the exact excerpts to keep. Excerpts must be verbatim (the script refuses anything else), so a capture can trim real work but never invent it. Keep tool output out.
3. `python3 helpers/session_capture.py spec.json out/` writes bare records (no cwd, ids or metadata), runs the privacy scan, renders with claude-replay 0.11.0 (MIT, es617/claude-replay) inside a no-network sandbox, and captures `session.png` at device scale 2 with `layout.js` (block and line boxes).
4. The privacy scan stops on emails, home and Drive paths, Notion, Google and claude.ai links, phone numbers, SSN shapes, patient identifiers (MRN, DOB), key shapes, and any name in the private denylist. Point `"denylist"` in the spec (or `$BROLL_DENYLIST`) at the private list in the claude-skills repo, `plugins/serious-ai/hooks/session_capture_denylist.txt`, and add every new client or prospect to it. No PHI and no client data ever goes into a capture: pick sessions about Serious AI's own tooling.
5. In the scene, `BK.frame` the capture, `BK.typeLines` it in, glide `BK.cursor` to the line, `BK.popout` the line and sweep `BK.marker` as Chris says it. Tag it with the session date in the eyebrow.

For a screen recording Chris makes himself (a live demo), the open-source Screen Studio alternatives found on November 26, 2026 are OpenScreen (MIT, about 40,000 stars, macOS, Windows, Linux) and Reframed (MIT, macOS, spring-smoothed cursor, click-cluster auto-zoom). Both are desktop apps for recording, not for this pipeline.

## Whiteboard scenes

The newsletter's hand-drawn graphics (navy ink on off-white, orange hachure on the key panel, an orange swoosh under the key word, all-caps lettering) carry into video as whiteboard scenes: `BK.sketch` shapes on `#F7F7F7`, labels in Montserrat 800 caps (not a handwriting face: two fonts per asset), strokes drawn on as Chris names each thing, a gentle camera push toward the part he is talking about. Use them for a system or a flow (where the work runs, what checks it), and the dark navy stage for numbers, quotes and captures.

## The weekly loop and the feedback ledger

After each episode's gate, Chris's comments on the B-roll come back here, the way RoboNuggets' review comments teach his animate skill.

1. Log each comment in the ledger below with the date and episode.
2. If the rule is mechanical (a word, a number, a position, a color, a duration), add it to `helpers/broll_lint.py` with an id and a test in `tests/test_broll.py`, and cite the id in the ledger. Do not also write it as prose.
3. If it is judgment, write it into the house style above, merged with what is there, and delete what it replaces.
4. Each new episode renders, runs the lint, and is checked against the house style before the gate.

| Date | Episode | Feedback | Where it lives |
|---|---|---|---|
| November 11, 2026 | Ep 333 | The three parts get three distinct visuals (dials), not one triangle | House style 5 (judgment) |
| November 17, 2026 | Ep 334 | Content stays inside the left 1560 of 1920 so the head card covers nothing | B5 |
| November 25, 2026 | Ep 335 | Every number on screen is one Chris says on camera | B1 |
| November 25, 2026 | Ep 335 | The warm beats stay on his face | House style 9 (judgment) |
| November 26, 2026 | standing | Show the real work when he mentions it, never client data or PHI | Session capture, `session_scan.py` |
| November 26, 2026 | standing | No em dashes or banned words, brand colors and fonts only | B2, B4 |

