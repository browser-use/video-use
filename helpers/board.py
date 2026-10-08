#!/usr/bin/env python3
"""Render a kinetic "board" video from a beat sheet: typography, cards, clips, timing.

A board is the complete visual base of a fast, faceless explainer: a dark
canvas on which one to three elements at a time (big words, labels, screenshot
cards, logos, emoji, code cards, memes, short video clips) pop, slide, or
dissolve in, each landing on the spoken word that motivates it. The output is
one MP4 with the narration (plus optional music, sound effects, and clip audio)
already muxed, so the public EDL only needs a single source and optional captions.

    python helpers/board.py edit/board.json -o edit/board.mp4 --preview
    python helpers/board.py edit/board.json -o edit/board.mp4 --contact edit/verify/board_contact.png
    python helpers/board.py edit/board.json --resolve-only            # print timing, no render
    python helpers/board.py edit/board.json -o edit/board.mp4 --write-edl edit/edl.json

The beat-sheet schema, element kinds, enter animations, and style options are
documented in ``references/editing/board-spec.md``. Layout QC runs
before rendering: every beat's settled frame is measured and checked with
``helpers/layout_qc.py`` for out-of-frame or unintended overlapping elements.
"""

from __future__ import annotations

import argparse
import json
import hashlib
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout_qc import LayoutQCError, validate_frame  # noqa: E402

SAMPLE_RATE = 48000
DEFAULT_STYLE: dict[str, Any] = {
    "bg": "#0E0D14",
    "text": "#FFFFFF",
    "accent": "#F5546B",
    "muted": "#9A9AAE",
    "palette": {"cyan": "#39C4E8", "green": "#5FD36A", "purple": "#8A3FE0", "yellow": "#FFD23F",
                "orange": "#FF8C42", "pink": "#F5546B", "blue": "#4C8DFF", "white": "#FFFFFF", "black": "#000000"},
    "fonts": {"display": "auto", "label": "auto", "mono": "auto", "body": "auto"},
    "card": {"radius": 24, "border": 2, "shadow": True},
    "code": {"bg": "#1B1B25", "text": "#ABB2BF", "keyword": "#C678DD", "string": "#98C379", "number": "#D19A66",
             "comment": "#5C6370", "function": "#61AFEF", "radius": 18},
}
ENTER_DEFAULTS = {
    "none": 0.0, "pop": 0.22, "grow": 0.3, "fade": 0.25, "slide_left": 0.35, "slide_right": 0.35,
    "slide_top": 0.35, "slide_bottom": 0.35, "whip_left": 0.3, "whip_right": 0.3, "blocks": 0.4,
    "wipe": 0.3, "type": 1.2,
}
EXIT_DEFAULTS = {"none": 0.0, "fade": 0.2, "pop": 0.18, "slide_left": 0.25, "slide_right": 0.25,
                 "slide_top": 0.25, "slide_bottom": 0.25}
SFX_FOR_ENTER = {"slide_left": "whoosh", "slide_right": "whoosh", "slide_top": "whoosh", "slide_bottom": "whoosh",
                 "whip_left": "whoosh", "whip_right": "whoosh", "blocks": "glitch", "pop": "pop", "grow": "pop",
                 "wipe": "pop", "type": None, "fade": None, "none": None}
SPARSE_SFX = {"whoosh", "glitch"}
FONT_CACHE_DIR = Path(os.environ.get("VIDEO_USE_TOOL_CACHE", Path.home() / ".cache" / "video-use")).expanduser() / "fonts"
SYSTEM_FONTS = {
    "display": [
        ("/Library/Fonts/Cubano-Regular.otf", None), (FONT_CACHE_DIR / "LilitaOne-Regular.ttf", None),
        ("/System/Library/Fonts/Supplemental/Arial Black.ttf", None),
        ("/System/Library/Fonts/Avenir Next Condensed.ttc#8", None),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", None),
    ],
    "label": [
        (FONT_CACHE_DIR / "BebasNeue-Regular.ttf", None), (FONT_CACHE_DIR / "Oswald[wght].ttf", "Bold"),
        ("/System/Library/Fonts/Avenir Next Condensed.ttc#8", None), ("/System/Library/Fonts/Supplemental/Impact.ttf", None),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", None),
    ],
    "mono": [
        (FONT_CACHE_DIR / "JetBrainsMono[wght].ttf", "Bold"), ("/System/Library/Fonts/Menlo.ttc#1", None),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", None),
    ],
    "body": [
        (FONT_CACHE_DIR / "Rubik[wght].ttf", "Bold"), ("/System/Library/Fonts/HelveticaNeue.ttc#1", None),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", None),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", None),
    ],
}
KEYWORDS = {
    "python": "def class return import from if elif else for while in not and or is None True False lambda with as try except raise yield async await pass break continue print",
    "javascript": "const let var function return if else for while import export from default class new this async await try catch throw typeof null undefined true false switch case break",
    "typescript": "const let var function return if else for while import export from default class new this async await try catch throw typeof null undefined true false interface type extends implements enum switch case break",
    "go": "package import func return var const if else for range type struct interface map chan go defer select switch case break continue nil true false make new",
    "rust": "fn let mut pub use struct enum impl trait return if else for while loop match in as mod crate self Self where unsafe async await move ref type const static true false",
    "c": "int char float double void return if else for while do switch case break continue struct typedef static const unsigned signed long short include define sizeof NULL",
    "bash": "if then else fi for do done while echo export cd ls rm mkdir sudo apt brew npm npx pip python go cargo git curl",
    "json": "true false null",
}
KEYWORDS["js"] = KEYWORDS["javascript"]
KEYWORDS["ts"] = KEYWORDS["typescript"]
KEYWORDS["sh"] = KEYWORDS["bash"]
KEYWORDS["cpp"] = KEYWORDS["c"]


# raised whenever a beat sheet cannot be rendered deterministically
class BoardError(ValueError):
    """Raised when a beat sheet cannot be rendered deterministically."""


# ---------------------------------------------------------------- easing
# cubic ease out that decelerates toward one
def ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


# exponential ease out that snaps quickly then settles
def ease_out_expo(t: float) -> float:
    return 1.0 if t >= 1 else 1 - 2 ** (-10 * t)


# ease out that overshoots slightly past one before settling for a springy pop
def ease_out_back(t: float, s: float = 1.6) -> float:
    t -= 1
    return 1 + t * t * ((s + 1) * t + s)


# cubic ease in that starts slow and accelerates
def ease_in_cubic(t: float) -> float:
    return t ** 3


# clip a value into a range that defaults to zero to one
def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------- colors / fonts
# turn a palette name style role hex string or rgb list into an rgba tuple
def parse_color(value: Any, style: dict[str, Any], default: str = "#FFFFFF") -> tuple[int, int, int, int]:
    if value is None:
        value = default
    if isinstance(value, (list, tuple)):
        parts = [int(v) for v in value]
        return (parts[0], parts[1], parts[2], parts[3] if len(parts) > 3 else 255)
    text = str(value).strip()
    palette = style.get("palette", {})
    named = {"accent": style.get("accent"), "text": style.get("text"), "bg": style.get("bg"), "muted": style.get("muted")}
    if text in palette:
        text = palette[text]
    elif text in named and named[text]:
        text = named[text]
    # expand short hex and add full alpha so every form ends up as eight hex digits
    text = text.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) == 6:
        text += "FF"
    if len(text) != 8:
        raise BoardError(f"unknown color '{value}'")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4, 6))  # type: ignore[return-value]


# resolves a font file per role with explicit overrides system fallbacks and a cache of opened faces
class FontBook:
    """Resolve fonts per role with explicit overrides, cached open fonts, and system fallbacks."""

    # merge the style font spec with defaults and resolve every role up front
    def __init__(self, fonts: dict[str, Any]):
        self.spec = {**DEFAULT_STYLE["fonts"], **(fonts or {})}
        self.resolved: dict[str, tuple[str, int, str | None]] = {}
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        for role in ("display", "label", "mono", "body"):
            self.resolved[role] = self._resolve(role, self.spec.get(role, "auto"))

    # split a font spec into path collection index and variation name
    @staticmethod
    def _split(spec: Any) -> tuple[str, int, str | None]:
        text = str(spec)
        variation = None
        index = 0
        if "@" in text:
            text, variation = text.rsplit("@", 1)
        if "#" in text:
            text, idx = text.rsplit("#", 1)
            index = int(idx)
        return text, index, variation

    # use an explicit path when given otherwise walk the system fallback list and keep the first face that opens
    def _resolve(self, role: str, spec: Any) -> tuple[str, int, str | None]:
        if spec and spec != "auto":
            path, index, variation = self._split(spec)
            if not Path(path).expanduser().exists():
                raise BoardError(f"font for '{role}' not found: {path}")
            return str(Path(path).expanduser()), index, variation
        for path, variation in SYSTEM_FONTS[role]:
            base, index, _ = self._split(str(path))
            if Path(base).exists():
                try:
                    ImageFont.truetype(base, 20, index=index)
                except Exception:
                    continue
                return base, index, variation
        return "", 0, None

    # return a cached font for a role and size applying the variation axis when the face has one
    def get(self, role: str, size: int) -> ImageFont.FreeTypeFont:
        key = (role, size)
        if key in self._cache:
            return self._cache[key]
        path, index, variation = self.resolved[role]
        if not path:
            font = ImageFont.load_default(size=size) if hasattr(ImageFont, "load_default") else ImageFont.load_default()
        else:
            font = ImageFont.truetype(path, size, index=index)
            if variation:
                try:
                    font.set_variation_by_name(variation)
                except Exception:
                    pass
        self._cache[key] = font
        return font

    # summarize the resolved font per role for logs and the timeline
    def describe(self) -> dict[str, str]:
        return {role: (f"{path}#{index}" if index else path) + (f"@{variation}" if variation else "") or "PIL default"
                for role, (path, index, variation) in self.resolved.items()}


