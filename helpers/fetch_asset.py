#!/usr/bin/env python3
"""Fetch small visual assets with provenance: web images, brand logos, emoji.

Every subcommand writes ``<out>.json`` next to the asset recording where it
came from, when, and a rights note the editor must review before delivery.

  image  Download an image URL (news photo, meme, chart) with a browser user
         agent, verify it decodes, optionally downscale.
  logo   Fetch a brand mark from the Simple Icons CDN by slug (e.g. ``go``,
         ``github``, ``rust``) as SVG, and rasterize it to a transparent PNG.
  emoji  Render one or more emoji (e.g. ``👉`` or ``🔥💀``) to a transparent
         PNG using the platform color emoji font.

Usage:
    python helpers/fetch_asset.py image https://example.com/photo.jpg -o edit/assets/photo.jpg --max-width 1600
    python helpers/fetch_asset.py logo go -o edit/assets/go.png --color 00ADD8 --size 600
    python helpers/fetch_asset.py emoji "👉" -o edit/assets/point.png --size 240
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from ._asset_io import staged_asset, download
except ImportError:
    from _asset_io import staged_asset, download


USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
SIMPLE_ICONS = "https://cdn.simpleicons.org/{slug}/{color}"
# Color emoji fonts are bitmap fonts: PIL can only open them at an exact strike size.
EMOJI_FONTS = [
    ("/System/Library/Fonts/Apple Color Emoji.ttc", (160, 96, 64, 48, 40, 32, 20)),
    ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", (109,)),
    ("/usr/share/fonts/noto/NotoColorEmoji.ttf", (109,)),
]


# current utc time as an iso string
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# write the provenance json next to an asset
def write_sidecar(out: Path, payload: dict[str, Any]) -> None:
    out.with_suffix(out.suffix + ".json").write_text(
        json.dumps(payload, indent=1) + "\n", encoding="utf-8"
    )


# hash a file in chunks and return the hex digest
def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------- image
# download an image url verify it decodes optionally downscale it and record its provenance
@staged_asset
def fetch_image(args: argparse.Namespace) -> None:
    from PIL import Image

    parsed = urlparse(args.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit(f"image needs an http(s) URL, got {args.url}")
    out: Path = args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.max_width is not None and args.max_width <= 0:
        raise ValueError("max width must be positive")
    if out.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("image output must be png jpg or webp")
    data, content_type, final_url = download(
        args.url,
        headers={"User-Agent": USER_AGENT, "Accept": "image/*"},
        max_bytes=40 * 1024 * 1024,
    )
    # write to a temp part file first so a bad download never leaves a broken asset behind
    tmp = out.with_suffix(out.suffix + ".part")
    tmp.write_bytes(data)
    try:
        with Image.open(tmp) as image:
            width, height = image.size
            if width * height > 80_000_000:
                raise ValueError("image exceeds 80 million pixels")
            image.load()
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError(
                    "animated images are not supported by the still image helper"
                )
            converted = image.convert(
                "RGB" if out.suffix.lower() in {".jpg", ".jpeg"} else "RGBA"
            )
            if args.max_width and width > args.max_width:
                ratio = args.max_width / width
                converted = converted.resize(
                    (args.max_width, max(1, int(height * ratio))), Image.LANCZOS
                )
            converted.save(out)
            width, height = converted.size
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"downloaded data is not a decodable image ({content_type}): {exc}"
        )
    finally:
        tmp.unlink(missing_ok=True)
    write_sidecar(
        out,
        {
            "kind": "web_image",
            "source_url": args.url,
            "final_url": final_url,
            "fetched_at": utc_now(),
            "content_type": content_type,
            "sha256": sha256_of(out),
            "pixels": {"width": width, "height": height},
            "rights": args.rights
            or "unknown; confirm license or fair-use basis before delivery",
            "credit": args.credit,
        },
    )
    print(f"image → {out} ({width}x{height}, {content_type})")


# ---------------------------------------------------------------- logo
# rasterize an svg to a transparent png with cairosvg or fall back to a headless chrome screenshot
def rasterize_svg(svg_path: Path, out: Path, size: int) -> str:
    """Rasterize an SVG to a transparent PNG using cairosvg or a headless browser."""
    try:
        import cairosvg  # type: ignore

        cairosvg.svg2png(
            url=str(svg_path), write_to=str(out), output_width=size, output_height=size
        )
        return "cairosvg"
    except Exception:
        pass
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from web_shot import find_chrome, run_chrome_screenshot  # noqa: E402

    chrome = find_chrome()
    if not chrome:
        raise SystemExit(
            "cannot rasterize SVG: install cairosvg (`pip install cairosvg`) or a Chrome/Chromium browser"
        )
    html = svg_path.with_suffix(".html")
    html.write_text(
        f'<html><body style="margin:0;background:transparent">'
        f'<img src="{svg_path.name}" style="width:{size}px;height:{size}px;display:block"></body></html>',
        encoding="utf-8",
    )
    try:
        run_chrome_screenshot(
            chrome,
            html.as_uri(),
            out,
            [
                "--default-background-color=00000000",
                f"--window-size={size},{size}",
                "--virtual-time-budget=1500",
            ],
            timeout=60,
        )
    finally:
        html.unlink(missing_ok=True)
    return "chrome"


# fetch a simple icons logo by slug as svg and rasterize and trim it when a png is requested
@staged_asset
def fetch_logo(args: argparse.Namespace) -> None:
    from PIL import Image

    slug = args.slug.lower().strip()
    if not re.fullmatch(r"[a-z0-9.+-]+", slug):
        raise SystemExit(
            "logo slug must be a Simple Icons slug like 'go', 'github', 'rust', 'dotnet'"
        )
    color = args.color.lstrip("#").lower()
    if not re.fullmatch(r"[0-9a-f]{6}", color):
        raise SystemExit("--color must be a 6-digit hex like 00ADD8")
    url = SIMPLE_ICONS.format(slug=slug, color=color)
    out: Path = args.output
    if out.suffix.lower() not in {".svg", ".png"} or not 1 <= args.size <= 8192:
        raise ValueError("logo needs svg or png output and size between 1 and 8192")
    data, _, final_url = download(
        url, headers={"User-Agent": USER_AGENT}, max_bytes=2 * 1024 * 1024
    )
    from xml.etree import ElementTree

    root = ElementTree.fromstring(data)
    if root.tag.split("}")[-1] != "svg":
        raise ValueError("logo response is not an SVG")
    out.parent.mkdir(parents=True, exist_ok=True)
    svg_path = out.with_suffix(".svg")
    svg_path.write_bytes(data)
    tool = "svg"
    if out.suffix.lower() == ".png":
        tool = rasterize_svg(svg_path, out, args.size)
        with Image.open(out) as image:
            image = image.convert("RGBA")
            bbox = image.getchannel("A").getbbox()
            if bbox:
                image = image.crop(bbox)
            image.save(out)
            size = image.size
    else:
        size = (args.size, args.size)
    write_sidecar(
        out,
        {
            "kind": "brand_logo",
            "source_url": url,
            "final_url": final_url,
            "slug": slug,
            "fetched_at": utc_now(),
            "tool": tool,
            "pixels": {"width": size[0], "height": size[1]}
            if out.suffix.lower() == ".png"
            else None,
            "rights": "unverified source and trademark terms must be reviewed for the intended use",
        },
    )
    print(f"logo → {out} ({size[0]}x{size[1]}, {tool})")


# ---------------------------------------------------------------- emoji
# render emoji text with a color emoji font trying each known strike size and cropping to painted pixels
def render_emoji(text: str, size: int, font_path: str | None = None):
    """Render text using the available emoji font and shaping support to an RGBA image of height ``size``."""
    from PIL import Image, ImageDraw, ImageFont

    if not 1 <= size <= 8192:
        raise ValueError("emoji size must be between 1 and 8192")
    if len(text) > 128:
        raise ValueError("emoji text must be at most 128 code points")
    candidates = [(font_path, None)] if font_path else EMOJI_FONTS
    last_error: Exception | None = None
    glyphs = max(1, len(text))
    # bitmap emoji fonts only open at exact strike sizes so try each until one draws
    for path, strikes in candidates:
        if not path or not Path(path).exists():
            continue
        for try_size in strikes or (160, 137, 109, 128, 96, 64, 48, 32):
            try:
                font = ImageFont.truetype(path, try_size)
                # Draw on a generous transparent canvas and crop to the painted pixels;
                # metrics from getbbox are unreliable for flag/ZWJ sequences.
                canvas = Image.new(
                    "RGBA", (try_size * (glyphs + 2) * 2, try_size * 3), (0, 0, 0, 0)
                )
                ImageDraw.Draw(canvas).text(
                    (try_size, try_size // 2), text, font=font, embedded_color=True
                )
                box = canvas.getchannel("A").getbbox()
                if not box:
                    raise ValueError("emoji rendered no pixels")
                image = canvas.crop(box)
                scale = size / image.height
                target_width = max(1, int(image.width * scale))
                if target_width * size > 80_000_000:
                    raise ValueError("emoji exceeds 80 million pixels")
                image = image.resize((target_width, size), Image.LANCZOS)
                return image, path
            except Exception as exc:  # pragma: no cover - font specific
                last_error = exc
                continue
    raise SystemExit(
        f"no color emoji font usable (tried {[p for p, _ in candidates]}): {last_error}"
    )


# handle the emoji subcommand saving the rendered png and its sidecar
@staged_asset
def fetch_emoji(args: argparse.Namespace) -> None:
    if args.output.suffix.lower() != ".png":
        raise ValueError("emoji output must be png")
    image, path = render_emoji(args.text, args.size, args.font)
    out: Path = args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    write_sidecar(
        out,
        {
            "kind": "emoji",
            "text": args.text,
            "font": path,
            "created_at": utc_now(),
            "pixels": {"width": image.width, "height": image.height},
            "rights": "unverified review the selected font and glyph terms for the intended use",
        },
    )
    print(f"emoji → {out} ({image.width}x{image.height})")


# build the command line parser with the image logo and emoji subcommands
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    img = sub.add_parser("image")
    img.add_argument("url")
    img.add_argument("-o", "--output", type=Path, required=True)
    img.add_argument("--max-width", type=int)
    img.add_argument("--rights", help="license or basis, recorded in the sidecar")
    img.add_argument("--credit")
    img.set_defaults(func=fetch_image)
    logo = sub.add_parser("logo")
    logo.add_argument("slug")
    logo.add_argument(
        "-o", "--output", type=Path, required=True, help=".png (rasterized) or .svg"
    )
    logo.add_argument("--color", default="ffffff")
    logo.add_argument("--size", type=int, default=512)
    logo.set_defaults(func=fetch_logo)
    emo = sub.add_parser("emoji")
    emo.add_argument("text")
    emo.add_argument("-o", "--output", type=Path, required=True)
    emo.add_argument("--size", type=int, default=256, help="output height in px")
    emo.add_argument("--font", help="color emoji font path override")
    emo.set_defaults(func=fetch_emoji)
    return parser


# command line entry that dispatches to the chosen subcommand
def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (ValueError, FileExistsError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
