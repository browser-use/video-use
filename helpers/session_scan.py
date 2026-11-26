#!/usr/bin/env python3
"""Privacy scan for session-capture B-roll. Run it on every text that will reach a frame.

A session capture shows real Claude Code work on screen, so anything in the transcript can end up
in a public video. This scan is the gate: it fails on emails, home and Drive paths, private links,
phone numbers, patient identifiers, key shapes, and any name in a private denylist (clients,
prospects, patients), one per line. The denylist path comes from --denylist or $BROLL_DENYLIST.

    python3 helpers/session_scan.py capture.jsonl --denylist ~/path/denylist.txt

Exit 0 when clean, 1 with one line per hit otherwise.
"""
import argparse
import json
import os
import re
import sys

PATTERNS = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "home path": r"/Users/[^/\s]+|~/Library/CloudStorage|GoogleDrive-[\w.@-]+",
    "private link": r"(?:app\.)?notion\.(?:so|com)/\S+|claude\.ai/(?:artifact|chat|code)/\S+|docs\.google\.com/\S+|drive\.google\.com/\S+",
    "phone": r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)",
    "ssn": r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)",
    "patient identifier": r"\b(?:MRN|DOB|date of birth|medical record)\b",
    "key": r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})",
}


def load_denylist(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]


def scan_text(text, denylist=()):
    """Return [(kind, match)] for every hit in text."""
    hits = []
    for kind, pat in PATTERNS.items():
        for m in re.finditer(pat, text, re.I if kind == "patient identifier" else 0):
            hits.append((kind, m.group(0)))
    for name in denylist:
        for m in re.finditer(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I):
            hits.append(("denylist", m.group(0)))
    return hits


def texts_from_jsonl(path):
    """Every string in every record (message, tool inputs and results, cwd and other metadata)."""
    out = []

    def walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k not in ("signature",):
                    walk(x)

    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                walk(json.loads(line))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", help=".jsonl captures or plain text files")
    ap.add_argument("--denylist", default=os.environ.get("BROLL_DENYLIST"))
    a = ap.parse_args(argv)
    deny = load_denylist(a.denylist)
    if a.denylist and not deny:
        print(f"denylist {a.denylist} is missing or empty", file=sys.stderr)
        return 1
    bad = 0
    for f in a.files:
        texts = texts_from_jsonl(f) if f.endswith(".jsonl") else [open(f, encoding="utf-8").read()]
        for t in texts:
            for kind, m in scan_text(t, deny):
                print(f"{f}: {kind}: {m}")
                bad += 1
    if not deny:
        print("note: no denylist given, client and prospect names were not checked", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
