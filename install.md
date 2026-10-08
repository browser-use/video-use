---
name: video-use-install
description: Install video-use into the current agent (Claude Code, Codex, Hermes, Openclaw, etc.) and wire up ffmpeg plus transcription (an ElevenLabs API key or the local Whisper engine) so the user can start editing immediately.
---

# video-use install

Use this file only for first-time install or reconnect. For daily editing, read `SKILL.md`. Always read `helpers/` — that's where the scripts live.

## What you're doing

You're setting up a conversation-driven video editor for the user. After install, the user drops raw footage into any folder, runs their agent (`claude`, `codex`, etc.) there, and says "edit these into a launch video." You do the rest by reading `SKILL.md`.

Three things must exist on this machine:

1. The `video-use` repo cloned somewhere stable.
2. `ffmpeg` on `$PATH` (plus optional `yt-dlp` for online sources).
3. Transcription configured in `.env` at the repo root: an ElevenLabs API key for Scribe, or `VIDEO_USE_TRANSCRIBER=local` with one Whisper library installed.

And one thing must be true about the current agent:

4. It can discover `SKILL.md` — either via a global skills directory (`~/.claude/skills/`, `~/.codex/skills/`) or via a `CLAUDE.md` / system-prompt import.

## Install prompt contract

- Do everything yourself. Only ask the user for things you cannot generate — the choice between an ElevenLabs key and local transcription, the key itself, and confirmation before `brew install` or a model download.
- Prefer a stable clone path like `~/Developer/video-use` (not `/tmp`, not `~/Downloads`).
- The skill references helpers by bare name (`transcribe.py`, `render.py`). That works because SKILL.md and `helpers/` ship together — keep them as siblings when you register the skill.
- After install, verify by running one real command against one real file. Don't declare success on file-existence checks alone.

## Steps

### 1. Clone to a stable path

```bash
test -d ~/Developer/video-use || git clone https://github.com/browser-use/video-use ~/Developer/video-use
cd ~/Developer/video-use
```

If the repo is already there, `git pull --ff-only` and continue.

### 2. Install Python deps

```bash
# Prefer uv if available; fall back to pip.
command -v uv >/dev/null && uv sync || pip install -e .
```

`pyproject.toml` lists `requests`, `librosa`, `matplotlib`, `pillow`, `numpy`. No console scripts — helpers are invoked directly as `python helpers/<name>.py`.

### 3. Install ffmpeg (+ optional yt-dlp)

`ffmpeg` and `ffprobe` are hard requirements. `yt-dlp` is only needed if the user wants to pull sources from URLs. Animation engines such as HyperFrames, Remotion, and Manim are installed lazily the first time a project actually needs them.

```bash
# macOS
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v yt-dlp >/dev/null || brew install yt-dlp     # optional

# Debian / Ubuntu
# sudo apt-get update && sudo apt-get install -y ffmpeg
# pip install yt-dlp

# Arch
# sudo pacman -S ffmpeg yt-dlp
```

If `brew` / `apt` / `pacman` requires a sudo prompt, tell the user the exact command and wait. Do not invent a password.

### 4. Register the skill with the current agent

Figure out which agent you are running under, and register once. A symlink of the whole repo directory is the right shape — helpers/ needs to sit next to SKILL.md.

- **Claude Code** (`~/.claude/` present):

    ```bash
    mkdir -p ~/.claude/skills
    ln -sfn ~/Developer/video-use ~/.claude/skills/video-use
    ```

- **Codex** (`$CODEX_HOME` set, or `~/.codex/` present):

    ```bash
    mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
    ln -sfn ~/Developer/video-use "${CODEX_HOME:-$HOME/.codex}/skills/video-use"
    ```

- **Hermes / Openclaw / another agent with a skills directory**: symlink `~/Developer/video-use` into that agent's skills directory under the name `video-use`. If the agent has no skills directory, add a line to its system prompt / config pointing at `~/Developer/video-use/SKILL.md` (e.g. an `@~/Developer/video-use/SKILL.md` import in a `CLAUDE.md`-equivalent).

