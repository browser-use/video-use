"""Beat-sheet authoring helpers for ``helpers/board.py``.

Write ``edit/make_board.py`` in the project, import this module from the skill,
build beats with the element helpers, and call ``write()``:

    import sys
    from pathlib import Path
    sys.path.insert(0, "/path/to/video-use/helpers")
    from boardlib import *

    b = BoardBuilder(Path(__file__).resolve().parent)
    b.beat("hook", 0.0, [
        display("t_hook", "If you woke up", W + "woke", y=0.4, size=150),
        label("l_today", "today", W + "today", 0.5, 0.62),
    ])
    b.beat("signoff", W + "This", endcard("SQLite"))
    b.write()

Then ``python helpers/board.py edit/board.json --resolve-only`` and iterate.
Element ids must be unique across the whole board. Coordinates are fractions of
the canvas; ``W + "phrase"`` anchors to the spoken words (exact token match first,
prefix as a fallback; use two-word phrases to disambiguate, e.g. ``W + "Every job"``).
"""
from __future__ import annotations

import json
from pathlib import Path

A = "assets"
W = "word:"

__all__ = ["A", "W", "BoardBuilder", "display", "label", "sticker", "card", "logo", "emoji", "code", "video", "dim", "endcard", "titlecard"]


# collects beats and writes board json with the default style and duplicate id checks
class BoardBuilder:
    """Collects beats and writes ``board.json`` with the genre's default style."""

    # remember the edit directory and any top level board defaults
    def __init__(self, edit_dir: Path, **defaults):
        self.edit = Path(edit_dir)
        self.beats: list[dict] = []
        self.defaults = defaults

    # append a beat with an id a start anchor and its elements
    def beat(self, bid: str, at, elements: list[dict], **extra) -> dict:
        beat = {"id": bid, "at": at, "elements": elements}
        beat.update(extra)
        self.beats.append(beat)
        return beat

    # assemble the board dict apply overrides reject duplicate element ids and write board json
    def write(self, **overrides) -> Path:
        board = {
            "version": 1, "width": 1920, "height": 1080, "fps": 30,
            "narration": "narration.wav", "alignment": "narration.alignment.json", "tail": 0.8,
            "sfx": "sparse", "sfx_gain_db": -20,
            "music": {"file": "assets/bed.wav", "gain_db": -22, "fade_out": 2.5},
            "style": {"bg": "#0E0D14", "accent": "#F5546B"},
            "beats": self.beats,
        }
        board.update(self.defaults)
        board.update(overrides)
        ids = [e["id"] for b in board["beats"] for e in b["elements"]]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate element ids: {', '.join(duplicates)}")
        out = self.edit / "board.json"
        with out.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(board, indent=1, ensure_ascii=False) + "\n")
        print(f"board.json: {len(self.beats)} beats, {len(ids)} elements")
        return out


# big display word card element
def display(eid, text, at=0.0, x=0.5, y=0.45, size=140, color="white", enter="pop", **kw):
    """Big word card (1-4 words) in the heavy display face."""
    e = {"id": eid, "kind": "text", "text": text, "style": "display", "size": size, "color": color, "x": x, "y": y, "enter": enter, "at": at}
    e.update(kw)
    return e


# condensed uppercase label in a solid box
def label(eid, text, at, x, y, box="white", color="#0E0D14", size=60, enter="pop", **kw):
    """Condensed uppercase text in a solid box (white, cyan, green, purple, accent)."""
    e = {"id": eid, "kind": "text", "text": text, "style": "label", "box": box, "color": color, "size": size, "x": x, "y": y, "enter": enter, "at": at}
    e.update(kw)
    return e


# rotated outlined word for punchlines over media
def sticker(eid, text, at, x, y, rotate=-6, color="accent", outline="white", size=120, enter="pop", **kw):
    """Rotated outlined word, for punchlines over media. Use a newline for two lines."""
    e = {"id": eid, "kind": "text", "text": text, "style": "sticker", "color": color, "outline": outline, "size": size, "rotate": rotate, "x": x, "y": y, "enter": enter, "at": at}
    e.update(kw)
    return e


