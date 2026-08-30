"""Unit tests for transcribe.py — only the pure conversion logic.

API calls require a live DashScope key and external network; those are
intentionally out of scope here. Run an end-to-end smoke manually:

    python helpers/transcribe.py path/to/clip.mp4 --language zh
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "helpers"))


@pytest.fixture
def transcribe_mod():
    import transcribe as t
    return t


def test_convert_basic_sentence(transcribe_mod):
    """One sentence with two words gets flattened into Scribe-shaped words[]."""
    sentences = [
        {
            "begin_time": 0,
            "end_time": 1500,
            "text": "你好世界",
            "words": [
                {"begin_time": 0,   "end_time": 500,  "text": "你好",   "punctuation": ""},
                {"begin_time": 500, "end_time": 1500, "text": "世界",   "punctuation": "。"},
            ],
        }
    ]
    out = transcribe_mod._convert_dashscope_to_scribe(sentences, language_hint="zh")
    assert out["language_code"] == "zh"
    assert out["_source"].startswith("dashscope-")
    assert len(out["words"]) == 2
    assert out["words"][0] == {"text": "你好", "start": 0.0, "end": 0.5, "type": "word"}
    # Punctuation gets folded into the preceding word's text
    assert out["words"][1] == {"text": "世界。", "start": 0.5, "end": 1.5, "type": "word"}


def test_convert_drops_empty_text(transcribe_mod):
    """Whitespace-only / empty word entries are skipped, not emitted as junk."""
    sentences = [
        {"words": [
            {"begin_time": 0,    "end_time": 100, "text": ""},
            {"begin_time": 100,  "end_time": 200, "text": "   "},
            {"begin_time": 200,  "end_time": 400, "text": "hello"},
        ]}
    ]
    out = transcribe_mod._convert_dashscope_to_scribe(sentences, language_hint=None)
    assert len(out["words"]) == 1
    assert out["words"][0]["text"] == "hello"
    # No language hint → "auto"
    assert out["language_code"] == "auto"


def test_convert_multiple_sentences(transcribe_mod):
    """Words from multiple sentences flatten into a single ordered list."""
    sentences = [
        {"words": [
            {"begin_time": 0,    "end_time": 500,  "text": "first"},
        ]},
        {"words": [
            {"begin_time": 1000, "end_time": 1500, "text": "second"},
            {"begin_time": 1500, "end_time": 2000, "text": "third"},
        ]},
    ]
    out = transcribe_mod._convert_dashscope_to_scribe(sentences, language_hint="en")
    assert [w["text"] for w in out["words"]] == ["first", "second", "third"]
    assert out["words"][0]["start"] == 0.0
    assert out["words"][-1]["end"] == 2.0


def test_convert_tolerates_missing_or_bad_timestamps(transcribe_mod):
    """A word with non-numeric timestamps is skipped rather than crashing
    the whole conversion."""
    sentences = [
        {"words": [
            {"begin_time": "bad", "end_time": 500, "text": "junk"},
            {"begin_time": 0,     "end_time": 500, "text": "good"},
        ]}
    ]
    out = transcribe_mod._convert_dashscope_to_scribe(sentences, language_hint=None)
    assert [w["text"] for w in out["words"]] == ["good"]


def test_convert_empty_input(transcribe_mod):
    """Empty / None input returns a structurally valid envelope with no words."""
    out = transcribe_mod._convert_dashscope_to_scribe([], language_hint=None)
    assert out["words"] == []
    assert "language_code" in out and "_source" in out

    out_none = transcribe_mod._convert_dashscope_to_scribe(None, language_hint=None)
    assert out_none["words"] == []


def test_output_shape_compatible_with_recommender(transcribe_mod, tmp_path):
    """Conversion produces JSON that recommend_edit_plan.load_transcript_words
    can consume directly — this is the cross-module contract we promise."""
    import json
    import recommend_edit_plan as rec

    sentences = [
        {"words": [
            {"begin_time": 1000, "end_time": 1500, "text": "hello", "punctuation": ""},
            {"begin_time": 1500, "end_time": 2000, "text": "world", "punctuation": "."},
        ]}
    ]
    transcript = transcribe_mod._convert_dashscope_to_scribe(sentences, language_hint="en")

    out_file = tmp_path / "transcript.json"
    out_file.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")

    words = rec.load_transcript_words(out_file)
    assert len(words) == 2
    assert words[0]["text"] == "hello"
    assert words[1]["text"] == "world."
    assert words[0]["start"] == 1.0
    assert words[1]["end"] == 2.0