If you can't tell which agent you're in, ask the user once: "which agent am I running under — Claude Code, Codex, or something else?" Then pick the right target.

### 5. Transcription: ElevenLabs key or local engine

Every edit starts from a word-level transcript. Two engines can produce it:

- **ElevenLabs Scribe** (hosted, needs an API key): word timestamps, speaker labels, audio events such as `(laughter)`, verbatim fillers. Best quality; costs credits.
- **Local Whisper** (`helpers/local_stt.py`, no key): word timestamps only, runs on the user's machine after a one-time ~1.6 GB model download. No speaker labels or audio events; fillers are best effort.

Configuration is one line in `~/Developer/video-use/.env`: either `ELEVENLABS_API_KEY=...` or `VIDEO_USE_TRANSCRIBER=local`. Values in `.env` win over exported environment variables, and every transcription run prints which engine it used and why, so the choice is never silent.

1. Check existing state in this order and stop at the first hit:

    ```bash
    # a) local engine already chosen in .env or exported (either one wins over a key at runtime)
    grep -q '^VIDEO_USE_TRANSCRIBER=local' ~/Developer/video-use/.env 2>/dev/null && echo "local (.env)"
    [ "$VIDEO_USE_TRANSCRIBER" = "local" ] && echo "local (env)"
    # b) key already exported
    [ -n "$ELEVENLABS_API_KEY" ] && echo "env"
    # c) .env at repo root already has a key
    grep -q '^ELEVENLABS_API_KEY=..' ~/Developer/video-use/.env 2>/dev/null && echo "dotenv"
    ```

