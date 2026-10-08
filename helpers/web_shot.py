#!/usr/bin/env python3
"""Capture web pages as evidence screenshots and turn them into overlay cards.

Two subcommands:

  capture   Render a public URL with a headless browser at 2x device scale.
            Uses Playwright when the Python package is installed (supports
            ``--selector`` element clips); otherwise falls back to a local
            Chrome/Chromium binary. Writes ``<out>.json`` provenance next to
            the PNG (url, title when known, viewport, capture time, tool).

  card      Turn any PNG/JPEG (a capture, a downloaded image, a chart) into a
            composited "card": optional crop, auto-trim, max size, rounded
            corners, thin border, drop shadow, and rotation. The result is an
            RGBA PNG ready for ``board.py`` or any overlay pipeline.

Usage:
    python helpers/web_shot.py capture https://github.com/org/repo -o edit/assets/repo.png
    python helpers/web_shot.py capture URL -o out.png --width 1280 --height 900 --dark --wait 2.5
    python helpers/web_shot.py capture URL -o out.png --selector "article h1" --full-page
    python helpers/web_shot.py card edit/assets/repo.png -o edit/assets/repo_card.png \
        --crop 0,0,1280,520 --radius 28 --shadow --rotate -4 --max-width 1400

Crops are ``x,y,w,h`` in pixels of the input image, or fractions (0-1) of its
size when every value is <= 1. Rotation is degrees, positive = counter-clockwise.
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from ._asset_io import staged_asset, file_hash
except ImportError:
    from _asset_io import staged_asset, file_hash


CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Arc.app/Contents/MacOS/Arc",
]
CHROME_NAMES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
    "msedge",
    "brave-browser",
]


# current utc time as an iso string
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# accept only http https or file urls and reject anything else
def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "file"} or (
        parsed.scheme != "file" and not parsed.netloc
    ):
        raise ValueError(f"capture needs an http(s) or file URL, got: {url}")
    return url


# locate a chrome like browser binary from the environment known app paths or the path
def find_chrome() -> str | None:
    env = os.environ.get("VIDEO_USE_CHROME")
    if env and Path(env).is_file() and os.access(env, os.X_OK):
        return env
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


# report whether the playwright python package can be imported
def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


# ---------------------------------------------------------------- capture
# screenshot a url with playwright chromium optionally clipping to a selector or the full page
def capture_playwright(
    url: str,
    out: Path,
    *,
    width: int,
    height: int,
    scale: int,
    dark: bool,
    wait: float,
    full_page: bool,
    selector: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=scale,
            color_scheme="dark" if dark else "light",
            user_agent=user_agent,
        )
        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=60_000)
        if wait:
            page.wait_for_timeout(int(wait * 1000))
        title = page.title()
        final_url = page.url
        if selector:
            element = page.locator(selector).first
            box = element.bounding_box()
            if box is None or box["width"] * box["height"] * scale ** 2 > 80_000_000:
                raise ValueError("element capture exceeds the pixel budget or is not visible")
            element.screenshot(path=str(out))
        else:
            if full_page:
                dimensions = page.evaluate("[Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0), Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0)]")
                if dimensions[0] * dimensions[1] * scale ** 2 > 80_000_000:
                    raise ValueError("full page capture exceeds 80 million pixels")
            page.screenshot(path=str(out), full_page=full_page)
        browser.close()
    return {"tool": "playwright", "title": title, "final_url": final_url}


# run headless chrome screenshot mode and stop it once the png has stopped growing or the deadline passes
def run_chrome_screenshot(
    chrome: str, url: str, out: Path, extra_args: list[str], *, timeout: float = 90.0
) -> None:
    """Run headless Chrome's --screenshot with a watchdog.

    Chrome occasionally writes the PNG and then never exits (notably with a
    custom ``--user-data-dir``). Poll the output and terminate the process once
    the file has stopped growing, then verify that it is a complete image.
    """
    import time

    if out.exists() or out.is_symlink():
        raise FileExistsError("screenshot output must be new")
    out = out.absolute()
    command = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        *extra_args,
        f"--screenshot={out}",
        url,
    ]
    proc = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True
    )
    deadline = time.time() + timeout
    stable_since: float | None = None
    last_size = -1
    # poll the output file and treat a size that stays stable for over a second and a half as finished
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        if out.exists():
            size = out.stat().st_size
            if size > 0 and size == last_size:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since > 1.5:
                    proc.terminate()
                    break
            else:
                stable_since = None
            last_size = size
        time.sleep(0.2)
    else:
        proc.kill()
    try:
        _, stderr = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        stderr = ""
    if not out.exists() or out.stat().st_size == 0:
        raise SystemExit(
            f"browser produced no screenshot for {url}\n{(stderr or '')[-800:]}"
        )
    from PIL import Image

    with Image.open(out) as image:
        image.verify()


# screenshot a viewport with chrome and reject unsupported capture modes
def capture_chrome(
    url: str,
    out: Path,
    *,
    width: int,
    height: int,
    scale: int,
    dark: bool,
    wait: float,
    full_page: bool,
    selector: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    chrome = find_chrome()
    if not chrome:
        raise SystemExit(
            "no headless browser found: `pip install playwright && playwright install chromium`, "
            "install Google Chrome/Chromium, or set VIDEO_USE_CHROME to a browser binary"
        )
    if selector:
        raise SystemExit(
            "--selector requires Playwright (`pip install playwright && playwright install chromium`)"
        )
    if full_page:
        raise SystemExit("--full-page requires Playwright for exact page height")
    extra = [
        f"--force-device-scale-factor={scale}",
        f"--window-size={width},{height}",
        f"--virtual-time-budget={int(max(wait, 1.0) * 1000)}",
    ]
    if dark:
        extra.append("--force-dark-mode")
    if user_agent:
        extra.append(f"--user-agent={user_agent}")
    if url.startswith("file:") or url.endswith(".svg"):
        extra.append("--default-background-color=00000000")
    with tempfile.TemporaryDirectory(prefix="video-use-chrome-") as profile:
        run_chrome_screenshot(
            chrome,
            url,
            out,
            [f"--user-data-dir={profile}", *extra],
            timeout=max(30.0, wait + 60.0),
        )
    return {"tool": "chrome", "browser": chrome, "title": None}


# handle the capture subcommand choosing the backend and writing the provenance sidecar
@staged_asset
def capture(args: argparse.Namespace) -> None:
    url = validate_url(args.url)
    if args.output.suffix.lower() != ".png":
        raise ValueError("capture output must be png")
    if not (
        1 <= args.width <= 16384 and 1 <= args.height <= 16384 and 1 <= args.scale <= 4
    ):
        raise ValueError("viewport dimensions must be 1 to 16384 and scale 1 to 4")
    if args.width * args.height * args.scale**2 > 80_000_000:
        raise ValueError("capture viewport exceeds 80 million pixels")
    if not math.isfinite(args.wait) or not 0 <= args.wait <= 60:
        raise ValueError("wait must be between 0 and 60 seconds")
    out: Path = args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    common = dict(
        width=args.width,
        height=args.height,
        scale=args.scale,
        dark=args.dark,
        wait=args.wait,
        full_page=args.full_page,
        selector=args.selector,
        user_agent=args.user_agent,
    )
    if playwright_available() and not args.chrome:
        info = capture_playwright(url, out, **common)
    else:
        info = capture_chrome(url, out, **common)
    from PIL import Image

    with Image.open(out) as image:
        size = image.size
    provenance = {
        "kind": "web_capture",
        "source_url": url,
        "captured_at": utc_now(),
        "viewport": {"width": args.width, "height": args.height, "scale": args.scale},
        "dark": args.dark,
        "selector": args.selector,
        "full_page": args.full_page,
        "pixels": {"width": size[0], "height": size[1]},
        "rights": "unverified review page content and reuse terms for the intended use",
        **info,
    }
    out.with_suffix(out.suffix + ".json").write_text(
        json.dumps(provenance, indent=1) + "\n", encoding="utf-8"
    )
    print(f"captured {url} → {out} ({size[0]}x{size[1]}, {info['tool']})")


# ---------------------------------------------------------------- card
# parse a crop spec of x y w h in pixels or fractions and validate it against the image size
def parse_crop(
    spec: str | None, width: int, height: int
) -> tuple[int, int, int, int] | None:
    if not spec:
        return None
    parts = [float(p) for p in spec.split(",")]
    if len(parts) != 4 or not all(math.isfinite(p) for p in parts):
        raise ValueError("--crop needs x,y,w,h")
    if all(0 <= p <= 1 for p in parts):
        parts = [
            parts[0] * width,
            parts[1] * height,
            parts[2] * width,
            parts[3] * height,
        ]
    x, y, w, h = (int(round(p)) for p in parts)
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
        raise ValueError(f"crop {spec} falls outside the {width}x{height} image")
    return x, y, w, h


# build an rgba card from an image applying crop trim resize rounding border shadow and rotation in order
def make_card(
    source: Path,
    *,
    crop: str | None = None,
    trim: bool = False,
    max_width: int | None = None,
    max_height: int | None = None,
    radius: int = 0,
    border: int = 0,
    border_color: tuple[int, int, int, int] = (255, 255, 255, 90),
    shadow: bool = False,
    shadow_blur: int = 28,
    shadow_offset: tuple[int, int] = (0, 18),
    shadow_opacity: int = 150,
    rotate: float = 0.0,
):
    """Return an RGBA PIL image: cropped, trimmed, sized, rounded, bordered, shadowed, rotated."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    if any(value is not None and value <= 0 for value in (max_width, max_height)):
        raise ValueError("maximum dimensions must be positive")
    if (
        radius < 0
        or border < 0
        or not 0 <= shadow_blur <= 1024
        or not 0 <= shadow_opacity <= 255
    ):
        raise ValueError("invalid radius border or shadow settings")
    if not math.isfinite(rotate):
        raise ValueError("rotation must be finite")
    with Image.open(source) as opened:
        image = opened.convert("RGBA")
    box = parse_crop(crop, image.width, image.height)
    if box:
        x, y, w, h = box
        image = image.crop((x, y, x + w, y + h))
    # trim by diffing against the top left pixel color and keeping a small pad around the content
    if trim:
        background = Image.new("RGBA", image.size, image.getpixel((0, 0)))
        difference = ImageChops.difference(image, background)
        bands = difference.split()
        combined = bands[0]
        for band in bands[1:]:
            combined = ImageChops.lighter(combined, band)
        bbox = combined.getbbox()
        if bbox:
            pad = 8
            image = image.crop(
                (
                    max(0, bbox[0] - pad),
                    max(0, bbox[1] - pad),
                    min(image.width, bbox[2] + pad),
                    min(image.height, bbox[3] + pad),
                )
            )
    if max_width or max_height:
        scale = min(
            (max_width / image.width) if max_width else 1.0,
            (max_height / image.height) if max_height else 1.0,
            1.0,
        )
        if scale < 1.0:
            image = image.resize(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.LANCZOS,
            )
    if radius > 0:
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, image.width - 1, image.height - 1), radius=radius, fill=255
        )
        alpha = ImageChops.multiply(image.getchannel("A"), mask)
        image.putalpha(alpha)
    if border > 0:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rounded_rectangle(
            (
                border // 2,
                border // 2,
                image.width - 1 - border // 2,
                image.height - 1 - border // 2,
            ),
            radius=radius,
            outline=border_color,
            width=border,
        )
        image = Image.alpha_composite(image, overlay)
    # shadow pads the canvas then blurs an offset copy of the alpha and composites the image on top
    if shadow:
        pad = shadow_blur * 2 + max(abs(shadow_offset[0]), abs(shadow_offset[1]))
        canvas = Image.new(
            "RGBA", (image.width + pad * 2, image.height + pad * 2), (0, 0, 0, 0)
        )
        shade = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        shade_alpha = Image.new("L", canvas.size, 0)
        shade_alpha.paste(
            image.getchannel("A").point(lambda a: a * shadow_opacity // 255),
            (pad + shadow_offset[0], pad + shadow_offset[1]),
        )
        shade_alpha = shade_alpha.filter(ImageFilter.GaussianBlur(shadow_blur))
        shade.putalpha(shade_alpha)
        canvas = Image.alpha_composite(canvas, shade)
        canvas.alpha_composite(image, (pad, pad))
        image = canvas
    if rotate:
        image = image.rotate(rotate, resample=Image.BICUBIC, expand=True)
    return image


# handle the card subcommand saving the image and a sidecar that links back to the source provenance
@staged_asset
def card(args: argparse.Namespace) -> None:
    if args.output.suffix.lower() != ".png":
        raise ValueError("card output must be png")
    image = make_card(
        args.source,
        crop=args.crop,
        trim=args.trim,
        max_width=args.max_width,
        max_height=args.max_height,
        radius=args.radius,
        border=args.border,
        shadow=args.shadow,
        shadow_blur=args.shadow_blur,
        rotate=args.rotate,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)
    sidecar = args.source.with_suffix(args.source.suffix + ".json")
    provenance: dict[str, Any] = {
        "kind": "card",
        "derived_from": str(args.source),
        "source_sha256": file_hash(args.source),
        "created_at": utc_now(),
        "treatment": {
            "crop": args.crop,
            "trim": args.trim,
            "radius": args.radius,
            "border": args.border,
            "shadow": args.shadow,
            "shadow_blur": args.shadow_blur,
            "max_width": args.max_width,
            "max_height": args.max_height,
            "rotate": args.rotate,
        },
        "pixels": {"width": image.width, "height": image.height},
    }
    if sidecar.exists():
        try:
            provenance["source"] = json.loads(sidecar.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError("source provenance is invalid JSON") from exc
    args.output.with_suffix(args.output.suffix + ".json").write_text(
        json.dumps(provenance, indent=1) + "\n", encoding="utf-8"
    )
    print(f"card → {args.output} ({image.width}x{image.height})")


# build the command line parser with the capture and card subcommands
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="screenshot a URL")
    cap.add_argument("url")
    cap.add_argument("-o", "--output", type=Path, required=True)
    cap.add_argument("--width", type=int, default=1280)
    cap.add_argument("--height", type=int, default=800)
    cap.add_argument(
        "--scale", type=int, default=2, help="device scale factor (2 = retina)"
    )
    cap.add_argument(
        "--dark", action="store_true", help="prefer the page's dark color scheme"
    )
    cap.add_argument(
        "--wait", type=float, default=2.0, help="seconds to let the page settle"
    )
    cap.add_argument("--full-page", action="store_true")
    cap.add_argument("--selector", help="CSS selector to clip to (Playwright only)")
    cap.add_argument("--user-agent")
    cap.add_argument(
        "--chrome",
        action="store_true",
        help="force the Chrome CLI path even if Playwright exists",
    )
    cap.set_defaults(func=capture)

    crd = sub.add_parser(
        "card", help="crop/round/shadow/rotate an image into an overlay card"
    )
    crd.add_argument("source", type=Path)
    crd.add_argument("-o", "--output", type=Path, required=True)
    crd.add_argument("--crop", help="x,y,w,h in pixels or fractions")
    crd.add_argument("--trim", action="store_true", help="auto-trim uniform borders")
    crd.add_argument("--max-width", type=int)
    crd.add_argument("--max-height", type=int)
    crd.add_argument("--radius", type=int, default=0)
    crd.add_argument(
        "--border", type=int, default=0, help="border width in px (subtle light line)"
    )
    crd.add_argument("--shadow", action="store_true")
    crd.add_argument("--shadow-blur", type=int, default=28)
    crd.add_argument("--rotate", type=float, default=0.0)
    crd.set_defaults(func=card)
    return parser


# command line entry that dispatches to the chosen subcommand and reports value errors cleanly
def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (ValueError, FileExistsError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
