"""Shared helpers for command-line entry points."""

from __future__ import annotations

import sys


def configure_stdout() -> None:
    """Keep progress output safe when the locale encoding is not UTF-8."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
