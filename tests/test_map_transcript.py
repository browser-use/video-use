"""Word mapping keeps source speech intact and makes comparison differences explicit."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from map_transcript import compare_words, map_words


# shifting a whole audio clip must shift each intact word by the same sample offset
def test_map_words_preserves_source_intervals():
    transcript = {
        "words": [
            {"text": "before", "start": 0, "end": 1},
            {"text": "Hello", "start": 1, "end": 1.5},
            {"text": "noise", "start": 1.5, "end": 1.6, "type": "audio_event"},
            {"text": "world", "start": 1.6, "end": 2},
            {"text": "after", "start": 2, "end": 2.5},
        ]
    }
    clip = {
        "id": "voice",
        "source_start_sample": 48000,
        "start_sample": 96000,
        "sample_count": 48000,
    }
    words = map_words(transcript, clip, "speaker", prefix="voice_")
    assert list(words) == ["voice_1", "voice_3"]
    assert words["voice_1"]["source_start_sample"] == 48000
    assert words["voice_1"]["start_sample"] == 96000
    assert words["voice_1"]["end_sample"] == 120000
    assert words["voice_3"]["end_sample"] == 144000
    assert words["voice_1"]["source"] == "speaker"
    assert words["voice_1"]["audio_clip"] == "voice"


# trimming through either edge of a word must fail rather than invent a partial caption
@pytest.mark.parametrize("start,end", [(0.9, 1.2), (1.8, 2.1)])
def test_partial_words_are_rejected(start, end):
    clip = {
        "id": "voice",
        "source_start_sample": 48000,
        "start_sample": 0,
        "sample_count": 48000,
    }
    with pytest.raises(ValueError, match="cuts through word"):
        map_words(
            {"words": [{"text": "speech", "start": start, "end": end}]}, clip, "speaker"
        )


# invalid word timestamps cannot become reversed or empty output intervals
@pytest.mark.parametrize("start,end", [(1.5, 1.2), (1, 1), (-1, 0.1)])
def test_invalid_word_timestamps(start, end):
    clip = {
        "id": "voice",
        "source_start_sample": 0,
        "start_sample": 0,
        "sample_count": 96000,
    }
    with pytest.raises(ValueError, match="word timestamps"):
        map_words(
            {"words": [{"text": "speech", "start": start, "end": end}]}, clip, "speaker"
        )


# sequence comparison keeps timing drift and changed words visible to the editor
def test_comparison_reports_matches_and_replacements():
    planned = {
        "a": {"text": "Hello,", "start_sample": 48000},
        "b": {"text": "world", "start_sample": 96000},
    }
    observed = {
        "words": [
            {"text": "hello", "start": 1.025, "end": 1.5},
            {"text": "there", "start": 2, "end": 2.4},
        ]
    }
    result = compare_words(planned, observed)
    assert result["rows"][0] == {
        "operation": "match",
        "text": "Hello,",
        "onset_delta_ms": 25,
    }
    assert result["rows"][1] == {
        "operation": "replace",
        "planned": ["world"],
        "heard": ["there"],
    }
    assert "not automatic proof" in result["limit"]


# half open voice intervals exclude unrelated speech and their end boundary
def test_comparison_limits_observed_words_to_voice_intervals():
    planned = {"a": {"text": "hello", "start_sample": 48000}}
    observed = {
        "words": [
            {"text": "before", "start": 0.5},
            {"text": "hello", "start": 1},
            {"text": "after", "start": 2},
        ]
    }
    result = compare_words(planned, observed, [(48000, 96000)])
    assert result["rows"] == [
        {"operation": "match", "text": "hello", "onset_delta_ms": 0}
    ]


# both command modes protect input documents including hard linked output aliases
@pytest.mark.parametrize("mode", ["map", "compare"])
@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize("alias", [False, True])
def test_cli_preserves_input_documents(tmp_path, mode, which, alias):
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_text("{}")
    second.write_text("{}")
    protected = [first, second][which]
    output = tmp_path / "alias.json" if alias else protected
    if alias:
        output.hardlink_to(protected)
    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "helpers/map_transcript.py"),
        mode,
        str(first),
        str(second),
        "--out",
        str(output),
    ]
    if mode == "map":
        command += ["--source", "speaker"]
    result = subprocess.run(command, capture_output=True)
    assert result.returncode != 0 and b"overwrite" in result.stderr
    assert protected.read_text() == "{}"