# evidence image card revealed in blocks with a slow zoom
def card(eid, file, at, x=0.04, y=0.5, w=0.6, h=0.8, anchor="left", enter="blocks", motion="kenburns", **kw):
    """Evidence card (a PNG from web_shot.py card) revealed in blocks with a slow zoom."""
    e = {"id": eid, "kind": "image", "file": f"{A}/{file}", "x": x, "y": y, "w": w, "h": h, "anchor": anchor, "enter": enter, "motion": motion, "motion_amount": 0.5, "at": at}
    e.update(kw)
    return e


# small logo image that grows in
def logo(eid, file, at, x, y, w=0.18, h=0.22, enter="grow", **kw):
    e = {"id": eid, "kind": "image", "file": f"{A}/{file}", "x": x, "y": y, "w": w, "h": h, "enter": enter, "at": at}
    e.update(kw)
    return e


# emoji element that pops in
def emoji(eid, text, at, x, y, size=190, enter="pop", **kw):
    e = {"id": eid, "kind": "emoji", "text": text, "size": size, "x": x, "y": y, "enter": enter, "at": at}
    e.update(kw)
    return e


# code card that types itself in
def code(eid, src, at, x, y, lang="python", size=32, enter="type", enter_duration=1.0, anchor="center", **kw):
    e = {"id": eid, "kind": "code", "code": src, "lang": lang, "size": size, "x": x, "y": y, "anchor": anchor, "enter": enter, "enter_duration": enter_duration, "at": at}
    e.update(kw)
    return e


# clip element either full frame cover or a rounded card on the left
def video(eid, file, at, source_start=0.0, full=True, **kw):
    """A clip: full-frame cover by default, or a rounded card on the left with ``full=False``.
    The clip must contain enough footage for the element's span; bound it with ``until``."""
    e = {"id": eid, "kind": "video", "file": file, "source_start": source_start, "x": 0.5, "y": 0.5, "fit": "cover", "enter": "fade", "at": at}
    if not full:
        e.update({"x": 0.04, "y": 0.5, "anchor": "left", "w": 0.58, "card": True, "enter": "slide_top"})
    e.update(kw)
    return e


# full frame translucent black rect that darkens a clip under text
def dim(eid, at, alpha="88", allow=(), **kw):
    """Full-frame translucent black rect to darken a clip under text. List the ids it may cover."""
    e = {"id": eid, "kind": "rect", "w": 1.0, "h": 1.0, "x": 0.5, "y": 0.5, "color": f"#000000{alpha}", "enter": "fade", "at": at, "allow_overlap_with": list(allow)}
    e.update(kw)
    return e


# boxed show title with the date underneath anchored to the spoken date line
def titlecard(show: str, date_text: str, at=W + "September", date_anchor=W + "2026,", enter="grow"):
    """Boxed show title with the date underneath; anchor it to the spoken date line."""
    return [
        {"id": "box_title", "kind": "box", "w": 0.36, "h": 0.42, "x": 0.5, "y": 0.47, "color": "white", "width": 10, "radius": 18, "enter": enter, "at": at},
        display("t_the", "The", at, y=0.32, size=80, enter=enter, allow_overlap_with=["box_title"]),
        display("t_title", show, at, y=0.5, size=150, enter=enter, allow_overlap_with=["box_title", "t_the"]),
        label("l_date", date_text, date_anchor, 0.5, 0.78, box="accent", color="white", size=64, enter="wipe"),
    ]


# closing card with a boxed topic a subtitle label and a thumbs up on the like line
def endcard(topic: str, at=W + "This", subtitle="in 60 seconds", like_anchor=W + "like"):
    """Closing card: boxed topic title, a subtitle label, thumbs-up on the like line."""
    return [
        {"id": "box_end", "kind": "box", "w": 0.44, "h": 0.42, "x": 0.5, "y": 0.45, "color": "white", "width": 10, "radius": 18, "enter": "slide_left", "at": at},
        display("t_end", topic, at, y=0.4, size=130, enter="slide_left", allow_overlap_with=["box_end"]),
        label("l_sub", subtitle, at, 0.5, 0.58, box="accent", color="white", size=56, enter="slide_left", allow_overlap_with=["box_end", "t_end"]),
        emoji("e_like", "👍", like_anchor, 0.5, 0.82, size=140),
    ]
