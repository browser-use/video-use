"""tests for the fetch asset helper covering emoji rendering and input validation before any network call
emoji tests are skipped when no color emoji font is installed
"""

import argparse
from pathlib import Path

import pytest

from helpers import fetch_asset


# report whether any known color emoji font exists on this machine
def _has_emoji_font() -> bool:
    return any(Path(path).exists() for path, _ in fetch_asset.EMOJI_FONTS)


# emoji render to an rgba image of the requested height
@pytest.mark.skipif(not _has_emoji_font(), reason="no color emoji font on this machine")
def test_render_emoji_returns_transparent_png_of_requested_height() -> None:
    image, font = fetch_asset.render_emoji("👉", 120)
    assert image.mode == "RGBA"
    assert image.height == 120
    assert image.getchannel("A").getbbox() is not None
    assert Path(font).exists()


# empty content fails clearly regardless of platform emoji sequence support
@pytest.mark.skipif(not _has_emoji_font(), reason="no color emoji font on this machine")
def test_render_emoji_fails_clearly_when_no_pixels_are_drawn() -> None:
    with pytest.raises(SystemExit, match="no pixels|no color emoji"):
        fetch_asset.render_emoji("", 120)


# bad slugs and colors are rejected before any request
def test_logo_rejects_bad_slug_before_network(tmp_path: Path) -> None:
    args = argparse.Namespace(slug="not a slug!", output=tmp_path / "x.png", color="ffffff", size=100)
    with pytest.raises(SystemExit):
        fetch_asset.fetch_logo(args)
    args = argparse.Namespace(slug="go", output=tmp_path / "x.png", color="zzz", size=100)
    with pytest.raises(SystemExit):
        fetch_asset.fetch_logo(args)


# non http urls are rejected
def test_image_rejects_non_http(tmp_path: Path) -> None:
    args = argparse.Namespace(url="file:///etc/hosts", output=tmp_path / "x.png", max_width=None, rights=None, credit=None)
    with pytest.raises(SystemExit):
        fetch_asset.fetch_image(args)
