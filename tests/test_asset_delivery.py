"""Verify bounded acquisition provenance and preservation of existing assets."""

import io
import json

import pytest
from PIL import Image

from helpers import _asset_io, fetch_asset, web_shot


# make a synthetic image response without third party media
@pytest.fixture
def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "orange").save(buffer, format="PNG")
    return buffer.getvalue()


# generate image command arguments using the public parser
def image_args(tmp_path, *options):
    return fetch_asset.build_parser().parse_args(
        [
            "image",
            "https://example.com/source.png",
            "-o",
            str(tmp_path / "asset.jpg"),
            *options,
        ]
    )


# download decoding format conversion and provenance agree on the resulting artifact
def test_image_delivery(monkeypatch, tmp_path, png_bytes):
    monkeypatch.setattr(
        fetch_asset,
        "download",
        lambda *a, **kw: (png_bytes, "image/png", "https://example.com/final.png"),
    )
    args = image_args(
        tmp_path, "--max-width", "20", "--rights", "user supplied declaration"
    )
    fetch_asset.fetch_image(args)
    with Image.open(args.output) as image:
        assert image.format == "JPEG" and image.size == (20, 10)
    metadata = json.loads((tmp_path / "asset.jpg.json").read_text())
    assert metadata["sha256"] == _asset_io.file_hash(args.output)
    assert metadata["final_url"].endswith("/final.png")
    assert metadata["rights"] == "user supplied declaration"
    assert metadata["rights_verified"] is False


# any existing asset or sidecar is protected before network access
@pytest.mark.parametrize("name", ["asset.jpg", "asset.jpg.json"])
def test_image_preserves_existing(monkeypatch, tmp_path, name):
    path = tmp_path / name
    path.write_text("keep")
    monkeypatch.setattr(
        fetch_asset, "download", lambda *a, **kw: pytest.fail("unexpected network")
    )
    with pytest.raises(FileExistsError):
        fetch_asset.fetch_image(image_args(tmp_path))
    assert path.read_text() == "keep"


# invalid downloads leave neither final assets nor temporary parts
def test_invalid_image_does_not_publish(monkeypatch, tmp_path):
    monkeypatch.setattr(
        fetch_asset,
        "download",
        lambda *a, **kw: (b"not an image", "text/html", "https://example.com"),
    )
    with pytest.raises(SystemExit, match="not a decodable image"):
        fetch_asset.fetch_image(image_args(tmp_path))
    assert list(tmp_path.iterdir()) == []


# unsupported file formats and dimensions fail before acquisition
@pytest.mark.parametrize(
    "options", [["--max-width", "0"], ["--max-width", "-5"], ["-o", "bad.txt"]]
)
def test_invalid_image_options(monkeypatch, tmp_path, options):
    monkeypatch.setattr(
        fetch_asset, "download", lambda *a, **kw: pytest.fail("unexpected network")
    )
    args = image_args(tmp_path, *options)
    if args.output.name == "bad.txt":
        args.output = tmp_path / "bad.txt"
    with pytest.raises(ValueError):
        fetch_asset.fetch_image(args)


# simple icon responses retain their exact downloaded svg and never assert usage rights
def test_logo_svg_provenance(monkeypatch, tmp_path):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h24v24H0z"/></svg>'
    monkeypatch.setattr(
        fetch_asset,
        "download",
        lambda *a, **kw: (svg, "image/svg+xml", "https://example.com/logo.svg"),
    )
    args = fetch_asset.build_parser().parse_args(
        ["logo", "test", "-o", str(tmp_path / "logo.svg")]
    )
    fetch_asset.fetch_logo(args)
    assert args.output.read_bytes() == svg
    data = json.loads((tmp_path / "logo.svg.json").read_text())
    assert data["rights_verified"] is False
    assert data["sha256"] == _asset_io.file_hash(args.output)


# rasterizer failure cannot leave the paired svg partially published
def test_logo_rasterizer_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        fetch_asset,
        "download",
        lambda *a, **kw: (b"<svg/>", "image/svg+xml", "https://example.com"),
    )

    # fail after the svg has been staged
    def fail(*args):
        raise ValueError("rasterizer failed")

    monkeypatch.setattr(fetch_asset, "rasterize_svg", fail)
    args = fetch_asset.build_parser().parse_args(
        ["logo", "test", "-o", str(tmp_path / "logo.png")]
    )
    with pytest.raises(ValueError, match="rasterizer failed"):
        fetch_asset.fetch_logo(args)
    assert list(tmp_path.iterdir()) == []


