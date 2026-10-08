"""tests for the web shot helper covering crop parsing card treatments and url validation
no browser is launched here
"""

from pathlib import Path

import pytest
from PIL import Image

from helpers import web_shot


# save a white image with a dark rectangle in the middle
def _source(tmp_path: Path, size=(400, 200)) -> Path:
    image = Image.new("RGB", size, (255, 255, 255))
    for x in range(40, 360):
        for y in range(40, 160):
            image.putpixel((x, y), (30, 30, 30))
    path = tmp_path / "shot.png"
    image.save(path)
    return path


# crops parse as pixels or fractions and invalid ones are rejected
def test_parse_crop_accepts_pixels_and_fractions() -> None:
    assert web_shot.parse_crop("10,20,100,50", 400, 200) == (10, 20, 100, 50)
    assert web_shot.parse_crop("0,0,0.5,0.5", 400, 200) == (0, 0, 200, 100)
    assert web_shot.parse_crop(None, 400, 200) is None
    with pytest.raises(ValueError):
        web_shot.parse_crop("300,0,200,50", 400, 200)
    with pytest.raises(ValueError):
        web_shot.parse_crop("1,2,3", 400, 200)


# cards crop round corners add shadow and rotate
def test_make_card_crops_rounds_and_shadows(tmp_path: Path) -> None:
    source = _source(tmp_path)
    plain = web_shot.make_card(source, crop="0,0,200,100")
    assert plain.size == (200, 100)
    rounded = web_shot.make_card(source, crop="0,0,200,100", radius=30)
    assert rounded.getpixel((0, 0))[3] == 0  # corner is transparent
    assert rounded.getpixel((100, 50))[3] == 255
    shadowed = web_shot.make_card(source, crop="0,0,200,100", shadow=True)
    assert shadowed.width > 200 and shadowed.height > 100
    rotated = web_shot.make_card(source, crop="0,0,200,100", rotate=10)
    assert rotated.width > 200


# trim removes uniform borders and max width scales down
def test_make_card_trims_uniform_borders_and_limits_size(tmp_path: Path) -> None:
    source = _source(tmp_path)
    trimmed = web_shot.make_card(source, trim=True)
    assert trimmed.width < 400 and trimmed.height < 200
    small = web_shot.make_card(source, max_width=100)
    assert small.width == 100 and small.height == 50


# only http https and file urls are accepted
def test_validate_url_rejects_non_http() -> None:
    with pytest.raises(ValueError):
        web_shot.validate_url("ftp://example.com/x")
    assert web_shot.validate_url("https://example.com/") == "https://example.com/"


# alpha differences keep black artwork visible to transparent border trimming
def test_review_transparent_black_trim(tmp_path):
    from PIL import ImageDraw
    image = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((40, 40, 59, 59), fill=(0, 0, 0, 255))
    path = tmp_path / 'source.png'; image.save(path)
    assert web_shot.make_card(path, trim=True).size == (36, 36)
    assert web_shot.validate_url('file:///tmp/page.html') == 'file:///tmp/page.html'
