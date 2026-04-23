---
name: video-use-install
description: Install video-use into the current agent (Claude Code, Codex, Hermes, Openclaw, etc.) and wire up ffmpeg + the ElevenLabs API key so the user can start editing immediately.
---

# video-use install

Use this file only for first-time install or reconnect. For daily editing, read `SKILL.md`. Always read `helpers/` — that's where the scripts live.

## What you're doing

You're setting up a conversation-driven video editor for the user. After install, the user drops raw footage into any folder, runs their agent (`claude`, `codex`, etc.) there, and says "edit these into a launch video." You do the rest by reading `SKILL.md`.

Three things must exist on this machine:

1. The `video-use` repo cloned somewhere stable.
2. `ffmpeg` on `$PATH` (plus optional `yt-dlp` for online sources).
3. An ElevenLabs API key in `.env` at the repo root (for Scribe transcription).

And one thing must be true about the current agent:

4. It can discover `SKILL.md` — either via a global skills directory (`~/.claude/skills/`, `~/.codex/skills/`) or via a `CLAUDE.md` / system-prompt import.

## Install prompt contract

- Do everything yourself. Only ask the user for things you cannot generate — the ElevenLabs API key, and confirmation before `brew install`.
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

`ffmpeg` and `ffprobe` are hard requirements. `yt-dlp` is only needed if the user wants to pull sources from URLs.

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

### 5. ElevenLabs API key

Scribe (ElevenLabs) does all transcription. Without a key, nothing transcribes.

1. Check existing state in this order and stop at the first hit:

    ```bash
    # a) env var already exported
    [ -n "$ELEVENLABS_API_KEY" ] && echo "env"
    # b) .env at repo root already has it
    grep -q '^ELEVENLABS_API_KEY=..' ~/Developer/video-use/.env 2>/dev/null && echo "dotenv"
    ```

2. If neither is set, ask the user exactly once:

    > I need an ElevenLabs API key for transcription (word-level timestamps, speaker diarization, filler tagging). Grab one at https://elevenlabs.io/app/settings/api-keys and paste it here — I'll write it to `~/Developer/video-use/.env`. Or if you already have it exported as `ELEVENLABS_API_KEY`, say "use env" and I'll skip.

    When the user pastes a key, write it to `~/Developer/video-use/.env`:

    ```bash
    printf 'ELEVENLABS_API_KEY=%s\n' "$KEY" > ~/Developer/video-use/.env
    chmod 600 ~/Developer/video-use/.env
    ```

    Never echo the key back in tool output. Never commit `.env`.

3. Sanity check with a cheap, quota-free call:

    ```bash
    curl -s -o /dev/null -w '%{http_code}\n' \
      -H "xi-api-key: $(sed -n 's/^ELEVENLABS_API_KEY=//p' ~/Developer/video-use/.env)" \
      https://api.elevenlabs.io/v1/user
    ```

    `200` means the key works. `401` means the user pasted a wrong/expired key — ask once more and stop. Anything else (network, 5xx), move on and verify during first real transcription.

### 6. Verify end-to-end

Run one real thing. Prefer the lightest verification that still proves the pipeline is wired up:

```bash
python ~/Developer/video-use/helpers/timeline_view.py --help >/dev/null && echo "helpers OK"
ffprobe -version | head -1
```

Full transcription test is optional at install time — it burns Scribe credits. Better to wait until the user hands you their first clip.

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
- Never run transcription as part of install verification unless the user explicitly asks — Scribe costs real money.
- If the user is on Linux without a package manager Claude recognizes, print the manual `ffmpeg` install URL and wait rather than guessing.
