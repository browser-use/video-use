"""Measured text cards preserve frame timing transparency and explicit font layout."""

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

import cards


# use a bundled licensed font so layout expectations do not depend on system fonts
@pytest.fixture
def manifest():
    return {
        "fps": 24,
        "total_frames": 12,
        "picture": [0, 0, 320, 180],
        "font_layout": "basic",
        "fonts": {"body": "@assets/fonts/AlfaSlabOne.ttf"},
        "cards": [
            {
                "id": "title",
                "start_frame": 2,
                "end_frame": 8,
                "lines": [
                    {"text": "TEST", "font": "body", "x": 80, "y": 50, "cap_height": 32}
                ],
            }
        ],
    }


# card visibility uses the declared half open interval and measured nontransparent ink
def test_card_visibility_and_bounds(manifest, tmp_path):
    renderer = cards.CardRenderer(manifest, tmp_path)
    assert renderer.frame(1).getbbox() is None
    image, boxes = renderer.frame(2, measure=True)
    assert image.getbbox() is not None
    assert image.getpixel((0, 0))[3] == 0
    assert boxes[0]["box"][0:2] == [80, 50]
    assert boxes[0]["box"][3] == pytest.approx(32, abs=1)
    assert boxes[0]["visible_support_xyxy"] == list(image.getbbox())
    assert renderer.frame(7).getbbox() is not None
    assert renderer.frame(8).getbbox() is None


# a supplied subject mask removes card pixels while keeping pre-mask measurements
def test_mask_occludes_caption_support(manifest, tmp_path):
    mask = Image.new("L", (320, 180))
    ImageDraw.Draw(mask).rectangle((0, 0, 105, 179), fill=255)
    mask.save(tmp_path / "subject.png")
    card = manifest["cards"][0]
    card.update(
        {
            "outline": {"radius": 1},
            "shadow": {"radius": 1.5, "opacity": 0.8},
            "occlusion_mask": {"file": "subject.png"},
        }
    )
    image, boxes = cards.CardRenderer(manifest, tmp_path).frame(3, measure=True)
    alpha = np.asarray(image.getchannel("A"))
    assert alpha[:, :106].max() == 0 and alpha[:, 106:].max() > 0
    assert boxes[0]["card_support_xyxy"][0] < 80
    assert boxes[0]["visible_support_xyxy"][0] >= 106


# entry scale changes settle into the measured final card dimensions
def test_entry_animation_settles(manifest, tmp_path):
    manifest["cards"][0]["animation"] = {"entry_frames": 3, "scale_from": 0.5}
    renderer = cards.CardRenderer(manifest, tmp_path)
    _, entering = renderer.frame(2, measure=True)
    _, settled = renderer.frame(5, measure=True)
    assert entering[0]["box"][2] < settled[0]["box"][2]
    assert renderer.frame(5).tobytes() == renderer.frame(7).tobytes()


# text that would clip is rejected rather than silently cropped
def test_clipped_text_fails(manifest, tmp_path):
    manifest["cards"][0]["lines"][0]["x"] = 300
    with pytest.raises(ValueError, match="clips"):
        cards.CardRenderer(manifest, tmp_path).frame(2)


# malformed frame clocks cannot produce misleading movie timing
@pytest.mark.parametrize(
    "change", [{"fps": 0}, {"total_frames": 0}, {"picture": [0, 0, -1, 180]}]
)
def test_invalid_clock_or_canvas(manifest, tmp_path, change):
    manifest.update(change)
    with pytest.raises(ValueError):
        cards.CardRenderer(manifest, tmp_path)


# explicitly requested complex shaping fails when the engine is unavailable
def test_raqm_is_never_silently_replaced(manifest, tmp_path, monkeypatch):
    cards.font_at.cache_clear()
    monkeypatch.setattr(cards.features, "check_feature", lambda name: False)
    manifest["font_layout"] = "raqm"
    with pytest.raises(RuntimeError, match="RAQM"):
        cards.CardRenderer(manifest, tmp_path)


# exported alpha movies use the declared frame rate and preserve active-frame pixels
def test_encoded_movie_preserves_rate_frames_and_alpha(manifest, tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg required")
    output = tmp_path / "cards.mov"
    cards.CardRenderer(manifest, tmp_path).write_movie(output)
    info = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
            capture_output=True,
            check=True,
        ).stdout
    )["streams"][0]
    assert info["r_frame_rate"] == "24/1" and int(info["nb_frames"]) == 12
    assert float(info["duration"]) == pytest.approx(0.5)
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(output),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(12, 180, 320, 4)
    assert frames[0, :, :, 3].max() == 0
    assert frames[2, :, :, 3].max() > 0
    assert frames[8, :, :, 3].max() == 0


# existing output and log files must remain intact on an attempted export
@pytest.mark.parametrize("name", ["cards.mov", "cards.log"])
def test_movie_preserves_existing_files(manifest, tmp_path, name):
    existing = tmp_path / name
    existing.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        cards.CardRenderer(manifest, tmp_path).write_movie(tmp_path / "cards.mov")
    assert existing.read_bytes() == b"keep"


# a CLI sidecar must not replace the manifest that supplied the card layout
def test_cli_preserves_existing_sidecar(manifest, tmp_path):
    source = tmp_path / "card.png.json"
    source.write_text(json.dumps(manifest))
    before = source.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "helpers/cards.py"),
            str(source),
            "--frame",
            "2",
            "--out",
            str(tmp_path / "card.png"),
        ],
        capture_output=True,
    )
    assert result.returncode != 0 and b"new caption output" in result.stderr
    assert source.read_bytes() == before


# older project font aliases render the same pixels after shared assets move
def test_legacy_font_alias_matches_shared_assets(manifest, tmp_path):
    legacy = copy.deepcopy(manifest)
    legacy["fonts"]["body"] = "@skill/assets/fonts/AlfaSlabOne.ttf"
    current = cards.CardRenderer(manifest, tmp_path).frame(6)
    previous = cards.CardRenderer(legacy, tmp_path).frame(6)
    assert np.array_equal(np.array(current), np.array(previous))


# malformed animations fail before an encoder can create an output
@pytest.mark.parametrize('animation', [{'entry_frames':-1}, {'entry_frames':0.5}, {'power':float('nan')}, {'scale_from':float('inf')}, {'blur_from':float('nan')}])
def test_review_invalid_animation(manifest, tmp_path, animation):
    manifest['cards'][0]['animation'] = animation
    with pytest.raises(ValueError):
        cards.CardRenderer(manifest, tmp_path)


# failed encodes leave no final output and preserve a concurrent writer
@pytest.mark.parametrize('race', [False, True])
def test_review_staged_movie_failure(manifest, tmp_path, monkeypatch, race):
    renderer = cards.CardRenderer(manifest, tmp_path)
    output = tmp_path / 'cards.mov'
    # simulate a partial encoder result or a concurrent output
    def encode(path):
        path.write_bytes(b'partial')
        if race:
            output.write_bytes(b'other writer')
            path.with_suffix('.log').write_text('log')
        else:
            raise RuntimeError('encoder failed')
    monkeypatch.setattr(renderer, '_write_movie', encode)
    with pytest.raises(FileExistsError if race else RuntimeError):
        renderer.write_movie(output)
    if race:
        assert output.read_bytes() == b'other writer'
    else:
        assert not output.exists()
    assert not list(tmp_path.glob('.cards-*'))
