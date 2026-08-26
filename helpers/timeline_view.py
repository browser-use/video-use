#!/usr/bin/env python3
"""Back-compat shim — the code moved to src/video_use/timeline_view.py.

Prefer the installed console command: `video-use timeline`.
This shim keeps `python helpers/timeline_view.py` working for git-clone installs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from video_use.timeline_view import main

if __name__ == "__main__":
    sys.exit(main())
