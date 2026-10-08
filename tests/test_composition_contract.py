"""Composition validation and audit commands protect project inputs."""

import json
import subprocess
import sys

import pytest

from helpers.cut_list import compile_shots, validate
from helpers.verify_edit import caption_timing, review_sheets


# small authored timelines do not require external media during structural checks
@pytest.fixture
def manifest():
    return {
        "version": 3,
        "fps": 30,
        "total_frames": 30,
        "canvas": [160, 90],
        "picture": [0, 0, 160, 90],
        "sources": {},
        "shots": [
            {
                "id": "blank",
                "source": None,
                "start_frame": 0,
                "end_frame": 30,
                "beat": "pause",
                "reason": "authored gap",
            }
        ],
        "audio": [],
        "cards": [],
        "words": {},
        "fonts": {},
    }


# unsupported picture clocks fail before media is staged
@pytest.mark.parametrize("fps", [24, "30000/1001", True])
def test_unsupported_composition_rate(manifest, tmp_path, fps):
    manifest["fps"] = fps
    with pytest.raises(ValueError, match="fps=30"):
        validate(manifest, tmp_path, check_files=False)


# omitted audio is rejected before creating the build directory
def test_empty_audio_fails_before_staging(manifest, tmp_path):
    from helpers._composition import build

    path = tmp_path / "edit.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="requires declared non silent audio"):
        build(path, tmp_path / "final.mp4")
    assert not (tmp_path / "final_build").exists()


# command output paths cannot replace a project manifest
@pytest.mark.parametrize(
    "script, arguments", [("cut_list.py", []), ("verify_edit.py", ["missing.mp4"])]
)
def test_cli_preserves_input_manifest(tmp_path, script, arguments):
    path = tmp_path / "edit.json"
    path.write_text("{}")
    result = subprocess.run(
        [
            sys.executable,
            "helpers/" + script,
            str(path),
            *arguments,
            "--out",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "new file" in result.stderr
    assert path.read_text() == "{}"


# existing review images remain intact when a report is requested again
def test_review_sheets_preserve_existing_files(tmp_path):
    image = tmp_path / "review_001.jpg"
    image.write_bytes(b"previous review")
    with pytest.raises(FileExistsError):
        review_sheets({}, "missing.mp4", tmp_path)
    assert image.read_bytes() == b"previous review"


# caption schedule auditing reports late text rather than rounding it away
def test_caption_schedule_reports_late_card():
    m = {
        "words": {"w": {"text": "word", "start_sample": 0}},
        "cards": [{"word_ids": ["w"], "start_frame": 3}],
    }
    row = caption_timing(m)[0]
    assert row["offset_ms"] == 100 and not row["within_one_frame"]


# compilation assigns a continuous picture clock without altering the input rows
def test_compiled_shots_cover_requested_frames():
    rows = [{"source": "a", "frames": 7}, {"source": "b", "frames": 11}]
    result = compile_shots(rows)
    assert [(s["start_frame"], s["end_frame"]) for s in result] == [(0, 7), (7, 18)]
    assert "start_frame" not in rows[0]


# phrase cards align to their first word while covering later spoken words
def test_review_multiword_card_timing():
    manifest = {'words':{'a':{'text':'hello','start_sample':0}, 'b':{'text':'world','start_sample':9600}}, 'cards':[{'word_ids':['a','b'],'start_frame':0,'end_frame':15}]}
    assert all(row['within_one_frame'] for row in caption_timing(manifest))
    manifest['cards'][0]['end_frame'] = 3
    assert not caption_timing(manifest)[1]['within_one_frame']
