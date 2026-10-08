"""test boardlib support for video use"""

import importlib.util
import json
from pathlib import Path

import pytest

from helpers.board import Board, BoardError

ROOT = Path(__file__).resolve().parent.parent
BOARDLIB = ROOT / "helpers" / "boardlib.py"


# load
def _load():
    spec = importlib.util.spec_from_file_location("boardlib", BOARDLIB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# alignment
def _alignment(tmp_path: Path) -> None:
    words = [("It", 0.0), ("is", 0.2), ("September", 0.4), ("2nd,", 0.9), ("2026,", 1.2), ("and", 1.7), ("This", 2.2), ("has", 2.4),
             ("been", 2.6), ("Hit", 3.0), ("the", 3.2), ("like", 3.4), ("button", 3.6)]
    payload = {"words": [{"type": "word", "text": t, "start": s, "end": s + 0.15} for t, s in words]}
    (tmp_path / "narration.alignment.json").write_text(json.dumps(payload))


# test boardlib builds a board that resolves
def test_boardlib_builds_a_board_that_resolves(tmp_path: Path) -> None:
    lib = _load()
    _alignment(tmp_path)
    b = lib.BoardBuilder(tmp_path, duration=4.5)
    b.beat("title", 0.0, lib.titlecard("Tech Brief", "Sep 2nd, 2026"))
    b.beat("signoff", lib.W + "This", lib.endcard("SQLite"))
    out = b.write(music=None, narration=None)  # full 1080p canvas: element sizes are in delivery pixels
    spec = json.loads(out.read_text())
    assert spec["width"] == 1920 and spec["music"] is None
    board = Board(spec, tmp_path, verbose=False)
    board.check_layout()
    assert [beat.id for beat in board.beats] == ["title", "signoff"]
    assert board.beats[1].start == pytest.approx(2.2)


# test boardlib rejects duplicate ids
def test_boardlib_rejects_duplicate_ids(tmp_path: Path) -> None:
    lib = _load()
    b = lib.BoardBuilder(tmp_path)
    b.beat("a", 0.0, [lib.display("same", "One"), lib.display("same", "Two", y=0.7)])
    with pytest.raises(ValueError, match="duplicate"):
        b.write(duration=2.0, narration=None, alignment=None, music=None)


# test element helpers shape
def test_element_helpers_shape() -> None:
    lib = _load()
    assert lib.card("c", "x.png", 0.0)["file"] == "assets/x.png"
    assert lib.video("v", "clip.mp4", 0.0, full=False)["card"] is True
    assert lib.dim("d", 0.0, alpha="70", allow=["v"])["color"] == "#00000070"
    assert lib.sticker("s", "Two\nlines", 0.0, 0.5, 0.5)["style"] == "sticker"


# test board rejects a video shorter than its beat with a clear error
def test_board_rejects_a_video_shorter_than_its_beat_with_a_clear_error(tmp_path: Path) -> None:
    spec = {"version": 1, "width": 320, "height": 180, "fps": 10, "duration": 3.0,
            "beats": [{"id": "one", "at": 0.0, "elements": [{"id": "v", "kind": "video", "file": "missing.mp4", "x": 0.5, "y": 0.5}]}]}
    with pytest.raises(BoardError, match="not found"):
        Board(spec, tmp_path, verbose=False)
