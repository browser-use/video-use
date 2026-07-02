#!/usr/bin/env python3
"""Back-compat shim — the code moved to src/video_use/render.py.

Prefer the installed console command: `video-use render`.
This shim keeps `python helpers/render.py` working for git-clone installs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from video_use.render import main

if __name__ == "__main__":
    sys.exit(main())
