# Shape brief: Project Review Console

**Status:** proposed (shape only — no implementation)
**Visitor mode:** Operate
**Visual authority:** DESIGN.md — The Harness Console (incumbent)
**Named target:** first interactive web surface for a human reviewing an agent edit session
**Source truth:** PRODUCT.md, SKILL.md `edit/` layout, poster.html metaphor

## 1. Job and audience
- **Who:** Creator/founder (primary) or agency editor mid-session with Claude/Codex/Warp after an agent proposed cuts.
- **Situation:** Footage already transcribed/packed; agent has or is about to write `edl.json` / render; human must *confirm strategy* and inspect what the model "saw" without opening an NLE.
- **Need:** Trust the plan and the composites quickly; approve, nudge, or reject before a long render.
- **Success:** Human understands take layout, proposed cuts, and can greenlight or send one clear correction—without re-watching full raw video.

## 2. Outcome and proof
- **Primary action:** Confirm or revise the edit strategy (PRODUCT principle 3).
- **Proof on screen:** real paths under `<videos_dir>/edit/` — `takes_packed.md` summary, `timeline_view` PNG(s), EDL cut list, optional `project.md` notes. No fabricated metrics.
- **Product-specific truth:** text + on-demand composites; never imply the model watched pixels end-to-end.

## 3. Selected direction
- **Visual authority:** preserve DESIGN.md dual surface (stone desk + dark console); accent `#ff6b35` for attention/approve; blue `#6b9fff` only for audio/word channel.
- **Structural thesis:** single-sheet "session brief" with a nested dark **instrument panel** for the active composite (same hierarchy as poster header → insight → timeline stage).
- **Sequence:** (1) session header + status (2) strategy block (human-confirm) (3) composite stage (4) cut list / transcript excerpt (5) actions.
- **Focal moment:** the on-demand composite panel labeled in mono (`timeline_view.py — …`) with ON-DEMAND badge.
- **Implementation consequence:** static-first HTML/CSS OK for v0; later wire to `edit/` files. No full Premiere clone.

## 4. Scope and boundaries
- **Fidelity:** production-ready *single screen* mock that can open real local files later; not a multi-route app.
- **Breadth:** one session/project view; not library browser, not settings, not account.
- **Interactivity v0:** scroll + approve/request-changes affordances (can be non-functional buttons in first build).
- **Untouched:** SKILL.md hard rules, Python helpers, render pipeline, agent loop.
- **Anti-goals:** marketing landing page; purple AI aesthetics; dumping full video player as the hero; light-only admin CRUD.

## 5. States and ranges
- **Empty:** no `edit/` yet — explain drop footage + run agent; show skeleton console.
- **Loading:** transcript/pack in progress — mono status line, no fake percentages unless real.
- **Ready for confirm:** strategy text + at least one composite path available.
- **Error:** missing ASR key / ffmpeg — plain error in insight bar pattern (stone-800 strip).
- **Ranges:** 1–20 takes typical; packed transcript can be long — show excerpt + "open takes_packed.md".
- **Overflow:** cut list virtualize/scroll inside panel; don't grow page infinitely without structure.

## 6. Interaction and layout
- **Hierarchy:** Confirm strategy (top) > see composite (middle) > inspect cuts/words (below) > secondary links to files.
- **Topology:** single column sheet max ~1400px; dark panels full width inside sheet padding 40px.
- **Affordances:** primary = Approve strategy (accent scarce); secondary = Request changes; tertiary = open file paths (mono links).
- **Feedback:** status chip in header (DRAFT STRATEGY / APPROVED / RENDERING) using stone + accent, not traffic-light semantics on decorative dots.
- **Responsive:** collapse side-by-side cut list under composite; keep mono readable (min ~12px on mobile or horizontal scroll composite).

## 7. Constraints and open decisions
- **Platform:** web (local file or static export); a11y: don't rely on color alone for approve state; composites need text alternative (cut summary).
- **Open:** exact data binding API (watch folder vs. manual path paste); whether v0 ships inside repo as `review.html` next to `poster.html`.
- **Builder must not invent:** testimonials, fake WPM/engagement scores, second brand hue, NLE ribbon chrome.

## Confirm
Reply with corrections or "brief approved" to proceed to implementation (`new-work` / build). Shape stops here — no code in this step.
