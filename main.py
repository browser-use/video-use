"""Project-root entry point for the SRT-driven editor.

A thin wrapper over `helpers/srt_driven_edit.py` that fills in the
`input/` -> `output/` layout described in CLAUDE.md so the common case
collapses to a single command:

    python main.py

The wrapper injects these defaults only when the corresponding flag is
absent from `sys.argv`:

    --srt    input/script.srt          (always; required by srt_driven_edit)
    --plan   input/edit_plan.json      (always; required by srt_driven_edit)
    --source input/source.mp4          (only if the file exists)
    --voice  input/voice.wav           (only if the file exists)
    -o       output/final.mp4          (always; output/ is auto-created)

Anything you pass explicitly wins. Batch mode (`--batch <manifest>`) skips
all single-job defaults so the manifest fully owns its own paths.

Examples:
    python main.py
    python main.py --bg-volume 0.1 --style cjk-natural
    python main.py --plan plans/custom.json -o out/custom.mp4
    python main.py --batch jobs.json --continue-on-error
"""

from __future__ import annotations

import sys
from pathlib import Path

# Wire helpers/ onto sys.path so `from srt_driven_edit import ...` works
# regardless of the user's cwd.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "helpers"))

from srt_driven_edit import main as _srt_driven_main  # noqa: E402


def _has_flag(args: list[str], *flags: str) -> bool:
    """True if any of `flags` appears in `args`, in either bare or `=` form."""
    for token in args:
        for f in flags:
            if token == f or token.startswith(f + "="):
                return True
    return False


def _inject_defaults(args: list[str]) -> list[str]:
    """Add input/ -> output/ defaults for the flags the user did not provide."""
    out = list(args)

    # Batch mode owns its own paths via the manifest — never inject.
    if _has_flag(out, "--batch"):
        return out

    if not _has_flag(out, "--srt"):
        out += ["--srt", "input/script.srt"]
    if not _has_flag(out, "--plan"):
        out += ["--plan", "input/edit_plan.json"]

    # --source is required for Form A plans but ignored for Form B. Inject
    # only when the file is actually present so Form B users with no
    # input/source.mp4 don't get a misleading "missing on disk" error.
    if not _has_flag(out, "--source") and (ROOT / "input/source.mp4").exists():
        out += ["--source", "input/source.mp4"]

    # Same idea for voice: it's always optional, so inject only when present.
    if not _has_flag(out, "--voice") and (ROOT / "input/voice.wav").exists():
        out += ["--voice", "input/voice.wav"]

    if not _has_flag(out, "-o", "--output"):
        (ROOT / "output").mkdir(exist_ok=True)
        out += ["-o", "output/final.mp4"]

    return out


def main() -> None:
    sys.argv = [sys.argv[0]] + _inject_defaults(sys.argv[1:])
    _srt_driven_main()


if __name__ == "__main__":
    main()
