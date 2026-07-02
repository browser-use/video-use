---
name: video-use-install
description: Install video-use into the current agent (Claude Code, Codex, Hermes, Openclaw, etc.) and wire up ffmpeg + the ElevenLabs API key so the user can start editing immediately.
---

# video-use install

Use once. For daily editing, read `SKILL.md`.

## Fast Path

```bash
uv tool install --python 3.12 --upgrade video-use
mkdir -p ~/.claude/skills/video-use
video-use skill > ~/.claude/skills/video-use/SKILL.md
video-use doctor
```

If `doctor` prints `all good`, stop. Setup is done.

No `uv`? `pipx install video-use` or `pip install video-use` work too. `uv` itself installs with `curl -LsSf https://astral.sh/uv/install.sh | sh`.

For Codex, write the skill to `"${CODEX_HOME:-$HOME/.codex}/skills/video-use/SKILL.md"` instead. For Hermes / Openclaw / any other agent with a skills directory, same idea: register a skill named `video-use` with `video-use skill` as the body. If the agent has no skills directory, add a system-prompt import pointing at the file, plus this trigger line:

```text
Use video-use for any video editing: cutting, transcription, color grading, subtitles, overlays.
```

If you can't tell which agent you're running under, ask the user once.

## ffmpeg

`ffmpeg` and `ffprobe` are hard requirements. `video-use doctor` checks for them.

```bash
# macOS
brew install ffmpeg
# Debian / Ubuntu
sudo apt-get update && sudo apt-get install -y ffmpeg
# Windows
winget install ffmpeg
# Arch
sudo pacman -S ffmpeg
```

If the package manager needs a sudo prompt, tell the user the exact command and wait. Do not invent a password. Static builds work fine; any modern (≥ 4.x) build is enough.

## ElevenLabs API key

Scribe (ElevenLabs) does all transcription. Without a key, nothing transcribes.

`video-use doctor` reports whether a key already resolves (environment → `./.env` → `~/.config/video-use/.env`). If it doesn't, ask the user exactly once:

> I need an ElevenLabs API key for transcription (word-level timestamps, speaker diarization, filler tagging). Grab one at https://elevenlabs.io/app/settings/api-keys and paste it here. Or if you already have it exported as `ELEVENLABS_API_KEY`, say "use env" and I'll skip.

When the user pastes a key, store it:

```bash
video-use key <PASTED_KEY>
```

This writes `~/.config/video-use/.env` with mode 600. Never echo the key back in tool output. Never write it into the user's footage directory.

Optional sanity check (quota-free):

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H "xi-api-key: $KEY" https://api.elevenlabs.io/v1/user
```

`200` means the key works. `401` means wrong/expired — ask once more and stop. Anything else (network, 5xx), move on and verify during the first real transcription.

## Verify and hand off

`video-use doctor` is the verification. Don't run a real transcription at install time — Scribe costs money; wait for the user's first clip.

Tell the user, in one short message:

- video-use is installed; they should `cd` into their footage folder and start their agent there (e.g. `claude`).
- A good first message is: *"edit these into a launch video"* or *"inventory these takes and propose a strategy."*
- All outputs land in `<videos_dir>/edit/` — their footage is never touched.

## Updating

```bash
uv tool install --python 3.12 --upgrade --force video-use
video-use skill > ~/.claude/skills/video-use/SKILL.md   # refresh the registered copy
```

## Notes

- Optional extras install lazily on first use: `yt-dlp` (URL sources), Node.js 22+ (HyperFrames/Remotion animation slots), Manim (`uv tool install 'video-use[animations]'` or per-slot).
- Developing from a git clone still works: `pip install -e .` gives you the same `video-use` command, and `python helpers/<name>.py` shims remain for old symlink installs.
- If `.env` exists but the key is empty, treat it the same as missing.
- If the user is on Linux without a package manager you recognize, print the manual ffmpeg install URL (https://ffmpeg.org/download.html) and wait rather than guessing.
