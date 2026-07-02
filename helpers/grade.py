#!/usr/bin/env python3
"""Back-compat shim — the code moved to src/video_use/grade.py.

Prefer the installed console command: `video-use grade`.
This shim keeps `python helpers/grade.py` working for git-clone installs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from video_use.grade import main

if __name__ == "__main__":
    sys.exit(main())
