<p align="center">
  <img src="static/video-use-banner.png" alt="video-use" width="100%">
</p>

# video-use

Introducing **video-use** — edit videos with Claude Code. 100% open source.

Drop raw footage in a folder, chat with Claude Code, get `final.mp4` back. Works for any content — talking heads, montages, tutorials, travel, interviews — without presets or menus.

Try video-use in [Browser Use Cloud](https://cloud.browser-use.com/v4?utm_campaign=video-use-use-in-cloud&utm_source=github).

## What it does

- **Cuts out filler words** (`umm`, `uh`, false starts) and dead space between takes
- **Auto color grades** every segment (warm cinematic, neutral punch, or any custom ffmpeg chain)
- **30ms audio fades** at every cut so you never hear a pop
- **Burns subtitles** in your style — 2-word UPPERCASE chunks by default, fully customizable
- **Generates animation overlays** via [HyperFrames](https://github.com/heygen-com/hyperframes), [Remotion](https://www.remotion.dev/), [Manim](https://www.manim.community/), or PIL — spawned in parallel sub-agents, one per animation
- **Self-evaluates the rendered output** at every cut boundary before showing you anything
- **Persists session memory** in `project.md` so next week's session picks up where you left off

## Setup prompt

Paste into Claude Code, Codex, Hermes, Openclaw, or any agent with shell access:

```text
Set up https://github.com/browser-use/video-use for me.

Read install.md first to install this repo, wire up ffmpeg, register the skill with whichever agent you're running under, and set up the ElevenLabs API key — ask me to paste it when you need it. Then read SKILL.md for daily usage, and always read helpers/ because that's where the editing scripts live. After install, don't transcribe anything on your own — just tell me it's ready and wait for me to drop footage into a folder.
```

The agent handles the clone, dependencies, skill registration, and prompts you once for your ElevenLabs API key (grab one at [elevenlabs.io/app/settings/api-keys](https://elevenlabs.io/app/settings/api-keys)).

Then point your agent at a folder of raw takes:

```bash
cd /path/to/your/videos
claude    # or codex, hermes, etc.
```

For always-on editing from your own VPS or Telegram, run the agent through [Browser Use Box](https://browser-use.com/bux). [Watch the 15-second demo](https://www.tiktok.com/@browser_use/video/7639824093721758989).

And in the session:

> edit these into a launch video

It inventories the sources, proposes a strategy, waits for your OK, then produces `edit/final.mp4` next to your sources. All outputs live in `<videos_dir>/edit/` — the skill directory stays clean.

## Manual install

If you'd rather do it by hand:

```bash
# 1. Clone and symlink into your agent's skills directory
git clone https://github.com/browser-use/video-use ~/Developer/video-use
ln -sfn ~/Developer/video-use ~/.claude/skills/video-use        # Claude Code
# ln -sfn ~/Developer/video-use ~/.codex/skills/video-use       # Codex

# 2. Install deps
cd ~/Developer/video-use
uv sync                         # or: pip install -e .
brew install ffmpeg             # required
brew install yt-dlp             # optional, for downloading online sources

# 3. Add your ElevenLabs API key
cp .env.example .env
$EDITOR .env                    # ELEVENLABS_API_KEY=...
```

## Setup troubleshooting

Use this checklist when the setup prompt or manual install gets stuck. Most install failures come from a missing system tool, running the command from the wrong directory, or an unset API key.

### Prerequisites

- **Install `ffmpeg` first.** The editing helpers shell out to `ffmpeg`/`ffprobe` for media inspection and rendering.
- On macOS, install it with `brew install ffmpeg`.
- On Ubuntu/Debian, install it with `sudo apt-get update && sudo apt-get install -y ffmpeg`.
- **Verify `ffmpeg` is on your `PATH`.** Run `ffmpeg -version` and `ffprobe -version` in the same terminal your agent uses.
- If either command is missing after installation, restart the shell or fix `PATH` before editing footage.
- **Use Python 3.10 or newer.** The package metadata requires Python 3.10+, so check with `python --version` or `python3 --version`.
- **Use `uv` or `pip` for Python dependencies.** From the repository root, prefer `uv sync`.
- If you do not use `uv`, run `python -m pip install -e .` from the repository root.
- **Install `uv` if needed.** If `uv sync` prints `command not found`, install it from [docs.astral.sh/uv](https://docs.astral.sh/uv/) or use the `pip` command above instead.
- **Optional download support needs `yt-dlp`.** Install it only if you want to pull online videos into your source folder; local-file editing does not require it.
- **Animation extras are optional.** Manim/Remotion/HyperFrames/PIL overlays may need additional toolchains, but the core transcript-and-cut workflow only needs the base Python dependencies, `ffmpeg`, and the API key.

### Environment and agent setup

- **Create a local `.env`.** Copy `.env.example` to `.env` in the repository root; do not commit your filled-in `.env` file.
- **Set `ELEVENLABS_API_KEY`.** Transcription requires an ElevenLabs key. Put it in `.env` as `ELEVENLABS_API_KEY=your_key_here`, or export it in the shell before running helpers.
- **Keep secrets out of prompts and commits.** Ask the agent to read the key from `.env` or the environment instead of pasting it into a reusable prompt or checked-in file.
- **Run commands from the repo root.** `uv sync`, `pip install -e .`, and skill registration commands assume the current directory is the cloned `video-use` repository.
- **Check the skill symlink target.** If your agent cannot find `video-use`, confirm the symlink points at this repository.
- For Claude Code, a typical target is `~/.claude/skills/video-use -> ~/Developer/video-use`.
- For Codex, use the matching skills directory shown in the manual install comments above.
- **Restart your agent after registration.** Some agents only scan skills at startup, so a fresh session may be needed before `video-use` appears.

### Quick smoke checks

- Run `python -c "import requests, librosa, matplotlib, PIL, numpy; print('deps ok')"` to catch missing Python dependencies early.
- Run `ffmpeg -version` to confirm the renderer is available.
- Run `ffprobe -version` to confirm media probing is available.
- If the dependency check fails, rerun `uv sync` or `python -m pip install -e .` from the repo root.
- If transcription fails immediately, re-check that `ELEVENLABS_API_KEY` is present in `.env` or exported in the same shell your agent uses.

## How it works

The LLM never watches the video. It **reads** it — through two layers that together give it everything it needs to cut with word-boundary precision.

<p align="center">
  <img src="static/timeline-view.svg" alt="timeline_view composite — filmstrip + speaker track + waveform + word labels + silence-gap cut candidates" width="100%">
</p>

**Layer 1 — Audio transcript (always loaded).** One ElevenLabs Scribe call per source gives word-level timestamps, speaker diarization, and audio events (`(laughter)`, `(applause)`, `(sigh)`). All takes pack into a single ~12KB `takes_packed.md` — the LLM's primary reading view.

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
Transcribe ──> Pack ──> LLM Reasons ──> EDL ──> Render ──> Self-Eval
                                                              │
                                                              └─ issue? fix + re-render (max 3)
```

The self-eval loop runs `timeline_view` on the _rendered output_ at every cut boundary — catches visual jumps, audio pops, hidden subtitles. You see the preview only after it passes.

## Design principles

1. **Text + on-demand visuals.** No frame-dumping. The transcript is the surface.
2. **Audio is primary, visuals follow.** Cuts come from speech boundaries and silence gaps.
3. **Ask → confirm → execute → self-eval → persist.** Never touch the cut without strategy approval.
4. **Zero assumptions about content type.** Look, ask, then edit.
5. **12 hard rules, artistic freedom elsewhere.** Production-correctness is non-negotiable. Taste isn't.

See [`SKILL.md`](./SKILL.md) for the full production rules and editing craft.
