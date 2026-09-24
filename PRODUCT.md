# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary:** Creators and founders who edit talking-head, tutorial, social, and personal video through Claude Code or similar coding agents—not timeline GUIs.

**Secondary:** Agencies and editors batching client work in agent workflows; developers embedding video-use as a library or pipeline skill.

**Job:** Turn raw footage into a finished `final.mp4` by conversation: cut, grade, subtitle, overlay, self-evaluate—without learning NLE software or hand-authoring filtergraphs.

## Product Purpose

video-use is an open-source video editing skill for AI coding agents. Drop footage in a folder, chat, get a production-correct edit.

Success means: correct audio/video boundaries, clean cuts, burned subtitles and overlays when asked, and a reproducible `edit/` workspace the agent can resume—without the model watching raw video frame-by-frame.

## Positioning

**Text + on-demand vision, not frame dumping.** The agent reasons over packed transcripts (word-level ASR) and pulls timeline composite PNGs only at decision points. Neighboring “AI video” tools either require a GUI, stream entire videos into the model, or ship preset templates. video-use is content-agnostic, agent-native, and production-rule hard.

## Operating Context

- Runs inside agent harnesses (Claude Code, Codex, Cursor, Warp/Oz, etc.) via `SKILL.md` + Python helpers.
- Typical loop: Transcribe → Pack → Reason → EDL → Render → Self-eval (≤3 loops) → Persist.
- User confirms strategy before execution; artistic direction is free, production rules are not.
- Depends on ffmpeg, ElevenLabs (Scribe) for word-level ASR, optional HyperFrames/Remotion/Manim for overlays.
- Outputs live under `<videos_dir>/edit/` (`takes_packed.md`, `edl.json`, `master.srt`, graded clips, animations, `project.md`).

## Capabilities and Constraints

**Capabilities (confirmed):**

- Filler-word and dead-space cutting from word-level transcripts
- Per-segment color grade; lossless concat with 30ms audio fades at boundaries
- Burned subtitles (default 2-word UPPERCASE; customizable)
- Parallel overlay generation (HyperFrames / Remotion / Manim / PIL)
- Self-evaluation at cut boundaries before presenting to the user
- Persistent project memory in `project.md`

**Hard constraints (non-negotiable — from SKILL.md):**

1. Subtitles applied last, after overlays  
2. Per-segment extract + lossless `-c copy` concat (no single-pass filtergraph)  
3. 30ms audio fades at every cut boundary  
4. Overlay PTS via `setpts=PTS-STARTPTS+T/TB`  
5. Master SRT uses output-timeline offsets  
6. Never cut inside a word (snap to word boundaries)  
7. Pad cut edges 30–200ms  
8. Word-level verbatim ASR only  
9. Cache transcripts per source  
10. Parallel sub-agents for animations  
11. Strategy confirmation before execution  
12. All outputs under `<videos_dir>/edit/`

**Architecture constraints:**

- LLM must not watch full video; use packed transcript + on-demand `timeline_view` composites  
- Open source; no content-type presets  

**Undecided / out of scope for this record:**

- First-class web dashboard or marketing site (none required for core skill)  
- Hosted SaaS offering  

## Brand Commitments

- Name: **video-use** (browser-use ecosystem naming pattern)  
- Tagline posture: edit videos with agents; 100% open source  
- Visual assets on hand: `static/video-use-banner.png`, `static/timeline-view.svg`, `poster.html`  
- Voice: direct, technical, craft-first; production correctness over flashy defaults  
- No binding corporate palette/type system beyond repo assets (design world not locked here)

## Evidence on Hand

- `README.md`, `SKILL.md` — product + production rules  
- `helpers/` — `transcribe.py`, `pack_transcripts.py`, `timeline_view.py`, `render.py`, `grade.py`, …  
- `static/video-use-banner.png`, `static/timeline-view.svg`  
- `poster.html`  
- Upstream: https://github.com/browser-use/video-use  

Do **not** fabricate testimonials, benchmarks, pricing, or customer logos.

## Product Principles

1. **Production correctness is law; art is free.** The twelve hard rules never yield to aesthetics.  
2. **Read text, glance at composites—never drown the model in frames.**  
3. **Confirm strategy, then execute.** No silent multi-minute renders without a plan the user saw.  
4. **Agent-native workspace.** Everything durable lives in `edit/` and `project.md` for the next session.  
5. **Content-agnostic.** No genre presets; the conversation supplies brand, palette, and pacing.

## Accessibility & Inclusion

No product-specific WCAG target for a GUI (core product is CLI/agent). When UI surfaces are added (poster, future web), prefer captions/subtitles as first-class output and readable timeline composites for human review.
