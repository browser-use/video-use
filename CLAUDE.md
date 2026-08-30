# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`video-use` is a Claude Code **skill**, not an application. It teaches an agent to edit raw video footage into a finished cut through conversation — transcribe, cut, color grade, generate overlay animations, burn subtitles. There is no server, no build step, no test suite. The "product" is `SKILL.md` (the behavioral spec the agent reads) plus a handful of Python helper scripts that do the actual media work.

This repo is normally symlinked into an agent's skills directory (`~/.claude/skills/video-use`) and invoked from *inside a user's footage folder*, not from this repo. When working *on* this repo (adding/fixing helpers, editing `SKILL.md`), you're editing the tool itself, not producing a video.

## Setup / commands

```bash
uv sync                # or: pip install -e .
brew install ffmpeg     # hard requirement (ffmpeg + ffprobe on PATH)
brew install yt-dlp     # optional, only for URL sources
```

No console-script entry points — helpers are invoked directly:

```bash
python helpers/transcribe.py <video> [--num-speakers N]
python helpers/transcribe_batch.py <videos_dir> [--workers 4] [--num-speakers N]
python helpers/pack_transcripts.py --edit-dir <dir> [--silence-threshold 0.5] [-o out.md]
python helpers/timeline_view.py <video> <start> <end> [-o out.png] [--n-frames 10]
python helpers/grade.py <input> -o <output> [--preset NAME | --filter '<raw ffmpeg>'] [--list-presets] [--analyze <clip>]
python helpers/render.py <edl.json> -o <output> [--preview | --draft] [--build-subtitles] [--no-subtitles] [--no-loudnorm]
```

There is no lint config and no test suite in this repo — don't invent one. Sanity-check a helper change by running it against a real clip and inspecting the output (ffprobe duration/dimensions, or opening the PNG/MP4).

`ELEVENLABS_API_KEY` must be set in `.env` at the repo root (see `install.md`) — Scribe (ElevenLabs) does all transcription. Never commit `.env` or echo the key.

## Architecture

**Two-layer reading model, not frame-dumping.** The core idea (see README "How it works"): the LLM never watches the video, it reads a text transcript and only pulls visuals on demand.

1. **Layer 1 — transcript (always loaded).** `transcribe.py`/`transcribe_batch.py` call ElevenLabs Scribe for word-level timestamps, speaker diarization, and audio events, cached as `transcripts/<name>.json`. `pack_transcripts.py` compresses all of these into one `takes_packed.md` (phrase-level, breaks on ≥0.5s silence or speaker change) — this is the primary artifact an editing agent reasons over.
2. **Layer 2 — visual composite (on demand).** `timeline_view.py` renders a filmstrip + waveform + word-label PNG for an arbitrary time range. Called only at decision points (ambiguous cuts, retake comparisons, self-eval), never as a scan.

**Pipeline:** `Transcribe → Pack → LLM reasons over takes_packed.md → EDL (edl.json) → render.py → self-eval (timeline_view on the rendered output) → iterate`.

**`edl.json`** is the intermediate cut decision list (sources, time ranges with reasons, grade, overlays, subtitles path) — see the "EDL format" section of `SKILL.md` for the schema. `render.py` consumes it and:
- extracts each segment with grade + 30ms audio fades baked in,
- lossless `-c copy` concats into `base.mp4`,
- composites overlays (PTS-shifted so frame 0 aligns to the overlay window) and applies subtitles **last** in the filter chain,
- two-pass loudnorm's the final output (-14 LUFS) unless `--no-loudnorm`.

**Animations** are generated outside this repo's Python helpers, per overlay slot, using HyperFrames, Remotion, Manim, or PIL — chosen per-animation, not globally. Multiple animation slots are always built by parallel sub-agents (`Agent` tool), never sequentially. `skills/manim-video/` is a vendored sub-skill with its own `SKILL.md` and reference docs for Manim-specific work.

**Everything session-related is written outside this repo**, under `<videos_dir>/edit/` next to the user's footage (`project.md` memory, `takes_packed.md`, `edl.json`, `transcripts/`, `animations/slot_<id>/`, `clips_graded/`, `master.srt`, `preview.mp4`, `final.mp4`). This repo directory (`video-use/`) must stay clean — the `.gitignore` treats those artifact names as a safety net in case the skill is ever invoked from inside this repo by mistake.

## The 12 hard rules

`SKILL.md` defines 12 non-negotiable "Hard Rules" for production correctness (subtitle ordering, per-segment extract before concat, 30ms fades, PTS-shifted overlays, output-timeline subtitle offsets, word-boundary cuts, cut padding, word-level ASR only, transcript caching, parallel animation sub-agents, strategy confirmation before execution, outputs confined to `<videos_dir>/edit/`). If you change a helper script (`render.py`, `grade.py`, etc.), re-check it against these rules — they exist because violating them produces silent failures (pops, hidden captions, misaligned overlays), not just suboptimal output. Everything else in `SKILL.md` is a worked example / taste, not a rule.

## Editing `SKILL.md` vs `helpers/`

- `SKILL.md` is behavioral guidance for the agent (when to ask, what to propose, how to structure sub-agent briefs) — prose changes here affect how future agent sessions behave, not this repo's code.
- `helpers/*.py` are the actual media-processing implementation `SKILL.md` refers to by bare filename (`transcribe.py`, `render.py`, ...). They're resolved relative to the directory containing `SKILL.md`, so keep `helpers/` and `SKILL.md` as siblings — don't relocate one without the other.
- `install.md` is a separate one-time setup flow (clone, deps, ffmpeg, skill registration, API key) — distinct from `SKILL.md`'s daily-use flow. Don't merge them.