# ---------------------------------------------------------------- alignment / anchors
# read a narration alignment file and keep only the timed word entries
def load_words(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    words = payload.get("words") if isinstance(payload, dict) else None
    if not isinstance(words, list) or not words:
        raise BoardError(f"alignment has no words: {path}")
    out = []
    for item in words:
        if not isinstance(item, dict) or item.get("type", "word") != "word":
            continue
        try:
            out.append({"text": str(item["text"]), "start": float(item["start"]), "end": float(item["end"])})
        except (KeyError, TypeError, ValueError):
            continue
    if not out:
        raise BoardError(f"alignment has no timed words: {path}")
    return out


# lowercase a token and strip everything except letters digits and apostrophes
def normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9']", "", text.lower())


# find when an anchor phrase is spoken after a given time preferring exact tokens and falling back to prefixes
def find_word_time(words: list[dict[str, Any]], anchor: str, after: float) -> float:
    """Return the start time of the first (or #n-th) occurrence of an anchor phrase after ``after``.

    Tokens are compared after stripping punctuation and case. Exact token matches
    win; prefix matches ("Tamay" for "Tamay's") are used only when no exact match
    exists, so "Every" no longer lands on "Everyone" and "So" on "some".
    """
    phrase, _, occurrence = anchor.partition("#")
    nth = int(occurrence) if occurrence else 1
    targets = [normalize_token(t) for t in phrase.split()]
    targets = [t for t in targets if t]
    if not targets:
        raise BoardError(f"empty word anchor '{anchor}'")

    # walk the words in order and return the start of the nth phrase match under the given comparison
    def scan(match) -> float | None:
        seen = 0
        for index, word in enumerate(words):
            if word["start"] + 1e-6 < after:
                continue
            window = words[index:index + len(targets)]
            if len(window) < len(targets):
                break
            if all(match(normalize_token(w["text"]), t) for w, t in zip(window, targets)):
                seen += 1
                if seen == nth:
                    return word["start"]
        return None

    # exact matches win so a short anchor does not land on a longer word that merely starts with it
    exact = scan(lambda got, want: got == want)
    if exact is not None:
        return exact
    prefix = scan(lambda got, want: got.startswith(want))
    if prefix is not None:
        return prefix
    raise BoardError(f"word anchor '{anchor}' not found in narration after {after:.2f}s")


# list the characters a font cannot draw by comparing each glyph mask with the notdef glyph
def missing_glyphs(font: ImageFont.FreeTypeFont, text: str) -> list[str]:
    """Characters the font cannot draw (they render as nothing or as the notdef box)."""

    # fingerprint a rendered glyph as its size and pixel bytes or none when it draws nothing
    def signature(ch: str):
        mask = font.getmask(ch)
        if mask.getbbox() is None:
            return None  # renders nothing
        try:
            return (mask.size, Image.Image()._new(mask).tobytes())
        except Exception:
            return (mask.size, None)

    try:
        notdef = signature("\u0378")  # unassigned code point -> the font's notdef glyph, if any
    except Exception:
        return []
    missing: list[str] = []
    for ch in sorted(set(text)):
        if ch.isspace():
            continue
        try:
            sig = signature(ch)
        except Exception:
            missing.append(ch)
            continue
        if sig is None or (notdef is not None and sig == notdef):
            missing.append(ch)
    return missing


# convert a time value that may be a number a word anchor or a relative offset into absolute seconds
def resolve_time(value: Any, *, words: list[dict[str, Any]] | None, base: float, after: float, label: str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.startswith("word:"):
        if words is None:
            raise BoardError(f"{label} uses a word anchor but the board has no alignment")
        return find_word_time(words, text[5:].strip(), after)
    if text.startswith("+") or text.startswith("-"):
        return base + float(text)
    try:
        return float(text)
    except ValueError as exc:
        raise BoardError(f"{label} has an invalid time '{value}'") from exc


# ---------------------------------------------------------------- text rendering
# greedily wrap words into lines that fit the given pixel width keeping explicit newlines
def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if font.getlength(trial) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


# draw wrapped text into a transparent image with optional stroke box strike and underline
def render_text_block(
    text: str, font: ImageFont.FreeTypeFont, *, fill, align: str = "center", max_width: int | None = None,
    stroke_width: int = 0, stroke_fill=None, line_spacing: float = 1.05, padding: int = 0, box_fill=None,
    box_radius: int = 0, strike_fill=None, underline_fill=None,
) -> Image.Image:
    lines = wrap_text(text, font, max_width) if max_width else text.split("\n")
    ascent, descent = font.getmetrics()
    line_height = int((ascent + descent) * line_spacing)
    widths = [int(font.getlength(line)) + 2 * stroke_width for line in lines]
    width = max(widths) if widths else 1
    height = line_height * len(lines) + 2 * stroke_width
    image = Image.new("RGBA", (width + 2 * padding, height + 2 * padding), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if box_fill is not None:
        draw.rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=box_radius, fill=box_fill)
    y = padding + stroke_width
    for line, line_width in zip(lines, widths):
        if align == "left":
            x = padding + stroke_width
        elif align == "right":
            x = padding + width - line_width + stroke_width
        else:
            x = padding + (width - line_width) // 2 + stroke_width
        draw.text((x, y), line, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)
        if strike_fill is not None:
            mid = y + ascent * 0.62
            draw.line((x - 6, mid, x + line_width - 2 * stroke_width + 6, mid), fill=strike_fill, width=max(4, font.size // 10))
        if underline_fill is not None:
            base = y + ascent + descent * 0.4
            draw.line((x, base, x + line_width - 2 * stroke_width, base), fill=underline_fill, width=max(4, font.size // 12))
        y += line_height
    return image


# tokenize code into colored pieces per line with a small regex based highlighter
def highlight_code(code: str, lang: str, palette: dict[str, str]) -> list[list[tuple[str, str]]]:
    """Return lines of (token, color) using a small regex tokenizer."""
    keywords = set(KEYWORDS.get(lang.lower(), KEYWORDS["javascript"]).split())
    comment_prefix = "#" if lang.lower() in {"python", "bash", "sh"} else "//"
    token_re = re.compile(r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`)|(\b\d+(?:\.\d+)?\b)|(\b[A-Za-z_][A-Za-z0-9_]*\b)(?=\()|(\b[A-Za-z_][A-Za-z0-9_]*\b)|(\s+)|(.)')
    lines = []
    for raw in code.split("\n"):
        tokens: list[tuple[str, str]] = []
        comment_at = raw.find(comment_prefix)
        body, comment = (raw[:comment_at], raw[comment_at:]) if comment_at >= 0 else (raw, "")
        for match in token_re.finditer(body):
            string, number, func, ident, space, other = match.groups()
            if string:
                tokens.append((string, palette["string"]))
            elif number:
                tokens.append((number, palette["number"]))
            elif func:
                tokens.append((func, palette["keyword"] if func in keywords else palette["function"]))
            elif ident:
                tokens.append((ident, palette["keyword"] if ident in keywords else palette["text"]))
            elif space:
                tokens.append((space, palette["text"]))
            else:
                tokens.append((other, palette["text"]))
        if comment:
            tokens.append((comment, palette["comment"]))
        lines.append(tokens)
    return lines


# draw a code card with a mac style header and syntax colored lines optionally cut off after n visible characters
def render_code_card(code: str, lang: str, font: ImageFont.FreeTypeFont, style: dict[str, Any], *, width: int | None,
                     title: str | None, chars_visible: int | None = None) -> Image.Image:
    palette = {**DEFAULT_STYLE["code"], **(style.get("code") or {})}
    lines = highlight_code(code, lang, palette)
    ascent, descent = font.getmetrics()
    line_height = int((ascent + descent) * 1.28)
    pad = int(font.size * 1.1)
    header = int(font.size * 1.6)
    content_width = max((sum(int(font.getlength(tok)) for tok, _ in line) for line in lines), default=10)
    card_width = width or content_width + 2 * pad
    card_height = header + pad + line_height * max(1, len(lines)) + pad
    image = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, card_width - 1, card_height - 1), radius=palette["radius"], fill=parse_color(palette["bg"], style))
    dot = max(6, font.size // 3)
    for i, color in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        cx = pad + i * (dot * 2 + 4)
        draw.ellipse((cx, header // 2 - dot // 2, cx + dot, header // 2 + dot // 2), fill=parse_color(color, style))
    if title:
        small = font.font_variant(size=max(10, int(font.size * 0.75)))
        draw.text((pad + 3 * (dot * 2 + 4) + 6, header // 2 - small.size // 2 - 2), title, font=small,
                  fill=parse_color(palette["comment"], style))
    # draw tokens until the visible character budget runs out then paint a cursor block for the typing effect
    remaining = chars_visible if chars_visible is not None else 10 ** 9
    y = header + pad // 2
    for line in lines:
        x = pad
        for token, color in line:
            if remaining <= 0:
                break
            piece = token[:remaining]
            remaining -= len(piece)
            draw.text((x, y), piece, font=font, fill=parse_color(color, style))
            x += int(font.getlength(piece))
        if remaining <= 0:
            draw.rectangle((x + 2, y, x + max(4, font.size // 2), y + ascent + descent), fill=parse_color(palette["text"], style))
            break
        y += line_height
    return image


# ---------------------------------------------------------------- image helpers
# scale an image down so it fits inside the given bounds without ever upscaling
def fit_within(image: Image.Image, max_w: int | None, max_h: int | None) -> Image.Image:
    scale = min((max_w / image.width) if max_w else 1e9, (max_h / image.height) if max_h else 1e9)
    if scale >= 1e8:
        return image
    if abs(scale - 1.0) < 1e-3:
        return image
    return image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)


# round the corners add a faint border and a drop shadow to turn an image into a card
def apply_card_treatment(image: Image.Image, style: dict[str, Any], *, radius: int | None, border: int | None,
                         shadow: bool | None) -> Image.Image:
    card = {**DEFAULT_STYLE["card"], **(style.get("card") or {})}
    radius = card["radius"] if radius is None else radius
    border = card["border"] if border is None else border
    shadow = card["shadow"] if shadow is None else shadow
    if radius:
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, fill=255)
        image = image.copy()
        image.putalpha(ImageChops.multiply(image.getchannel("A"), mask))
    if border:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rounded_rectangle((border // 2, border // 2, image.width - 1 - border // 2, image.height - 1 - border // 2),
                                                  radius=radius, outline=(255, 255, 255, 80), width=border)
        image = Image.alpha_composite(image, overlay)
    if shadow:
        image = add_shadow(image)
    return image


# composite a blurred offset copy of the alpha channel under an image as a soft shadow
def add_shadow(image: Image.Image, blur: int = 26, offset: tuple[int, int] = (0, 16), opacity: int = 150) -> Image.Image:
    pad = blur * 2 + max(abs(offset[0]), abs(offset[1]))
    canvas = Image.new("RGBA", (image.width + 2 * pad, image.height + 2 * pad), (0, 0, 0, 0))
    alpha = Image.new("L", canvas.size, 0)
    alpha.paste(image.getchannel("A").point(lambda a: a * opacity // 255), (pad + offset[0], pad + offset[1]))
    alpha = alpha.filter(ImageFilter.GaussianBlur(blur))
    shade = Image.new("RGBA", canvas.size, (0, 0, 0, 255))
    shade.putalpha(alpha)
    canvas = Image.alpha_composite(canvas, shade)
    canvas.alpha_composite(image, (pad, pad))
    return canvas


# multiply an image by a solid color while keeping its alpha
def tint_image(image: Image.Image, color: tuple[int, int, int, int]) -> Image.Image:
    rgb = Image.new("RGBA", image.size, color[:3] + (255,))
    mixed = ImageChops.multiply(image.convert("RGBA"), rgb)
    mixed.putalpha(image.getchannel("A"))
    return mixed


# build a mask that reveals a random subset of grid blocks proportional to progress using a fixed seed
def blocks_mask(size: tuple[int, int], progress: float, seed: int, block: int = 28) -> Image.Image:
    width, height = size
    cols, rows = max(1, math.ceil(width / block)), max(1, math.ceil(height / block))
    rng = np.random.default_rng(seed)
    order = rng.permutation(cols * rows)
    visible = np.zeros(cols * rows, dtype=np.uint8)
    visible[order[: int(round(progress * cols * rows))]] = 255
    grid = visible.reshape(rows, cols)
    mask = Image.fromarray(np.kron(grid, np.ones((block, block), dtype=np.uint8)), mode="L")
    return mask.crop((0, 0, width, height))


# offset the red and blue channels sideways for a glitch look
def rgb_shift(image: Image.Image, amount: int) -> Image.Image:
    if amount <= 0:
        return image
    r, g, b, a = image.split()
    r = ImageChops.offset(r, amount, 0)
    b = ImageChops.offset(b, -amount, 0)
    return Image.merge("RGBA", (r, g, b, a))


# ---------------------------------------------------------------- data model
# a resolved on screen element with its timing animation placement and rendered sprite
@dataclass
class Element:
    id: str
    kind: str
    start: float
    end: float
    enter: str
    enter_duration: float
    exit: str
    exit_duration: float
    x: float
    y: float
    anchor: str
    z: int
    rotate: float
    opacity: float
    motion: str
    motion_amount: float
    allow_overlap_with: list[str]
    raw: dict[str, Any]
    image: Image.Image | None = None            # settled RGBA sprite (or None for video)
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)   # settled bbox in canvas px
    video: dict[str, Any] | None = None         # decoded-clip state
    seed: int = 0
    type_chars: int = 0

    # the time when the enter animation has finished
    @property
    def enter_end(self) -> float:
        return self.start + self.enter_duration


# a resolved time span with its background and the elements shown during it
@dataclass
class Beat:
    id: str
    start: float
    end: float
    bg: dict[str, Any]
    elements: list[Element] = field(default_factory=list)
    bg_image: Image.Image | None = None


# ---------------------------------------------------------------- board
# the resolved board that owns timing sprites layout checks audio mixing and frame rendering
class Board:
    # parse the spec merge style load words and narration then resolve timing and build every sprite
    def __init__(self, spec: dict[str, Any], base_dir: Path, *, preview: bool = False, verbose: bool = True):
        self.spec = spec
        self.base_dir = base_dir
        self.verbose = verbose
        self.full_width = int(spec.get("width", 1920))
        self.full_height = int(spec.get("height", 1080))
        self.fps = float(spec.get("fps", 30))
        if not math.isfinite(self.fps) or not 1 <= self.fps <= 120:
            raise BoardError("fps must be finite and between 1 and 120")
        if min(self.full_width, self.full_height) < 4 or self.full_width % 2 or self.full_height % 2:
            raise BoardError("canvas dimensions must be even and at least four pixels")
        self.scale = 0.5 if preview else 1.0
        self.width = int(self.full_width * self.scale) // 2 * 2
        self.height = int(self.full_height * self.scale) // 2 * 2
        self.style = self._merge_style(spec.get("style") or {})
        self.fonts = FontBook(self.style.get("fonts") or {})
        self.words: list[dict[str, Any]] | None = None
        alignment = spec.get("alignment")
        if alignment:
            self.words = load_words(self.resolve_path(alignment))
        self.narration = self.resolve_path(spec["narration"]) if spec.get("narration") else None
        if self.narration and not self.narration.exists():
            raise BoardError(f"narration file not found: {self.narration}")
        self.safe_bottom = float(spec.get("captions_safe_bottom", 0.0))
        if not 0 <= self.safe_bottom <= 0.35:
            raise BoardError("captions_safe_bottom must be between 0 and 0.35")
        self.beats: list[Beat] = []
        self.duration = 0.0
        self.sfx_events: list[tuple[float, str]] = []
        self.clip_audio: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self._resolve()
        if not math.isfinite(self.duration) or self.duration <= 0 or round(self.duration * self.fps) < 1:
            raise BoardError("duration must be finite positive and cover at least one frame")
        self._build_sprites()

    # ---- helpers
    # resolve a spec path relative to the board directory unless it is absolute
    def resolve_path(self, value: str) -> Path:
        path = Path(str(value)).expanduser()
        return path if path.is_absolute() else (self.base_dir / path).resolve()

    # deep copy the default style and overlay the spec style one nested dict at a time
    def _merge_style(self, style: dict[str, Any]) -> dict[str, Any]:
        merged = json.loads(json.dumps(DEFAULT_STYLE))
        for key, value in style.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value
        return merged

    # convert a canvas fraction into pixels along the given axis
    def px(self, value: float, axis: str) -> int:
        return int(round(float(value) * (self.width if axis == "x" else self.height)))

    # print a progress message unless verbose is off
    def log(self, message: str) -> None:
        if self.verbose:
            print(message)

    # ---- timing
    # turn raw beats into timed beats by resolving explicit starts filling implicit ones and chaining ends
    def _resolve(self) -> None:
        raw_beats = self.spec.get("beats")
        if not isinstance(raw_beats, list) or not raw_beats:
            raise BoardError("board needs a non-empty 'beats' list")
        narration_duration = self._narration_duration()
        declared = self.spec.get("duration")
        cursor = 0.0
        pending: list[tuple[Beat, dict[str, Any]]] = []
        for index, raw in enumerate(raw_beats):
            if not isinstance(raw, dict):
                raise BoardError(f"beat {index} must be an object")
            beat_id = str(raw.get("id") or f"beat_{index + 1:02d}")
            start = resolve_time(raw.get("at", "+0" if index == 0 else "next"), words=self.words, base=cursor,
                                 after=cursor, label=f"beat '{beat_id}'.at") if raw.get("at", None) not in (None, "next") else None
            if start is None:
                start = cursor if index == 0 else -1.0  # -1 = fill from previous end
            if index == 0 and start < 0:
                start = 0.0
            beat = Beat(id=beat_id, start=start, end=-1.0, bg=self._bg_spec(raw.get("bg")))
            pending.append((beat, raw))
            if start >= 0:
                cursor = start
        # fill implicit starts from previous ends and compute ends
        for index, (beat, raw) in enumerate(pending):
            if beat.start < 0:
                beat.start = pending[index - 1][0].end
                if beat.start < 0:
                    raise BoardError(f"beat {beat.id!r} needs an explicit start or a previous beat end")
            if raw.get("end") not in (None, "next"):
                beat.end = resolve_time(raw["end"], words=self.words, base=beat.start, after=beat.start, label=f"beat '{beat.id}'.end")
            elif index + 1 < len(pending) and pending[index + 1][0].start >= 0 and pending[index + 1][1].get("at") not in (None, "next"):
                beat.end = pending[index + 1][0].start
            else:
                beat.end = -1.0
        # second pass: implicit ends chain to the next beat's start; last to duration
        for index, (beat, raw) in enumerate(pending):
            if beat.end < 0:
                if index + 1 < len(pending):
                    nxt = pending[index + 1][0]
                    if nxt.start < 0 or nxt.start <= beat.start:
                        raise BoardError(f"beat '{beat.id}' has no end and beat '{nxt.id}' has no explicit start")
                    beat.end = nxt.start
            if index + 1 < len(pending) and pending[index + 1][0].start < 0:
                pending[index + 1][0].start = beat.end
        # pick the board duration from the declared value the narration length or the last beat end in that order
        last = pending[-1][0]
        if declared is not None:
            self.duration = float(declared)
        elif narration_duration:
            self.duration = narration_duration + float(self.spec.get("tail", 0.6))
        elif last.end > 0:
            self.duration = last.end
        else:
            raise BoardError("cannot determine duration: set 'duration', provide narration, or give the last beat an 'end'")
        if last.end < 0:
            last.end = self.duration
        for beat, raw in pending:
            if beat.end <= beat.start:
                raise BoardError(f"beat '{beat.id}' ends ({beat.end:.2f}s) before it starts ({beat.start:.2f}s)")
            if beat.end > self.duration + 1e-6:
                raise BoardError(f"beat '{beat.id}' ends at {beat.end:.2f}s, after the board duration {self.duration:.2f}s")
            beat.elements = self._resolve_elements(beat, raw.get("elements") or [])
            self.beats.append(beat)
        # beats must be ordered and must not overlap and the board must cover the narration
        for first, second in zip(self.beats, self.beats[1:]):
            if second.start < first.end - 1e-6:
                raise BoardError(f"beats '{first.id}' and '{second.id}' overlap in time")
        if narration_duration and self.duration + 0.05 < narration_duration:
            raise BoardError(f"board duration {self.duration:.2f}s is shorter than the narration {narration_duration:.2f}s")

    # probe the narration file length with ffprobe or return zero when there is no narration
    def _narration_duration(self) -> float:
        if not self.narration:
            return 0.0
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                                 str(self.narration)], capture_output=True, text=True)
        try:
            return float(result.stdout.strip())
        except ValueError as exc:
            raise BoardError(f"cannot read narration duration: {self.narration}") from exc

    # normalize a beat background into a dict with at least a color
    def _bg_spec(self, raw: Any) -> dict[str, Any]:
        if raw is None:
            return {"color": self.style["bg"]}
        if isinstance(raw, str):
            return {"color": raw}
        if isinstance(raw, dict):
            return raw
        raise BoardError("beat.bg must be a color string or an object")

    # resolve every element of a beat into timed elements with validated enter and exit animations and sfx events
    def _resolve_elements(self, beat: Beat, raws: list[Any]) -> list[Element]:
        elements: list[Element] = []
        for index, raw in enumerate(raws):
            if not isinstance(raw, dict):
                raise BoardError(f"beat '{beat.id}' element {index} must be an object")
            kind = str(raw.get("kind") or "text")
            element_id = str(raw.get("id") or f"{beat.id}_{kind}{index + 1}")
            # numeric at and until values are relative to the beat start while strings go through the time resolver
            at_raw = raw.get("at", 0.0)
            if isinstance(at_raw, (int, float)):
                start = beat.start + float(at_raw)  # numbers are relative to the beat start
            else:
                start = resolve_time(at_raw, words=self.words, base=beat.start, after=beat.start, label=f"element '{element_id}'.at")
            until_raw = raw.get("until")
            if until_raw is not None:
                if isinstance(until_raw, (int, float)):
                    end = beat.start + float(until_raw)
                else:
                    end = resolve_time(until_raw, words=self.words, base=beat.start, after=start, label=f"element '{element_id}'.until")
            elif raw.get("hold") is not None:
                end = start + float(raw["hold"])
            else:
                end = beat.end
            end = min(end, beat.end)
            if start < beat.start - 1e-6 or start >= beat.end:
                raise BoardError(f"element '{element_id}' starts at {start:.2f}s outside beat '{beat.id}' ({beat.start:.2f}-{beat.end:.2f}s)")
            enter = str(raw.get("enter", "pop" if kind in {"text", "emoji", "box"} else "fade"))
            if enter not in ENTER_DEFAULTS:
                raise BoardError(f"element '{element_id}' has unknown enter '{enter}'; choose {sorted(ENTER_DEFAULTS)}")
            if enter == "type" and kind != "code":
                raise BoardError("type entry animation requires a code element")
            if raw.get("fit", "contain") not in {"cover", "contain"}:
                raise BoardError("fit must be cover or contain")
            exit_kind = str(raw.get("exit", "none"))
            if exit_kind not in EXIT_DEFAULTS:
                raise BoardError(f"element '{element_id}' has unknown exit '{exit_kind}'; choose {sorted(EXIT_DEFAULTS)}")
            # lead pulls the start earlier so the enter animation lands on the anchor word
            lead = float(raw.get("lead", 0.0))
            enter_duration = float(raw.get("enter_duration", ENTER_DEFAULTS[enter]))
            start = max(beat.start, start - lead)
            if end - start < 0.08:
                raise BoardError(f"element '{element_id}' is visible for less than 80 ms")
            enter_duration = min(enter_duration, max(0.0, end - start))
            elements.append(Element(
                id=element_id, kind=kind, start=start, end=end, enter=enter, enter_duration=enter_duration,
                exit=exit_kind, exit_duration=min(float(raw.get("exit_duration", EXIT_DEFAULTS[exit_kind])), end - start),
                x=float(raw.get("x", 0.5)), y=float(raw.get("y", 0.5)), anchor=str(raw.get("anchor", "center")),
                z=int(raw.get("z", index)), rotate=float(raw.get("rotate", 0.0)), opacity=float(raw.get("opacity", 1.0)),
                motion=str(raw.get("motion", "none")), motion_amount=float(raw.get("motion_amount", 1.0)),
                allow_overlap_with=[str(v) for v in raw.get("allow_overlap_with", [])], raw=raw,
                seed=int.from_bytes(hashlib.sha256(element_id.encode()).digest()[:4], "big") % (2 ** 31),
            ))
            sfx = SFX_FOR_ENTER.get(enter)
            if raw.get("sfx") is not None:
                sfx = raw["sfx"] or None
            if sfx:
                self.sfx_events.append((start, sfx))
        ids = [element.id for element in elements]
        if len(ids) != len(set(ids)):
            raise BoardError(f"beat '{beat.id}' has duplicate element ids")
        return elements

    # ---- sprites
    # render the background and the sprite for every element and compute its settled rectangle
    def _build_sprites(self) -> None:
        for beat in self.beats:
            beat.bg_image = self._build_background(beat)
            for element in beat.elements:
                if element.kind == "video":
                    self._prepare_video(element)
                else:
                    element.image = self._render_element(element)
                self._place(element)

    # load and fit an optional background image and darken it by the dim amount
    def _build_background(self, beat: Beat) -> Image.Image | None:
        bg = beat.bg
        if bg.get("image"):
            path = self.resolve_path(bg["image"])
            if not path.exists():
                raise BoardError(f"background image not found: {path}")
            image = Image.open(path).convert("RGBA")
            fit = str(bg.get("fit", "cover"))
            image = self._fit_canvas(image, fit)
            dim = float(bg.get("dim", 0.0))
            if dim > 0:
                shade = Image.new("RGBA", image.size, (0, 0, 0, int(255 * clamp(dim))))
                image = Image.alpha_composite(image, shade)
            return image
        return None

    # scale an image to cover or fit the canvas and center it on a transparent layer
    def _fit_canvas(self, image: Image.Image, fit: str, target: tuple[int, int] | None = None) -> Image.Image:
        if fit not in {"cover", "contain"}:
            raise BoardError("fit must be cover or contain")
        width, height = target or (self.width, self.height)
        scale = (max if fit == "cover" else min)(width / image.width, height / image.height)
        resized = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.alpha_composite(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
        return canvas

    # dispatch on element kind and return its settled sprite
    def _render_element(self, element: Element) -> Image.Image:
        raw, style = element.raw, self.style
        kind = element.kind
        if kind == "text":
            return self._render_text(element)
        if kind == "emoji":
            return self._render_emoji(element)
        if kind == "image":
            return self._render_image(element)
        if kind == "code":
            code = raw.get("code")
            if raw.get("file"):
                code = self.resolve_path(raw["file"]).read_text(encoding="utf-8")
            if not code:
                raise BoardError(f"code element '{element.id}' needs 'code' or 'file'")
            size = int(raw.get("size", 34) * self.scale)
            width = self.px(raw["w"], "x") if raw.get("w") else None
            element.type_chars = len(code)
            return render_code_card(code.rstrip("\n"), str(raw.get("lang", "javascript")), self.fonts.get("mono", size), style,
                                    width=width, title=raw.get("title"))
        if kind == "box":
            width, height = self.px(raw.get("w", 0.3), "x"), self.px(raw.get("h", 0.2), "y")
            line = int(raw.get("width", 6) * self.scale)
            color = parse_color(raw.get("color", "accent"), style)
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            ImageDraw.Draw(image).rounded_rectangle((line // 2, line // 2, width - 1 - line // 2, height - 1 - line // 2),
                                                    radius=int(raw.get("radius", 12) * self.scale), outline=color, width=line)
            return image
        if kind == "rect":
            width, height = self.px(raw.get("w", 0.3), "x"), self.px(raw.get("h", 0.2), "y")
            image = Image.new("RGBA", (width, height), parse_color(raw.get("color", "accent"), style))
            if raw.get("radius"):
                mask = Image.new("L", image.size, 0)
                ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=int(raw["radius"] * self.scale), fill=255)
                image.putalpha(mask)
            return image
        raise BoardError(f"element '{element.id}' has unknown kind '{kind}'")

    # render a text element in its style with font sizing box outline shadow and glyph warnings
    def _render_text(self, element: Element) -> Image.Image:
        raw, style = element.raw, self.style
        text = str(raw.get("text", "")).strip()
        if not text:
            raise BoardError(f"text element '{element.id}' has no text")
        text_style = str(raw.get("style", "display"))
        defaults = {"display": ("display", 120, True), "label": ("label", 64, True), "sticker": ("display", 110, True),
                    "body": ("body", 48, False), "quote": ("body", 52, False), "mono": ("mono", 44, False)}
        if text_style not in defaults:
            raise BoardError(f"text element '{element.id}' has unknown style '{text_style}'")
        role, default_size, upper = defaults[text_style]
        if raw.get("uppercase", upper):
            text = text.upper()
        word_count = len(text.split())
        if text_style in {"display", "label", "sticker"} and word_count > 6:
            self.warnings.append(f"text '{element.id}' has {word_count} words; designed text reads best at 1-4 words")
        size = int(float(raw.get("size", default_size)) * self.scale)
        font = self.fonts.get(role, size)
        lacking = missing_glyphs(font, text)
        if lacking:
            self.warnings.append(f"text '{element.id}' uses characters the {role} font cannot draw: {' '.join(lacking)} (rewrite them, e.g. 'to' instead of an arrow)")
        fill = parse_color(raw.get("color", style["text"]), style)
        max_width = self.px(raw["max_w"], "x") if raw.get("max_w") else None
        stroke = int(float(raw.get("outline_width", 0 if text_style != "sticker" else max(3, size // 14))) * self.scale)
        stroke_fill = parse_color(raw.get("outline", "#000000" if text_style == "sticker" else "#000000"), style) if stroke else None
        # labels get a solid box by default with dark text while other styles are boxed only when asked
        box_fill = parse_color(raw["box"], style) if raw.get("box") else (parse_color(style["text"], style) if text_style == "label" and raw.get("box", True) is True and "box" in raw else None)
        if text_style == "label" and "box" not in raw:
            box_fill = parse_color(style["text"], style)
            fill = parse_color(raw.get("color", "#0E0D14"), style)
        padding = int(raw.get("padding", size * 0.32 if box_fill else 0) * self.scale) if box_fill else 0
        image = render_text_block(
            text, font, fill=fill, align=str(raw.get("align", "center")), max_width=max_width, stroke_width=stroke,
            stroke_fill=stroke_fill, padding=padding, box_fill=box_fill, box_radius=int(raw.get("radius", 8) * self.scale),
            strike_fill=parse_color(raw.get("strike"), style) if raw.get("strike") else None,
            underline_fill=parse_color(raw.get("underline"), style) if raw.get("underline") else None,
            line_spacing=float(raw.get("line_spacing", 1.02 if text_style in {"display", "sticker"} else 1.1)),
        )
        if raw.get("shadow", text_style == "display" and not box_fill):
            image = add_shadow(image, blur=int(14 * self.scale), offset=(0, int(8 * self.scale)), opacity=120)
        return image

    # render an emoji element with the shared emoji renderer and turn its failures into board errors
    def _render_emoji(self, element: Element) -> Image.Image:
        from fetch_asset import render_emoji

        size = int(float(element.raw.get("size", 160)) * self.scale)
        try:
            image, _ = render_emoji(str(element.raw.get("text", "🔥")), size, element.raw.get("font"))
        except (SystemExit, ValueError) as exc:
            raise BoardError(
                f"emoji element '{element.id}' cannot be rendered ({exc}); "
                "choose a glyph supported by the installed font or provide a rendered image asset"
            ) from None
        return image

    # load crop fit and optionally grayscale tint and card treat an image element
    def _render_image(self, element: Element) -> Image.Image:
        raw, style = element.raw, self.style
        path = self.resolve_path(raw.get("file", ""))
        if not raw.get("file") or not path.exists():
            raise BoardError(f"image element '{element.id}' file not found: {raw.get('file')}")
        image = Image.open(path).convert("RGBA")
        if raw.get("crop"):
            x, y, w, h = raw["crop"]
            if all(0 <= v <= 1 for v in (x, y, w, h)):
                x, y, w, h = x * image.width, y * image.height, w * image.width, h * image.height
            image = image.crop((int(x), int(y), int(x + w), int(y + h)))
        max_w = self.px(raw.get("w", 0.6), "x")
        max_h = self.px(raw.get("h", 0.7), "y")
        image = fit_within(image, max_w, max_h)
        if raw.get("grayscale"):
            gray = image.convert("L").convert("RGBA")
            gray.putalpha(image.getchannel("A"))
            image = gray
        if raw.get("tint"):
            image = tint_image(image, parse_color(raw["tint"], style))
        if raw.get("card"):
            image = apply_card_treatment(image, style, radius=raw.get("radius"), border=raw.get("border"), shadow=raw.get("shadow"))
        elif raw.get("shadow"):
            image = add_shadow(image)
        return image

    # probe a clip and set up its decode state box size and optional audio contribution without decoding yet
    def _prepare_video(self, element: Element) -> None:
        raw = element.raw
        path = self.resolve_path(raw.get("file", ""))
        if not raw.get("file") or not path.exists():
            raise BoardError(f"video element '{element.id}' file not found: {raw.get('file')}")
        source_start = float(raw.get("source_start", 0.0))
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                                "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, text=True)
        try:
            info = json.loads(probe.stdout)
            src_w = int(info["streams"][0]["width"])
            src_h = int(info["streams"][0]["height"])
            src_duration = float(info["format"]["duration"])
        except (KeyError, ValueError, IndexError, json.JSONDecodeError) as exc:
            raise BoardError(f"cannot probe video '{path}'") from exc
        needed = element.end - element.start
        if source_start + needed > src_duration + 0.05:
            raise BoardError(f"video element '{element.id}' needs {needed:.2f}s from {source_start:.2f}s but the clip is {src_duration:.2f}s")
        fit = str(raw.get("fit", "cover"))
        # derive the missing box dimension from the clip aspect ratio or fall back to full frame
        if raw.get("w") or raw.get("h"):
            box_w, box_h = self.px(raw.get("w", 0.6), "x"), self.px(raw.get("h", 0.6), "y")
            if not raw.get("h"):
                box_h = int(box_w * src_h / src_w)
            if not raw.get("w"):
                box_w = int(box_h * src_w / src_h)
        else:
            box_w, box_h = self.width, self.height
        box_w, box_h = max(2, box_w // 2 * 2), max(2, box_h // 2 * 2)
        element.video = {"path": path, "source_start": source_start, "size": (box_w, box_h), "fit": fit,
                         "proc": None, "next_time": element.start, "frame": None, "audio": bool(raw.get("audio", False))}
        element.image = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        if element.video["audio"]:
            self.clip_audio.append({"path": path, "source_start": source_start, "at": element.start, "duration": needed,
                                    "gain_db": float(raw.get("audio_gain_db", 0.0))})

    # start an ffmpeg process that streams raw rgba frames of the clip at the board frame rate
    def _open_video(self, element: Element) -> None:
        video = element.video
        assert video is not None
        box_w, box_h = video["size"]
        # cover scales up and crops while fit scales down and pads with transparency
        if video["fit"] == "cover":
            vf = f"scale={box_w}:{box_h}:force_original_aspect_ratio=increase,crop={box_w}:{box_h}"
        else:
            vf = f"scale={box_w}:{box_h}:force_original_aspect_ratio=decrease,pad={box_w}:{box_h}:(ow-iw)/2:(oh-ih)/2:color=black@0"
        command = ["ffmpeg", "-loglevel", "error", "-ss", f"{video['source_start']:.3f}", "-i", str(video["path"]),
                   "-t", f"{element.end - element.start + 0.5:.3f}", "-vf", f"{vf},fps={self.fps},format=rgba",
                   "-f", "rawvideo", "-pix_fmt", "rgba", "-"]
        video["proc"] = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    # return the decoded frame for time t by consuming the stream forward and keeping the last frame
    def _video_frame(self, element: Element, t: float) -> Image.Image:
        video = element.video
        assert video is not None
        if video["proc"] is None:
            self._open_video(element)
        box_w, box_h = video["size"]
        nbytes = box_w * box_h * 4
        # advance to the frame for time t (frames are consumed in order)
        target_index = int(round((t - element.start) * self.fps))
        current = video.get("index", -1)
        while current < target_index:
            data = video["proc"].stdout.read(nbytes) if video["proc"].stdout else b""
            if len(data) < nbytes:
                break
            video["frame"] = Image.frombuffer("RGBA", (box_w, box_h), data, "raw", "RGBA", 0, 1)
            current += 1
        video["index"] = current
        frame = video["frame"] or element.image
        if element.raw.get("card"):
            frame = apply_card_treatment(frame, self.style, radius=element.raw.get("radius"), border=element.raw.get("border"), shadow=False)
        return frame

    # compute the settled rectangle of an element from its anchor and canvas fractions
    def _place(self, element: Element) -> None:
        image = element.image
        assert image is not None
        width, height = image.size
        if element.rotate:
            probe = image.rotate(element.rotate, expand=True)
            width, height = probe.size
        ax, ay = self._anchor_offsets(element.anchor)
        cx, cy = self.px(element.x, "x"), self.px(element.y, "y")
        left = int(round(cx - width * ax))
        top = int(round(cy - height * ay))
        element.rect = (left, top, width, height)

    # map an anchor name to the fractional offsets of the sprite origin
    @staticmethod
    def _anchor_offsets(anchor: str) -> tuple[float, float]:
        table = {"center": (0.5, 0.5), "left": (0.0, 0.5), "right": (1.0, 0.5), "top": (0.5, 0.0), "bottom": (0.5, 1.0),
                 "top-left": (0.0, 0.0), "top-right": (1.0, 0.0), "bottom-left": (0.0, 1.0), "bottom-right": (1.0, 1.0)}
        if anchor not in table:
            raise BoardError(f"unknown anchor '{anchor}'; choose {sorted(table)}")
        return table[anchor]

    # ---- layout QC
    # sample each beat at its settled moments and report the tight boxes of the visible elements
    def layout_manifest(self) -> dict[str, Any]:
        frames = []
        for beat in self.beats:
            times = sorted({min(beat.end - 0.01, element.enter_end + 0.05) for element in beat.elements} | {min(beat.end - 0.01, beat.start + 0.05)})
            for time in times:
                elements = []
                for element in beat.elements:
                    if element.start <= time < element.end:
                        left, top, width, height = element.rect
                        # shadows/padding extend the sprite; measure the tight alpha box instead
                        tight = self._tight_box(element)
                        elements.append({"id": element.id, "rect": {"x": left + tight[0], "y": top + tight[1], "width": tight[2], "height": tight[3]},
                                         "allow_overlap_with": element.allow_overlap_with})
                if elements:
                    frames.append({"time": round(time, 3), "beat": beat.id, "elements": elements})
        return {"canvas": {"width": self.width, "height": self.height}, "frames": frames}

    # measure the opaque bounding box of a sprite so shadows and padding do not count as content
    def _tight_box(self, element: Element) -> tuple[int, int, int, int]:
        image = element.image
        assert image is not None
        if element.kind == "video":
            return (0, 0, image.width, image.height)
        if element.rotate:
            image = image.rotate(element.rotate, expand=True)
        box = image.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox()
        if not box:
            return (0, 0, image.width, image.height)
        return (box[0], box[1], max(1, box[2] - box[0]), max(1, box[3] - box[1]))

    # run layout qc on every sampled frame and reject elements that leave the canvas overlap or enter the caption rail
    def check_layout(self) -> dict[str, Any]:
        manifest = self.layout_manifest()
        safe_top = self.height * (1 - self.safe_bottom)
        problems: list[str] = []
        for frame in manifest["frames"]:
            try:
                validate_frame(frame["elements"], width=self.width, height=self.height, time=frame["time"])
            except LayoutQCError as exc:
                problems.append(str(exc))
            if self.safe_bottom:
                for element in frame["elements"]:
                    if element["rect"]["y"] + element["rect"]["height"] > safe_top + 0.5:
                        problems.append(f"element '{element['id']}' enters the caption rail at {frame['time']:.2f}s")
        if problems:
            raise BoardError("layout QC failed:\n" + "\n".join(problems))
        return manifest

    # ---- rendering
    # compute the sprite position scale and alpha at time t by applying the enter exit and motion animations
    def _transform(self, element: Element, t: float) -> tuple[Image.Image, int, int, float]:
        """Return (sprite, left, top, alpha) for time t, applying enter/exit/motion."""
        sprite = self._video_frame(element, t) if element.kind == "video" else element.image
        assert sprite is not None
        left, top, _, _ = element.rect
        alpha = element.opacity
        scale = 1.0
        dx = dy = 0.0
        blur_dir: tuple[float, float] | None = None
        blocks: float | None = None
        wipe: float | None = None
        life = element.end - element.start
        age = t - element.start
        # enter
        if element.enter_duration > 0 and age < element.enter_duration:
            p = clamp(age / element.enter_duration)
            kind = element.enter
            if kind == "pop":
                scale = 0.7 + 0.3 * ease_out_back(p)
                alpha *= clamp(p / 0.4)
            elif kind == "grow":
                scale = max(0.01, ease_out_cubic(p))
            elif kind == "fade":
                alpha *= ease_out_cubic(p)
            elif kind.startswith("slide") or kind.startswith("whip"):
                e = ease_out_expo(p)
                dist_x = self.width * (0.6 if kind.startswith("whip") else 0.25)
                dist_y = self.height * 0.35
                if kind.endswith("left"):
                    dx = -(1 - e) * dist_x
                elif kind.endswith("right"):
                    dx = (1 - e) * dist_x
                elif kind.endswith("top"):
                    dy = -(1 - e) * dist_y
                else:
                    dy = (1 - e) * dist_y
                if p < 0.85:
                    blur_dir = (dx, dy)
                alpha *= clamp(p / 0.25)
            elif kind == "blocks":
                blocks = ease_out_cubic(p)
            elif kind == "wipe":
                wipe = ease_out_cubic(p)
            elif kind == "type":
                pass
        # exit
        if element.exit != "none" and element.exit_duration > 0 and age > life - element.exit_duration:
            p = clamp((age - (life - element.exit_duration)) / element.exit_duration)
            kind = element.exit
            if kind == "fade":
                alpha *= 1 - p
            elif kind == "pop":
                scale *= 1 - 0.3 * ease_in_cubic(p)
                alpha *= 1 - p
            else:
                e = ease_in_cubic(p)
                if kind.endswith("left"):
                    dx -= e * self.width * 0.3
                elif kind.endswith("right"):
                    dx += e * self.width * 0.3
                elif kind.endswith("top"):
                    dy -= e * self.height * 0.35
                else:
                    dy += e * self.height * 0.35
                alpha *= 1 - clamp((p - 0.6) / 0.4)
        # motion
        amount = element.motion_amount
        if element.motion == "kenburns":
            scale *= 1.0 + 0.06 * amount * clamp(age / max(life, 1e-6))
        elif element.motion == "float":
            dy += math.sin(age * 2 * math.pi * 0.45) * 8 * amount * self.scale
        elif element.motion == "shake" and age < 0.35:
            rng = random.Random(int(age * 60) + element.seed)
            dx += rng.uniform(-5, 5) * amount * self.scale
            dy += rng.uniform(-4, 4) * amount * self.scale
        elif element.motion == "drift":
            dx += (age / max(life, 1e-6) - 0.5) * 40 * amount * self.scale
        # apply
        # the type enter re renders the code card each frame with a growing visible character count
        if element.enter == "type" and age < element.enter_duration and element.kind == "code":
            visible = int(element.type_chars * clamp(age / element.enter_duration))
            raw = element.raw
            code = raw.get("code") or self.resolve_path(raw["file"]).read_text(encoding="utf-8")
            sprite = render_code_card(code.rstrip("\n"), str(raw.get("lang", "javascript")), self.fonts.get("mono", int(raw.get("size", 34) * self.scale)),
                                      self.style, width=self.px(raw["w"], "x") if raw.get("w") else None, title=raw.get("title"), chars_visible=visible)
        if element.rotate:
            sprite = sprite.rotate(element.rotate, resample=Image.BICUBIC, expand=True)
        # resize around the sprite center so pops and grows stay anchored in place
        if abs(scale - 1.0) > 1e-3:
            new_size = (max(1, int(sprite.width * scale)), max(1, int(sprite.height * scale)))
            resized = sprite.resize(new_size, Image.BILINEAR)
            left += (sprite.width - new_size[0]) // 2
            top += (sprite.height - new_size[1]) // 2
            sprite = resized
        if blocks is not None:
            mask = blocks_mask(sprite.size, blocks, element.seed, block=max(8, int(28 * self.scale)))
            sprite = sprite.copy()
            sprite.putalpha(ImageChops.multiply(sprite.getchannel("A"), mask))
            if blocks < 0.6:
                sprite = rgb_shift(sprite, int(6 * self.scale))
        if wipe is not None:
            mask = Image.new("L", sprite.size, 0)
            ImageDraw.Draw(mask).rectangle((0, 0, int(sprite.width * wipe), sprite.height), fill=255)
            sprite = sprite.copy()
            sprite.putalpha(ImageChops.multiply(sprite.getchannel("A"), mask))
        if blur_dir is not None:
            sprite = self._smear(sprite, blur_dir)
            left += int(min(0, blur_dir[0]))
            top += int(min(0, blur_dir[1]))
        return sprite, int(round(left + dx)), int(round(top + dy)), clamp(alpha)

    # stack a few faded copies of the sprite along the motion direction to fake motion blur
    @staticmethod
    def _smear(sprite: Image.Image, direction: tuple[float, float]) -> Image.Image:
        dx, dy = direction
        length = math.hypot(dx, dy)
        if length < 2:
            return sprite
        steps = 4
        span_x, span_y = int(abs(dx) * 0.35), int(abs(dy) * 0.35)
        canvas = Image.new("RGBA", (sprite.width + span_x, sprite.height + span_y), (0, 0, 0, 0))
        base_x = 0 if dx >= 0 else span_x
        base_y = 0 if dy >= 0 else span_y
        for i in range(steps, 0, -1):
            frac = i / steps
            ghost = sprite.copy()
            ghost.putalpha(ghost.getchannel("A").point(lambda a, f=frac: int(a * 0.22 * (1 - f) + a * 0.05)))
            ox = int(base_x - (dx / length) * span_x * frac) if dx else base_x
            oy = int(base_y - (dy / length) * span_y * frac) if dy else base_y
            canvas.alpha_composite(ghost, (max(0, ox), max(0, oy)))
        canvas.alpha_composite(sprite, (base_x, base_y))
        return canvas

    # compose the full frame at time t from the beat background and its visible elements in z order
    def render_frame(self, t: float) -> Image.Image:
        beat = next((b for b in self.beats if b.start <= t < b.end), None)
        color = parse_color(beat.bg.get("color", self.style["bg"]) if beat else self.style["bg"], self.style)
        frame = Image.new("RGBA", (self.width, self.height), color)
        if beat is None:
            return frame
        if beat.bg_image is not None:
            bg = beat.bg_image
            motion = beat.bg.get("motion")
            # ken burns zooms the background slowly by cropping a shrinking center window
            if motion == "kenburns":
                p = clamp((t - beat.start) / max(beat.end - beat.start, 1e-6))
                zoom = 1.0 + 0.07 * p
                crop_w, crop_h = int(self.width / zoom), int(self.height / zoom)
                left, top = (self.width - crop_w) // 2, (self.height - crop_h) // 2
                bg = bg.crop((left, top, left + crop_w, top + crop_h)).resize((self.width, self.height), Image.BILINEAR)
            frame.alpha_composite(bg)
        for element in sorted(beat.elements, key=lambda e: e.z):
            if not (element.start <= t < element.end):
                continue
            sprite, left, top, alpha = self._transform(element, t)
            if alpha <= 0.0:
                continue
            if alpha < 0.999:
                sprite = sprite.copy()
                sprite.putalpha(sprite.getchannel("A").point(lambda a, m=alpha: int(a * m)))
            frame.alpha_composite(sprite, (left, top)) if 0 <= left and 0 <= top and left + sprite.width <= self.width and top + sprite.height <= self.height else self._paste_clipped(frame, sprite, left, top)
        return frame

    # composite a sprite that partly leaves the frame by cropping it to the visible region
    @staticmethod
    def _paste_clipped(frame: Image.Image, sprite: Image.Image, left: int, top: int) -> None:
        x0, y0 = max(0, left), max(0, top)
        x1, y1 = min(frame.width, left + sprite.width), min(frame.height, top + sprite.height)
        if x1 <= x0 or y1 <= y0:
            return
        crop = sprite.crop((x0 - left, y0 - top, x1 - left, y1 - top))
        frame.alpha_composite(crop, (x0, y0))

    # the time in a beat when every enter animation has finished
    def settled_time(self, beat: Beat) -> float:
        latest = max([element.enter_end for element in beat.elements] + [beat.start])
        return min(beat.end - 0.02, latest + 0.05)

    # ---- audio
    # mix narration sfx music and clip audio into one mono wav and report whether anything is audible
    def build_audio(self, out_wav: Path) -> bool:
        """Mix narration, SFX, music, and clip audio into one 48k stereo WAV. Returns False if silent."""
        total_samples = int(self.duration * SAMPLE_RATE)
        mix = np.zeros(total_samples, dtype=np.float32)
        has_audio = False

        # add a sample buffer into the mix at a time offset with a gain clipping at the board end
        def add(sample: np.ndarray, at_s: float, gain: float = 1.0) -> None:
            start = max(0, int(at_s * SAMPLE_RATE))
            end = min(total_samples, start + len(sample))
            if end > start:
                mix[start:end] += sample[: end - start] * gain

        if self.narration:
            add(self._decode(self.narration, 0.0, self.duration), 0.0)
            has_audio = True
        sfx_mode = str(self.spec.get("sfx", "sparse"))
        if sfx_mode not in {"off", "sparse", "all"}:
            raise BoardError("sfx must be off, sparse, or all")
        if sfx_mode != "off":
            gain = 10 ** (float(self.spec.get("sfx_gain_db", -18.0)) / 20)
            for at, kind in self.sfx_events:
                if sfx_mode == "sparse" and kind not in SPARSE_SFX:
                    continue
                add(synth_sfx(kind, seed=int(at * 1000)), at, gain)
                has_audio = True
        music = self.spec.get("music")
        if music:
            music_spec = music if isinstance(music, dict) else {"file": music}
            path = self.resolve_path(music_spec["file"])
            if not path.exists():
                raise BoardError(f"music file not found: {path}")
            track = self._decode(path, float(music_spec.get("source_start", 0.0)), self.duration, loop=True)[:total_samples].copy()
            gain = 10 ** (float(music_spec.get("gain_db", -20.0)) / 20)
            fade = float(music_spec.get("fade_out", 2.0))
            if fade > 0 and len(track):
                n = min(len(track), int(fade * SAMPLE_RATE))
                track[-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)
            add(track, 0.0, gain)
            has_audio = True
        for clip in self.clip_audio:
            add(self._decode(clip["path"], clip["source_start"], clip["duration"]), clip["at"], 10 ** (clip["gain_db"] / 20))
            has_audio = True
        # normalize peaks above the ceiling so the mix never clips
        mix = np.nan_to_num(mix)
        peak = float(np.max(np.abs(mix))) if total_samples else 0.0
        if peak > 0.98:
            mix *= 0.98 / peak
        pcm = (mix * 32767).astype(np.int16)
        with wave.open(str(out_wav), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(pcm.tobytes())
        return has_audio and peak > 1e-4

    # decode any audio file to mono float samples with ffmpeg optionally looping it to fill the duration
    @staticmethod
    def _decode(path: Path, start: float, duration: float, loop: bool = False) -> np.ndarray:
        command = ["ffmpeg", "-loglevel", "error", "-ss", f"{start:.3f}"]
        if loop:
            command += ["-stream_loop", "-1"]
        command += ["-i", str(path), "-t", f"{duration:.3f}", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
        result = subprocess.run(command, capture_output=True)
        if result.returncode != 0:
            raise BoardError(f"ffmpeg could not decode audio from {path}: {result.stderr.decode()[-300:]}")
        return np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0

    # ---- output
    # render every frame into ffmpeg then mux the audio mix or silence into the final mp4
    def render(self, out: Path, *, crf: int = 18) -> None:
        require_new_output(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        total_frames = int(round(self.duration * self.fps))
        with tempfile.TemporaryDirectory() as tmp:
            audio_wav = Path(tmp) / "mix.wav"
            has_audio = self.build_audio(audio_wav)
            silent_video = Path(tmp) / "video.mp4"
            command = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{self.width}x{self.height}",
                       "-r", f"{self.fps:g}", "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
                       "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(silent_video)]
            proc = subprocess.Popen(command, stdin=subprocess.PIPE)
            assert proc.stdin is not None
            report_every = max(1, total_frames // 10)
            for index in range(total_frames):
                t = (index + 0.5) / self.fps
                frame = self.render_frame(t)
                proc.stdin.write(frame.convert("RGB").tobytes())
                if index % report_every == 0:
                    self.log(f"  frame {index}/{total_frames} ({t:.1f}s)")
            proc.stdin.close()
            if proc.wait() != 0:
                raise BoardError("ffmpeg failed while encoding the board video")
            for beat in self.beats:
                for element in beat.elements:
                    if element.video and element.video.get("proc"):
                        element.video["proc"].kill()
            if has_audio:
                audio_args = ["-i", str(audio_wav), "-c:a", "aac", "-b:a", "192k"]
            else:
                audio_args = ["-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo", "-c:a", "aac", "-b:a", "128k"]
            mux = ["ffmpeg", "-n", "-loglevel", "error", "-i", str(silent_video), *audio_args, "-map", "0:v:0", "-map", "1:a:0",
                   "-c:v", "copy", "-shortest", "-movflags", "+faststart", str(out)]
            if subprocess.run(mux).returncode != 0:
                raise BoardError("ffmpeg failed while muxing audio")
        self.log(f"board → {out} ({self.duration:.2f}s, {self.width}x{self.height}@{self.fps:g}, audio={'yes' if has_audio else 'silent'})")

    # save a grid of settled frames per beat with timing and spoken words under each thumbnail
    def contact_sheet(self, out: Path, columns: int = 4) -> None:
        require_new_output(out)
        thumb_w = 480
        thumb_h = int(thumb_w * self.height / self.width)
        label_h = 34
        rows = math.ceil(len(self.beats) / columns)
        sheet = Image.new("RGB", (columns * thumb_w, rows * (thumb_h + label_h)), (24, 24, 30))
        draw = ImageDraw.Draw(sheet)
        font = self.fonts.get("body", 15)
        for index, beat in enumerate(self.beats):
            frame = self.render_frame(self.settled_time(beat)).convert("RGB").resize((thumb_w, thumb_h), Image.BILINEAR)
            x, y = (index % columns) * thumb_w, (index // columns) * (thumb_h + label_h)
            sheet.paste(frame, (x, y))
            draw.text((x + 6, y + thumb_h + 4), f"{beat.id}  {beat.start:.2f}-{beat.end:.2f}s  ({len(beat.elements)} el)", font=font, fill=(235, 235, 240))
            speech = " ".join(w["text"] for w in (self.words or []) if beat.start <= w["start"] < beat.end)
            draw.text((x + 6, y + thumb_h + 19), speech[:62], font=font, fill=(150, 150, 165))
        out.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out)
        self.log(f"contact sheet → {out}")

    # summarize the resolved beats elements fonts warnings and sfx events as json ready data
    def timeline(self) -> dict[str, Any]:
        return {
            "duration": round(self.duration, 3), "fps": self.fps, "width": self.width, "height": self.height,
            "fonts": self.fonts.describe(), "warnings": self.warnings,
            "beats": [{
                "id": beat.id, "start": round(beat.start, 3), "end": round(beat.end, 3),
                "elements": [{"id": e.id, "kind": e.kind, "start": round(e.start, 3), "end": round(e.end, 3), "enter": e.enter,
                              "rect": {"x": e.rect[0], "y": e.rect[1], "width": e.rect[2], "height": e.rect[3]}} for e in beat.elements],
            } for beat in self.beats],
            "sfx_events": [{"at": round(t, 3), "kind": k} for t, k in self.sfx_events],
        }


# ---------------------------------------------------------------- sfx synthesis
# synthesize a short normalized whoosh pop or glitch sound from noise and sine sweeps
def synth_sfx(kind: str, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if kind == "whoosh":
        n = int(0.28 * SAMPLE_RATE)
        noise = rng.standard_normal(n).astype(np.float32)
        t = np.linspace(0, 1, n, dtype=np.float32)
        env = np.clip(np.sin(np.pi * t), 0.0, 1.0) ** 1.5
        # crude band sweep: mix of low-passed noise moving upward
        kernel = np.ones(24, dtype=np.float32) / 24
        low = np.convolve(noise, kernel, mode="same")
        signal = (low * (1 - t) + noise * 0.35 * t) * env
    elif kind == "pop":
        n = int(0.09 * SAMPLE_RATE)
        t = np.linspace(0, 0.09, n, dtype=np.float32)
        freq = 520 * np.exp(-t * 18) + 180
        signal = np.sin(2 * np.pi * np.cumsum(freq) / SAMPLE_RATE) * np.exp(-t * 40)
    elif kind == "glitch":
        n = int(0.16 * SAMPLE_RATE)
        noise = rng.standard_normal(n).astype(np.float32)
        gate = (rng.random(n // 240) > 0.45).repeat(240)[:n]
        signal = noise * gate * np.linspace(1, 0.2, n, dtype=np.float32) * 0.6
    else:
        return np.zeros(1, dtype=np.float32)
    signal = np.nan_to_num(signal.astype(np.float32))
    peak = float(np.max(np.abs(signal))) or 1.0
    return (signal / peak).astype(np.float32)


# ---------------------------------------------------------------- EDL writer
# write a ready edl that uses the rendered board as its single source with optional caption provenance
def write_edl(board: Board, video_path: Path, edl_path: Path, *, subtitles: Path | None, workflow: str) -> None:
    require_new_output(edl_path)
    edit_dir = edl_path.parent
    edl: dict[str, Any] = {
        "version": 2,
        "status": "ready",
        "task_context": {
            "operation": "creation",
            "workflow": workflow,
            "media_origin": "generated_board_and_narration" if board.narration else "generated_board",
            "summary": f"Kinetic board of {len(board.beats)} beats over {board.duration:.1f}s rendered by helpers/board.py.",
        },
        "sources": {"board": str(video_path.resolve())},
        "ranges": [{"source": "board", "start": 0.0, "end": round(board.duration, 3), "beat": "BOARD",
                    "quote": "", "reason": "The board is the complete visual base with narration already muxed."}],
        "grade": "none",
        "overlays": [],
        "total_duration_s": round(board.duration, 3),
    }
    # captions need the alignment so the edl can record where the words came from
    if subtitles:
        if not subtitles.exists():
            raise BoardError(f"subtitles file not found: {subtitles}")
        if not board.spec.get("alignment"):
            raise BoardError("captions require the board 'alignment' (timestamped narration) for provenance")
        alignment_path = board.resolve_path(board.spec["alignment"])
        try:
            rel_align = str(alignment_path.relative_to(edit_dir.resolve()))
            rel_subs = str(subtitles.resolve().relative_to(edit_dir.resolve()))
        except ValueError:
            rel_align, rel_subs = str(alignment_path), str(subtitles.resolve())
        edl["subtitles"] = rel_subs
        edl["captions"] = {"provenance": {"kind": "narration_alignment", "files": [rel_align]},
                           "safe_region": {"x": 0.0, "y": round(1 - max(board.safe_bottom, 0.16), 3), "width": 1.0,
                                           "height": round(max(board.safe_bottom, 0.16), 3)}}
    edl_path.parent.mkdir(parents=True, exist_ok=True)
    with edl_path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(edl, indent=2) + "\n")
    print(f"edl → {edl_path}")


# ---------------------------------------------------------------- CLI
# build the command line parser for the board renderer
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("board", type=Path, help="beat sheet JSON")
    parser.add_argument("-o", "--output", type=Path, help="output MP4")
    parser.add_argument("--preview", action="store_true", help="render at half resolution for fast iteration")
    parser.add_argument("--crf", type=int, default=18)
    parser.add_argument("--contact", type=Path, help="write a per-beat contact sheet PNG (settled frames)")
    parser.add_argument("--frame", type=float, action="append", help="write a single full-resolution frame at this time (repeatable)")
    parser.add_argument("--frames-dir", type=Path, help="directory for --frame outputs (default: next to the output)")
    parser.add_argument("--resolve-only", action="store_true", help="resolve timing and layout, print the timeline, do not render")
    parser.add_argument("--manifest", type=Path, help="write the layout manifest JSON here (default: <output>.layout_manifest.json)")
    parser.add_argument("--timeline", type=Path, help="write the resolved timeline JSON here (default: <output>.timeline.json)")
    parser.add_argument("--write-edl", type=Path, help="write a ready EDL that uses the rendered board as its single source")
    parser.add_argument("--subtitles", type=Path, help=".ass captions built from the narration alignment, for --write-edl")
    parser.add_argument("--workflow", default="narrated visual board", help="task_context.workflow label for --write-edl")
    parser.add_argument("--skip-layout-qc", action="store_true", help="render even when layout QC fails (never for delivery)")
    return parser


# reject existing outputs including dangling symbolic links
def require_new_output(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise BoardError(f"output already exists use a new name: {path}")


# command line entry that resolves the board runs layout qc writes the timeline and renders the requested outputs
def main() -> None:
    args = build_parser().parse_args()
    spec = json.loads(args.board.read_text(encoding="utf-8"))
    base_dir = args.board.resolve().parent
    try:
        if args.write_edl and not args.output:
            raise BoardError("--write-edl needs -o/--output")
        timeline = args.timeline or (args.output.with_suffix(".timeline.json") if args.output else args.board.with_suffix(".timeline.json"))
        manifest = args.manifest or (args.output.with_suffix(".layout_manifest.json") if args.output else args.board.with_suffix(".layout_manifest.json"))
        targets = [timeline, manifest]
        if not args.resolve_only:
            targets += [p for p in (args.output, args.contact, args.write_edl) if p is not None]
            if args.frame:
                folder = args.frames_dir or (args.output.parent if args.output else base_dir / "verify")
                targets += [folder / f"board_{t:07.2f}s.png" for t in args.frame]
        if len({p.resolve() for p in targets}) != len(targets):
            raise BoardError("each output must use a distinct path")
        for target in targets:
            require_new_output(target)
        board = Board(spec, base_dir, preview=args.preview)
        print(f"resolved {len(board.beats)} beats, {sum(len(b.elements) for b in board.beats)} elements, {board.duration:.2f}s")
        print("fonts: " + ", ".join(f"{role}={path}" for role, path in board.fonts.describe().items()))
        for warning in board.warnings:
            print(f"warning: {warning}")
        try:
            manifest = board.check_layout()
        except BoardError as exc:
            if not args.skip_layout_qc:
                raise
            print(f"warning: {exc}")
            manifest = board.layout_manifest()
        timeline_target = args.timeline or (args.output.with_suffix(".timeline.json") if args.output else args.board.with_suffix(".timeline.json"))
        timeline_target.parent.mkdir(parents=True, exist_ok=True)
        timeline_target.write_text(json.dumps(board.timeline(), indent=1) + "\n", encoding="utf-8")
        manifest_target = args.manifest or (args.output.with_suffix(".layout_manifest.json") if args.output else args.board.with_suffix(".layout_manifest.json"))
        manifest_target.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
        if args.resolve_only:
            for beat in board.beats:
                print(f"{beat.id:>14} {beat.start:7.2f}-{beat.end:7.2f}s  " + ", ".join(f"{e.id}:{e.enter}@{e.start:.2f}" for e in beat.elements))
            return
        if args.contact:
            board.contact_sheet(args.contact)
        if args.frame:
            frames_dir = args.frames_dir or (args.output.parent if args.output else base_dir / "verify")
            frames_dir.mkdir(parents=True, exist_ok=True)
            for t in args.frame:
                target = frames_dir / f"board_{t:07.2f}s.png"
                board.render_frame(t).convert("RGB").save(target)
                print(f"frame → {target}")
        if args.output:
            board.render(args.output, crf=args.crf)
            if args.write_edl:
                write_edl(board, args.output, args.write_edl, subtitles=args.subtitles, workflow=args.workflow)
        elif args.write_edl:
            raise BoardError("--write-edl needs -o/--output so the EDL can reference the rendered board")
    except BoardError as exc:
        raise SystemExit(f"board error: {exc}")


if __name__ == "__main__":
    main()
