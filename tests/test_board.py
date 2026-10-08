"""tests for the board renderer covering timing resolution layout qc word anchors sfx and edl output
rendering tests are skipped when ffmpeg is not installed
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from helpers import board as board_module
from helpers.board import Board, BoardError, find_word_time, synth_sfx, write_edl
from helpers.edl import validate_edl

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")


# build a tiny two beat spec with optional overrides
def _spec(**overrides) -> dict:
    spec = {
        "version": 1, "width": 320, "height": 180, "fps": 10, "duration": 2.0, "sfx": "off",
        "beats": [
            {"id": "one", "at": 0.0, "elements": [
                {"id": "title", "kind": "text", "text": "Hello", "style": "display", "size": 40, "x": 0.5, "y": 0.3, "enter": "pop"},
                {"id": "bar", "kind": "rect", "w": 0.4, "h": 0.1, "x": 0.5, "y": 0.8, "color": "accent", "enter": "fade"},
            ]},
            {"id": "two", "at": 1.0, "elements": [
                {"id": "big", "kind": "text", "text": "It's bad", "style": "display", "size": 40, "color": "accent", "enter": "whip_left"},
            ]},
        ],
    }
    spec.update(overrides)
    return spec


# beats chain their ends and layout frames and timeline rects are produced
def test_board_resolves_times_and_layout(tmp_path: Path) -> None:
    board = Board(_spec(), tmp_path, verbose=False)
    assert [b.id for b in board.beats] == ["one", "two"]
    assert board.beats[0].end == pytest.approx(1.0)
    assert board.beats[1].end == pytest.approx(2.0)
    manifest = board.check_layout()
    assert manifest["canvas"] == {"width": 320, "height": 180}
    assert manifest["frames"]
    frame = board.render_frame(0.5)
    assert frame.size == (320, 180)
    timeline = board.timeline()
    assert timeline["beats"][0]["elements"][0]["rect"]["width"] > 0


# overlapping elements fail qc unless the overlap is allowed
def test_layout_qc_rejects_unintended_overlap(tmp_path: Path) -> None:
    spec = _spec()
    spec["beats"][0]["elements"][1]["y"] = 0.3  # bar now sits on the title
    board = Board(spec, tmp_path, verbose=False)
    with pytest.raises(BoardError, match="overlap"):
        board.check_layout()
    spec["beats"][0]["elements"][1]["allow_overlap_with"] = ["title"]
    Board(spec, tmp_path, verbose=False).check_layout()


# an element pushed off the canvas fails qc
def test_layout_qc_rejects_elements_leaving_the_canvas(tmp_path: Path) -> None:
    spec = _spec()
    spec["beats"][0]["elements"][0]["x"] = 0.98
    with pytest.raises(BoardError, match="canvas"):
        Board(spec, tmp_path, verbose=False).check_layout()


# beats that overlap in time or run past the duration are errors
def test_overlapping_beats_and_short_boards_are_rejected(tmp_path: Path) -> None:
    spec = _spec()
    spec["beats"][0]["end"] = 1.5
    with pytest.raises(BoardError, match="overlap in time"):
        Board(spec, tmp_path, verbose=False)
    spec = _spec(duration=0.5)
    with pytest.raises(BoardError):
        Board(spec, tmp_path, verbose=False)


# unknown enter names and missing image files fail at construction
def test_unknown_enter_and_missing_image_fail_fast(tmp_path: Path) -> None:
    spec = _spec()
    spec["beats"][0]["elements"][0]["enter"] = "teleport"
    with pytest.raises(BoardError, match="unknown enter"):
        Board(spec, tmp_path, verbose=False)
    spec = _spec()
    spec["beats"][0]["elements"].append({"id": "img", "kind": "image", "file": "missing.png", "x": 0.5, "y": 0.6})
    with pytest.raises(BoardError, match="not found"):
        Board(spec, tmp_path, verbose=False)


# write a small alignment file with eight timed words
def _alignment(tmp_path: Path) -> Path:
    words = [("If", 0.0), ("you", 0.2), ("woke", 0.4), ("up", 0.6), ("today,", 0.8), ("you", 1.0), ("should", 1.2), ("sleep.", 1.5)]
    payload = {"words": [{"type": "word", "text": t, "start": s, "end": s + 0.15} for t, s in words]}
    path = tmp_path / "narration.alignment.json"
    path.write_text(json.dumps(payload))
    return path


# anchors resolve forward from a time by occurrence and by phrase
def test_word_anchors_resolve_forward_and_by_occurrence(tmp_path: Path) -> None:
    words = board_module.load_words(_alignment(tmp_path))
    assert find_word_time(words, "woke", 0.0) == pytest.approx(0.4)
    assert find_word_time(words, "you", 0.0) == pytest.approx(0.2)
    assert find_word_time(words, "you#2", 0.0) == pytest.approx(1.0)
    assert find_word_time(words, "you", 0.5) == pytest.approx(1.0)
    assert find_word_time(words, "woke up", 0.0) == pytest.approx(0.4)
    with pytest.raises(BoardError, match="not found"):
        find_word_time(words, "banana", 0.0)


# exact token matches beat prefix matches but prefixes still work as a fallback
def test_word_anchors_prefer_exact_tokens_over_prefixes() -> None:
    words = [{"text": t, "start": s, "end": s + 0.1} for t, s in
             [("Everyone.", 0.0), ("Every", 0.5), ("job", 0.7), ("some", 1.0), ("So", 1.4), ("Tamay's", 2.0)]]
    assert find_word_time(words, "Every", 0.0) == pytest.approx(0.5)      # not "Everyone"
    assert find_word_time(words, "So", 0.0) == pytest.approx(1.4)         # not "some"
    assert find_word_time(words, "Tamay", 0.0) == pytest.approx(2.0)      # prefix fallback still works
    assert find_word_time(words, "Every job", 0.0) == pytest.approx(0.5)


# beat and element starts can be word anchors
def test_beats_anchor_to_words(tmp_path: Path) -> None:
    _alignment(tmp_path)
    spec = _spec()
    spec["alignment"] = "narration.alignment.json"
    spec["beats"][1]["at"] = "word:should"
    spec["beats"][0]["elements"][1]["at"] = "word:woke"
    board = Board(spec, tmp_path, verbose=False)
    assert board.beats[1].start == pytest.approx(1.2)
    assert board.beats[0].elements[1].start == pytest.approx(0.4)


# long display text produces a warning instead of an error
def test_text_over_six_words_warns_but_renders(tmp_path: Path) -> None:
    spec = _spec()
    spec["beats"][0]["elements"][0]["text"] = "one two three four five six seven"
    spec["beats"][0]["elements"][0]["size"] = 12
    board = Board(spec, tmp_path, verbose=False)
    assert any("words" in warning for warning in board.warnings)


# the glyph check flags an arrow the display font lacks
def test_missing_glyphs_reports_arrows_for_display_fonts() -> None:
    from PIL import ImageFont

    from helpers.board import FONT_CACHE_DIR, missing_glyphs

    lilita = FONT_CACHE_DIR / "LilitaOne-Regular.ttf"
    if not lilita.exists():
        pytest.skip("Lilita One not fetched")
    font = ImageFont.truetype(str(lilita), 40)
    assert missing_glyphs(font, "$1 to $0.25") == []
    assert "→" in missing_glyphs(font, "$1 → $0.25")


# text with undrawable characters adds a warning
def test_text_with_unsupported_glyphs_warns(tmp_path: Path) -> None:
    spec = _spec()
    spec["beats"][0]["elements"][0]["text"] = "A → B"
    board = Board(spec, tmp_path, verbose=False)
    fonts = board.fonts.describe()
    if "LilitaOne" not in fonts["display"]:
        pytest.skip("display font differs on this machine")
    assert any("cannot draw" in warning for warning in board.warnings)


# every sfx kind is short and normalized to one
def test_synth_sfx_is_normalized_and_short() -> None:
    for kind in ("whoosh", "pop", "glitch"):
        sample = synth_sfx(kind, seed=1)
        assert 0 < len(sample) <= board_module.SAMPLE_RATE // 2
        assert abs(float(sample.max())) <= 1.0


# a real render produces a video with audio and a valid edl
@pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
def test_render_writes_video_and_valid_edl(tmp_path: Path) -> None:
    spec = _spec(sfx="all")
    board = Board(spec, tmp_path, verbose=False)
    board.check_layout()
    out = tmp_path / "board.mp4"
    board.render(out, crf=30)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-show_entries", "stream=codec_type",
                            "-of", "json", str(out)], capture_output=True, text=True, check=True)
    info = json.loads(probe.stdout)
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert float(info["format"]["duration"]) == pytest.approx(2.0, abs=0.15)
    edl_path = tmp_path / "edl.json"
    write_edl(board, out, edl_path, subtitles=None, workflow="test brief")
    edl = json.loads(edl_path.read_text())
    assert edl["version"] == 2 and edl["sources"]["board"] == str(out.resolve())
    validate_edl(edl, tmp_path, require_caption_provenance=True)


# captions in the edl require a board alignment
def test_write_edl_requires_alignment_for_captions(tmp_path: Path) -> None:
    board = Board(_spec(), tmp_path, verbose=False)
    subs = tmp_path / "master.ass"
    subs.write_text("[Events]\n")
    with pytest.raises(BoardError, match="alignment"):
        write_edl(board, tmp_path / "board.mp4", tmp_path / "edl.json", subtitles=subs, workflow="x")


# invalid output clocks fail before rendering
@pytest.mark.parametrize('fps', [0, float('nan'), float('inf'), 121])
def test_invalid_board_clock(tmp_path, fps):
    with pytest.raises(BoardError, match='fps'):
        Board(_spec(fps=fps), tmp_path, verbose=False)


# existing movies and contact sheets cannot be overwritten
def test_board_preserves_existing_outputs(tmp_path):
    board = Board(_spec(), tmp_path, verbose=False)
    output = tmp_path / 'existing.mp4'
    output.write_bytes(b'keep')
    with pytest.raises(BoardError, match='already exists'):
        board.render(output)
    with pytest.raises(BoardError, match='already exists'):
        board.contact_sheet(output)
    assert output.read_bytes() == b'keep'


# duplicate cli output paths fail without replacing the source specification
def test_cli_rejects_aliases_before_writing(tmp_path):
    path = tmp_path/'board.json'
    path.write_text(json.dumps(_spec()))
    output = tmp_path/'shared.json'
    result = subprocess.run([__import__('sys').executable, str(Path(board_module.__file__)),
        str(path), '--resolve-only', '--manifest', str(output), '--timeline', str(output)], capture_output=True, text=True)
    assert result.returncode != 0 and 'distinct path' in result.stderr
    assert not output.exists()


# a beat without a start follows the explicitly resolved previous end
@pytest.mark.parametrize('at', [None, 'next'])
def test_review_implicit_beat_start(tmp_path, at):
    spec = _spec()
    spec['beats'][0]['end'] = 1
    if at is None:
        del spec['beats'][1]['at']
    else:
        spec['beats'][1]['at'] = at
    board = Board(spec, tmp_path, verbose=False)
    assert [(beat.start, beat.end) for beat in board.beats] == [(0, 1), (1, 2)]


# unsupported element treatments cannot silently render as a different treatment
@pytest.mark.parametrize('field,value', [('enter','type'), ('fit','typo')])
def test_review_bad_element_treatment(tmp_path, field, value):
    spec = _spec()
    spec['beats'][0]['elements'][0][field] = value
    with pytest.raises(BoardError):
        Board(spec, tmp_path, verbose=False)