# changing an output during acquisition cannot clobber the newly created file
def test_publication_race_preserves_new_destination(monkeypatch, tmp_path, png_bytes):
    args = image_args(tmp_path)

    # simulate another writer creating the destination while a request is in flight
    def download(*a, **kw):
        args.output.write_text("other writer")
        return png_bytes, "image/png", "https://example.com"

    monkeypatch.setattr(fetch_asset, "download", download)
    with pytest.raises(FileExistsError):
        fetch_asset.fetch_image(args)
    assert args.output.read_text() == "other writer"
    assert not (tmp_path / "asset.jpg.json").exists()


# cards retain source identity and all requested sizing treatments
def test_card_provenance(tmp_path, png_bytes):
    source = tmp_path / "source.png"
    source.write_bytes(png_bytes)
    args = web_shot.build_parser().parse_args(
        [
            "card",
            str(source),
            "-o",
            str(tmp_path / "card.png"),
            "--max-width",
            "20",
            "--radius",
            "3",
        ]
    )
    web_shot.card(args)
    data = json.loads((tmp_path / "card.png.json").read_text())
    assert data["source_sha256"] == _asset_io.file_hash(source)
    assert data["treatment"]["max_width"] == 20
    assert data["pixels"] == {"width": 20, "height": 10}
    with pytest.raises(FileExistsError):
        web_shot.card(args)


# corrupt source provenance fails rather than silently discarding evidence
def test_card_rejects_broken_provenance(tmp_path, png_bytes):
    source = tmp_path / "source.png"
    source.write_bytes(png_bytes)
    (tmp_path / "source.png.json").write_text("{bad")
    args = web_shot.build_parser().parse_args(
        ["card", str(source), "-o", str(tmp_path / "card.png")]
    )
    with pytest.raises(ValueError, match="provenance"):
        web_shot.card(args)
    assert not (tmp_path / "card.png").exists()


# unsupported browser options are not approximated by a tall viewport
@pytest.mark.parametrize("option", ["--selector", "--full-page"])
def test_chrome_rejects_unsupported_capture(monkeypatch, tmp_path, option):
    monkeypatch.setattr(web_shot, "find_chrome", lambda: "chrome")
    args = web_shot.build_parser().parse_args(
        [
            "capture",
            "https://example.com",
            "-o",
            str(tmp_path / "shot.png"),
            "--chrome",
            option,
            *(["h1"] if option == "--selector" else []),
        ]
    )
    with pytest.raises(SystemExit, match="requires Playwright"):
        web_shot.capture(args)
    assert not (tmp_path / "shot.png").exists()


# capture metadata records actual pixel geometry and content hash
def test_capture_metadata(monkeypatch, tmp_path, png_bytes):
    monkeypatch.setattr(web_shot, "playwright_available", lambda: True)

    # provide a deterministic browser result
    def capture(url, out, **options):
        out.write_bytes(png_bytes)
        return {"tool": "playwright", "title": "Fixture"}

    monkeypatch.setattr(web_shot, "capture_playwright", capture)
    args = web_shot.build_parser().parse_args(
        ["capture", "https://example.com", "-o", str(tmp_path / "shot.png")]
    )
    web_shot.capture(args)
    data = json.loads((tmp_path / "shot.png.json").read_text())
    assert data["pixels"] == {"width": 40, "height": 20}
    assert data["title"] == "Fixture" and not data["rights_verified"]


# streaming limits hold even when the server omits its content length
@pytest.mark.parametrize("declared", [None, "20"])
def test_download_byte_limit(monkeypatch, declared):
    import requests

    # emulate a response whose body exceeds the configured limit
    class Response:
        status_code = 200
        headers = {} if declared is None else {"Content-Length": declared}
        url = "https://example.com/final"

        # return the response through its context manager
        def __enter__(self):
            return self

        # close without swallowing download errors
        def __exit__(self, *args):
            return False

        # yield a body larger than the allowed size
        def iter_content(self, chunk_size):
            yield b"123456"
            yield b"789012"

    monkeypatch.setattr(requests, "get", lambda *a, **kw: Response())
    with pytest.raises(ValueError, match="byte limit"):
        _asset_io.download("https://example.com", headers={}, max_bytes=10)


# invalid image geometry cannot allocate a malformed card
@pytest.mark.parametrize(
    "options",
    [
        {"max_width": -1},
        {"shadow_blur": -1},
        {"rotate": float("nan")},
        {"crop": "nan,0,1,1"},
    ],
)
def test_card_invalid_geometry(tmp_path, png_bytes, options):
    source = tmp_path / "source.png"
    source.write_bytes(png_bytes)
    with pytest.raises(ValueError):
        web_shot.make_card(source, **options)


# transport errors follow the normal helper error path
def test_review_download_error(monkeypatch):
    import requests
    # simulate a timed out transport without making a network request
    def fail(*args, **kwargs):
        raise requests.Timeout('fixture timeout')
    monkeypatch.setattr(requests, 'get', fail)
    with pytest.raises(ValueError, match='asset download failed'):
        _asset_io.download('https://example.com', headers={}, max_bytes=10)
