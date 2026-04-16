<p align="center">
  <img src="static/video-use-banner.png" alt="video-use" width="100%">
</p>

# video-use

Introducing **video-use** — edit videos with Claude Code. 100% open source.

Drop raw footage in a folder, chat with Claude Code, get `final.mp4` back. Works for any content — talking heads, montages, tutorials, travel, interviews — without presets or menus.

## What it does

- **Cuts out filler words** (`umm`, `uh`, false starts) and dead space between takes
- **Auto color grades** every segment (warm cinematic, neutral punch, or any custom ffmpeg chain)
- **30ms audio fades** at every cut so you never hear a pop
- **Burns subtitles** in your style — 2-word UPPERCASE chunks by default, fully customizable
- **Generates animation overlays** via [Manim](https://www.manim.community/), [Remotion](https://www.remotion.dev/), or PIL — spawned in parallel sub-agents, one per animation
- **Self-evaluates the rendered output** at every cut boundary before showing you anything
- **Persists session memory** in `project.md` so next week's session picks up where you left off

## Get started

```bash
# 1. Clone and symlink into Claude Code's skills directory
git clone https://github.com/browser-use/video-use
cd video-use
ln -s "$(pwd)" ~/.claude/skills/video-use

# 2. Install deps
pip install -e .
brew install ffmpeg           # required
brew install yt-dlp            # optional, for downloading online sources

# 3. Transcription backend (pick one)
#    Option A — ElevenLabs Scribe (cloud, fast, includes audio event tags):
cp .env.example .env
$EDITOR .env                   # ELEVENLABS_API_KEY=...
#    Option B — WhisperX (local, no API key needed):
pip install -e ".[whisperx]"

# 4. (Optional) Remotion Studio for instant preview — no render wait
cd remotion && npm install && cd ..
```

> **Backend auto-detection:** if `ELEVENLABS_API_KEY` is set, ElevenLabs is used. Otherwise WhisperX runs locally. Override with `--backend whisperx` or `--backend elevenlabs`.

Then point Claude Code at a folder of raw takes:

```bash
cd /path/to/your/videos
claude
```

And in the session:

> edit these into a launch video

It inventories the sources, proposes a strategy, waits for your OK, then produces `edit/final.mp4` next to your sources. All outputs live in `<videos_dir>/edit/` — the skill directory stays clean.

## How it works

The LLM never watches the video. It **reads** it — through two layers that together give it everything it needs to cut with word-boundary precision.

<p align="center">
  <img src="static/timeline-view.svg" alt="timeline_view composite — filmstrip + speaker track + waveform + word labels + silence-gap cut candidates" width="100%">
</p>

**Layer 1 — Audio transcript (always loaded).** One transcription pass per source (ElevenLabs Scribe cloud or WhisperX local) gives word-level timestamps, speaker diarization, and optionally audio events. All takes pack into a single ~12KB `takes_packed.md` — the LLM's primary reading view.

```
## C0103  (duration: 43.0s, 8 phrases)
  [002.52-005.36] S0 Ninety percent of what a web agent does is completely wasted.
  [006.08-006.74] S0 We fixed this.
```

**Layer 2 — Visual composite (on demand).** `timeline_view` produces a filmstrip + waveform + word labels PNG for any time range. Called only at decision points — ambiguous pauses, retake comparisons, cut-point sanity checks.

> Naive approach: 30,000 frames × 1,500 tokens = **45M tokens of noise**.
> Video Use: **12KB text + a handful of PNGs**.

Same idea as browser-use giving an LLM a structured DOM instead of a screenshot — but for video.

## Pipeline

```
Transcribe ──> Pack ──> LLM Reasons ──> EDL ──┬──> Remotion Studio (instant preview)
                                               │
                                               └──> Render ──> Self-Eval
                                                                  │
                                                                  └─ issue? fix + re-render (max 3)
```

The self-eval loop runs `timeline_view` on the _rendered output_ at every cut boundary — catches visual jumps, audio pops, hidden subtitles. You see the preview only after it passes.

## Remotion Studio preview

Skip the render wait — preview your edit instantly in the browser:

```bash
cd remotion
npm run setup       # copies source videos into public/
npm run studio      # opens http://localhost:3000
```

Remotion Studio reads `edit/edl.json` and sequences the clips with live subtitles. Scrub anywhere, adjust timing in the EDL, and the studio hot-reloads. Only do the full ffmpeg render once you're happy with the cut.

## Design principles

1. **Text + on-demand visuals.** No frame-dumping. The transcript is the surface.
2. **Audio is primary, visuals follow.** Cuts come from speech boundaries and silence gaps.
3. **Ask → confirm → execute → self-eval → persist.** Never touch the cut without strategy approval.
4. **Zero assumptions about content type.** Look, ask, then edit.
5. **12 hard rules, artistic freedom elsewhere.** Production-correctness is non-negotiable. Taste isn't.

See [`SKILL.md`](./SKILL.md) for the full production rules and editing craft.