2. If nothing is set, ask the user exactly once:

    > Transcription needs either an ElevenLabs API key (best quality: word timestamps, speaker labels, laughter/applause tags — grab one at https://elevenlabs.io/app/settings/api-keys and paste it here) or a local Whisper model (free, runs on this machine, word timestamps only, one-time ~1.6 GB download). Which do you want? If you already have the key exported as `ELEVENLABS_API_KEY`, say "use env".

3. **If the user pastes a key**, write it to `~/Developer/video-use/.env`:

    ```bash
    printf 'ELEVENLABS_API_KEY=%s\n' "$KEY" > ~/Developer/video-use/.env
    chmod 600 ~/Developer/video-use/.env
    ```

    Never echo the key back in tool output. Never commit `.env`. Then sanity check with a cheap, quota-free call:

    ```bash
    curl -s -o /dev/null -w '%{http_code}\n' \
      -H "xi-api-key: $(sed -n 's/^ELEVENLABS_API_KEY=//p' ~/Developer/video-use/.env)" \
      https://api.elevenlabs.io/v1/user
    ```

    `200` means the key works. `401` means the user pasted a wrong/expired key — ask once more and stop. Anything else (network, 5xx), move on and verify during first real transcription.

4. **If the user chooses local**, probe the machine first so only the right library gets installed:

    ```bash
    cd ~/Developer/video-use && python helpers/local_stt.py probe
    ```

    The probe prints the library for this machine (`mlx-whisper` on Apple Silicon, `faster-whisper` on CPU-only or NVIDIA), whether it is already installed, free disk, the model size, and the exact install command. Tell the user the download size, then after confirmation run the printed command — one of:

    ```bash
    uv sync --extra stt-mlx        # Apple Silicon   (or: pip install -e '.[stt-mlx]')
    uv sync --extra stt-cpu        # CPU-only        (or: pip install -e '.[stt-cpu]')
    uv sync --extra stt-cuda       # NVIDIA: adds the CUDA 12 cuBLAS and cuDNN 9 wheels faster-whisper needs on the GPU
    ```

    On an NVIDIA machine without those runtime libraries the engine still works, on the CPU, and prints the `stt-cuda` command.

    Then persist the choice:

    ```bash
    printf 'VIDEO_USE_TRANSCRIBER=local\n' >> ~/Developer/video-use/.env
    chmod 600 ~/Developer/video-use/.env
    ```

    The model weights download on the first transcription into the standard Hugging Face cache (`~/.cache/huggingface`, or `HF_HOME`), pinned to a fixed revision. Warn the user that the first clip takes a few extra minutes.

### 6. Verify end-to-end

Run one real thing. Prefer the lightest verification that still proves the pipeline is wired up:

```bash
python ~/Developer/video-use/helpers/timeline_view.py --help >/dev/null && echo "helpers OK"
ffprobe -version | head -1
```

A Scribe transcription test is optional at install time — it burns credits. Better to wait until the user hands you their first clip.

If the local engine was chosen, prove it once on a synthetic clip instead — no credits involved. This first run downloads the ~1.6 GB model weights, so ask the user to confirm the download before running it (the probe already showed the size and free disk). Pick the interpreter that matches step 2: `uv run --no-sync python` when the deps came from `uv sync` (`--no-sync` keeps the extra you just installed), or plain `python` from the environment where `pip install -e` ran.

```bash
cd ~/Developer/video-use
PY="uv run --no-sync python"        # pip path: PY=python
say -o /tmp/vu_check.aiff "local transcription check one two three" \
  && ffmpeg -y -loglevel error -f lavfi -i color=c=black:s=320x240:r=25 -i /tmp/vu_check.aiff \
       -shortest -c:v libx264 -c:a aac /tmp/vu_check.mp4 \
  && $PY helpers/transcribe.py /tmp/vu_check.mp4 --edit-dir /tmp/vu_check_edit \
  && $PY helpers/pack_transcripts.py --edit-dir /tmp/vu_check_edit \
  && cat /tmp/vu_check_edit/takes_packed.md
```

`say` is macOS only; on Linux skip the synthetic clip and verify on the user's first real file, again after confirming the download.

### 7. Hand off

Tell the user, in one short message:

- Where the skill is installed (`~/Developer/video-use`).
- That they should `cd` into their footage folder and start their agent there (e.g. `claude`).
- That a good first message is: *"edit these into a launch video"* or *"inventory these takes and propose a strategy."*
- That all outputs land in `<videos_dir>/edit/` — the repo stays clean.

## Keeping the skill current

- `cd ~/Developer/video-use && git pull --ff-only` pulls the latest code. The symlink auto-picks it up on the next run.
- If `pyproject.toml` changed deps, re-run `uv sync` / `pip install -e .` after pulling.

## Cold-start reminders

- Symlink the **whole directory**, not just `SKILL.md`. The helpers need to sit next to it.
- If `.env` exists but the key is empty, treat it the same as missing — don't assume existence means validity.
- `ffmpeg` from static builds works fine. Any modern (≥ 4.x) build is enough.
- `yt-dlp` is optional. Don't block install on it; install lazily the first time a user asks to pull from a URL.
- Node.js/npm are only needed for HyperFrames or Remotion slots. HyperFrames currently requires Node.js 22+.
- HyperFrames, Remotion, and Manim are optional animation engines. Don't install or prefer one globally during setup; pick the engine per animation slot in `SKILL.md`. HyperFrames can run through `npx --yes hyperframes ...` in the slot directory. Remotion can be scaffolded with `npx create-video@latest` or installed inside the slot before rendering.
- Never run a Scribe transcription as part of install verification unless the user explicitly asks — it costs real money. The local engine is free to verify; the synthetic clip in step 6 is the right check.
- `VIDEO_USE_TRANSCRIBER` is the only transcription setting. Which Whisper library runs is decided by `helpers/local_stt.py probe`, not by the user, and never belongs in `.env`.
- If the user is on Linux without a package manager Claude recognizes, print the manual `ffmpeg` install URL and wait rather than guessing.
