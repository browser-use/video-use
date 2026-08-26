"""video-use console entry point.

Dispatches subcommands to the helper modules, and provides install plumbing
(`skill`, `where`, `key`, `doctor`) so agents can set everything up without
cloning the repo.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

# Windows consoles default to cp1252, which can't encode arrows/emoji some
# helpers print. Force UTF-8 so output never dies on UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from video_use import __version__
from video_use.paths import config_dir, resolve_api_key

PKG_DIR = Path(__file__).resolve().parent

# subcommand -> module with a main() that parses sys.argv[1:]
COMMANDS = {
    "transcribe": "video_use.transcribe",
    "transcribe-batch": "video_use.transcribe_batch",
    "pack": "video_use.pack_transcripts",
    "timeline": "video_use.timeline_view",
    "grade": "video_use.grade",
    "render": "video_use.render",
}

HELP = f"""video-use {__version__} — edit videos with coding agents

Read SKILL.md for the editing workflow. Typical pipeline:

  video-use transcribe-batch <videos_dir>       transcribe every source (cached)
  video-use pack --edit-dir <videos_dir>/edit   build takes_packed.md
  video-use timeline <video> <start> <end>      filmstrip + waveform PNG
  video-use render <edl.json> -o final.mp4      EDL -> final video

Commands:
  transcribe <video>          single-file Scribe transcription
  transcribe-batch <dir>      parallel transcription of a directory
  pack --edit-dir <dir>       transcripts/*.json -> takes_packed.md
  timeline <video> <s> <e>    visual drill-down composite
  grade <in> -o <out>         color grade (presets or raw ffmpeg filter)
  render <edl.json> -o <out>  extract -> concat -> overlays -> subtitles

  skill                       print the video-use SKILL.md (pipe into your agent's skills dir)
  where                       print the installed package directory (bundled skills live here)
  key                         store the ElevenLabs API key in {config_dir() / '.env'}
  doctor                      diagnose ffmpeg, API key, and install state
  --version                   print the installed version

Pass -h to any subcommand for its full flags.
"""


def cmd_skill(argv: list[str]) -> int:
    name = argv[0] if argv else "SKILL"
    candidates = {
        "SKILL": PKG_DIR / "SKILL.md",
        "manim-video": PKG_DIR / "skills" / "manim-video" / "SKILL.md",
    }
    path = candidates.get(name)
    if path is None or not path.exists():
        print(f"unknown skill: {name} (available: {', '.join(candidates)})", file=sys.stderr)
        return 1
    sys.stdout.write(path.read_text(encoding="utf-8"))
    return 0


def cmd_where(_argv: list[str]) -> int:
    print(PKG_DIR)
    return 0


def cmd_key(argv: list[str]) -> int:
    if argv and argv[0] not in ("-", "--stdin"):
        key = argv[0].strip()
    elif not sys.stdin.isatty():
        key = sys.stdin.readline().strip()
    else:
        key = input("Paste your ElevenLabs API key: ").strip()
    if not key:
        print("no key provided", file=sys.stderr)
        return 1
    cfg = config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    env_path = cfg / ".env"
    lines = []
    if env_path.exists():
        lines = [
            l for l in env_path.read_text().splitlines()
            if not l.strip().startswith("ELEVENLABS_API_KEY=")
        ]
    lines.append(f"ELEVENLABS_API_KEY={key}")
    env_path.write_text("\n".join(lines) + "\n")
    try:
        env_path.chmod(0o600)
    except OSError:
        pass
    print(f"wrote ELEVENLABS_API_KEY to {env_path}")
    return 0


def cmd_doctor(_argv: list[str]) -> int:
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        mark = "OK  " if passed else "FAIL"
        print(f"  {mark} {label}" + (f" — {detail}" if detail else ""))

    print(f"video-use {__version__} (python {sys.version.split()[0]})")

    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        if path:
            try:
                head = subprocess.run(
                    [tool, "-version"], capture_output=True, text=True, timeout=10
                ).stdout.splitlines()[0]
            except Exception:
                head = path
            check(tool, True, head)
        else:
            check(tool, False, "not on PATH — install ffmpeg (brew/apt/winget)")

    key, source = resolve_api_key()
    if key:
        check("ELEVENLABS_API_KEY", True, f"from {source}")
    else:
        check(
            "ELEVENLABS_API_KEY", False,
            "not found — run `video-use key` (get one at https://elevenlabs.io/app/settings/api-keys)",
        )

    for mod in ("requests", "numpy", "PIL"):
        try:
            importlib.import_module(mod)
            check(f"python dep: {mod}", True)
        except ImportError:
            check(f"python dep: {mod}", False, "reinstall: uv tool install --force video-use")

    for tool, note in (("yt-dlp", "only needed for URL sources"), ("node", "only needed for HyperFrames/Remotion overlays")):
        print(f"  {'OK  ' if shutil.which(tool) else 'info'} {tool} " + ("" if shutil.which(tool) else f"not found — {note}"))

    print("all good" if ok else "fix the FAIL lines above, then re-run `video-use doctor`")
    return 0 if ok else 1


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(HELP)
        return 0
    if argv[0] in ("-V", "--version", "version"):
        print(__version__)
        return 0

    cmd, rest = argv[0], argv[1:]
    if cmd == "skill":
        return cmd_skill(rest)
    if cmd == "where":
        return cmd_where(rest)
    if cmd == "key":
        return cmd_key(rest)
    if cmd == "doctor":
        return cmd_doctor(rest)

    module_name = COMMANDS.get(cmd)
    if module_name is None:
        print(f"unknown command: {cmd}\n", file=sys.stderr)
        print(HELP, file=sys.stderr)
        return 2

    module = importlib.import_module(module_name)
    sys.argv = [f"video-use {cmd}", *rest]
    result = module.main()
    return int(result) if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())
