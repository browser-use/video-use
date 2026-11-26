#!/usr/bin/env python3
"""B-roll lint: the mechanical rules from the feedback ledger, run before a scene track renders.

    node helpers/broll/texts.js scenes.html texts.json          # what the scenes actually draw
    python3 helpers/broll_lint.py texts.json --transcript transcript.json --page scenes.html

Each rule has an id that `references/broll-style.md` cites in the feedback ledger. When feedback
on an episode is mechanical, it becomes a rule here with a test, not a line of prose.

  B1  every number on screen is one the speaker says within --window seconds (dates are exempt)
  B2  on-screen text carries no em or en dash and no banned word
  B3  no string flashes on screen for less than --min-hold seconds
  B4  the page uses only brand colors (plus the capture theme) and only Montserrat and Inter
  B5  no text lands under the head card (top right, 1565..1905 x 170..510 on a 1920x1080 frame)
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BANNED = ["honest", "honestly", "genuinely", "genuine"]
# Serious AI tokens (brand-application assets/tokens.json, November 26, 2026): navy, orange, ink, the navy ramp, neutrals.
BRAND = {"#0D1C37", "#FF5A1F", "#090519", "#FFFFFF", "#F7F7F7", "#A9B6C6", "#122A4A", "#1D3857", "#294665",
         "#355573", "#436583", "#2C2C2C", "#888888", "#CCCCCC"}
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE = re.compile(rf"\b(?:{MONTHS}|{MONTHS.upper()}|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.? \d{{1,2}},? \d{{4}}\b|\b(19|20)\d{{2}}\b")
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
WORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000, "million": 1000000, "billion": 1000000000})


def norm(n):
    n = n.replace(",", "")
    try:
        f = float(n)
    except ValueError:
        return None
    return int(f) if f == int(f) else f


def spoken_numbers(words, t0, t1):
    """Numbers said between t0 and t1: numerals, and number words including simple compounds."""
    out, run = set(), []
    for w in words:
        if w.get("type", "word") != "word" or not (t0 <= w["start"] <= t1):
            continue
        tok = w["text"].lower().strip(".,?!;:\"'()$%")
        for m in NUM.finditer(tok):
            v = norm(m.group(0))
            if v is not None:
                out.add(v)
        for part in tok.replace("-", " ").split():
            if part in WORDS:
                run.append(WORDS[part])
                out.add(WORDS[part])
            elif run:
                out.add(compound(run))
                run = []
    if run:
        out.add(compound(run))
    return out


def compound(run):
    total, cur = 0, 0
    for v in run:
        if v in (100, 1000, 1000000, 1000000000):
            cur = max(cur, 1) * v
            if v >= 1000:
                total, cur = total + cur, 0
        else:
            cur += v
    return total + cur


HEAD_CARD = (1565, 170, 1905, 510)


def items(f):
    """Each sampled text as (string, box or None). Older texts.json files hold bare strings."""
    return [(x, None) if isinstance(x, str) else (x["s"], x.get("box")) for x in f["texts"]]


def spans(frames, step):
    """{text: [(first_t, last_t), ...]} from sampled frames."""
    seen = {}
    for f in frames:
        for s, _ in items(f):
            runs = seen.setdefault(s, [])
            if runs and f["t"] - runs[-1][1] <= step * 1.5:
                runs[-1][1] = f["t"]
            else:
                runs.append([f["t"], f["t"]])
    return seen


def lint(texts, words=None, page_html=None, window=20.0, min_hold=1.0, allow=(), theme_colors=()):
    problems = []
    step = texts.get("step", 0.25)
    sp = spans(texts["frames"], step)
    for s, runs in sp.items():
        if re.search("[—–]", s):
            problems.append(("B2", s, "em or en dash on screen"))
        for b in BANNED:
            if re.search(rf"\b{b}\b", s, re.I):
                problems.append(("B2", s, f"banned word '{b}'"))
        if all(r[1] - r[0] + step < min_hold for r in runs):
            problems.append(("B3", s, f"on screen under {min_hold}s"))
        if words is not None:
            bare = DATE.sub("", s)
            for m in NUM.finditer(bare):
                v = norm(m.group(0))
                if v is None or str(m.group(0)) in allow:
                    continue
                if not any(v in spoken_numbers(words, r[0] - window, r[1] + window) for r in runs):
                    problems.append(("B1", s, f"{m.group(0)} is not said within {window}s"))
    under = {}
    for f in texts["frames"]:
        for s, b in items(f):
            if b and b[0] < HEAD_CARD[2] and b[2] > HEAD_CARD[0] and b[1] < HEAD_CARD[3] and b[3] > HEAD_CARD[1]:
                under.setdefault(s, f["t"])
    for s, t in under.items():
        problems.append(("B5", s, f"text under the head card at {t:.2f}s"))
    if page_html is not None:
        ok = {c.upper() for c in BRAND | set(theme_colors)}
        for c in set(re.findall(r"#[0-9A-Fa-f]{6}\b", page_html)):
            if c.upper() not in ok:
                problems.append(("B4", c, "color outside the brand palette"))
        for src in re.findall(r"@font-face\{[^}]*src:url\(([^)]+)\)", page_html):
            if not re.search(r"montserrat|inter", src, re.I):
                problems.append(("B4", src, "font other than Montserrat or Inter"))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("texts")
    ap.add_argument("--transcript", help="word-level transcript .json ({words:[{text,start}]}) on the same clock as texts.json")
    ap.add_argument("--page", help="the scenes .html, for B4")
    ap.add_argument("--window", type=float, default=20.0)
    ap.add_argument("--min-hold", type=float, default=1.0)
    ap.add_argument("--allow", action="append", default=[], help="a number that may appear unspoken, e.g. a version")
    a = ap.parse_args(argv)
    texts = json.load(open(a.texts))
    words = json.load(open(a.transcript))["words"] if a.transcript else None
    page = open(a.page, encoding="utf-8").read() if a.page else None
    theme = json.load(open(os.path.join(HERE, "broll", "claude-dark.json")))
    probs = lint(texts, words, page, a.window, a.min_hold, a.allow, [v for v in theme.values() if str(v).startswith("#")])
    for rule, what, why in probs:
        print(f"{rule}  {why}: {what}")
    print(f"{len(probs)} problem(s)" if probs else "clean")
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
