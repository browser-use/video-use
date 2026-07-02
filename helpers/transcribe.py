#!/usr/bin/env python3
"""Back-compat shim — the code moved to src/video_use/transcribe.py.

Prefer the installed console command: `video-use transcribe`.
This shim keeps `python helpers/transcribe.py` working for git-clone installs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from video_use.transcribe import main

if __name__ == "__main__":
    sys.exit(main())
