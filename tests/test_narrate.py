"""tests for the narration helper covering script parsing chunking alignment conversion and srt output
no api calls are made here
"""

import json
from pathlib import Path

import pytest

from helpers import narrate


# pauses become ssml breaks and headings notes and tags are dropped
def test_parse_script_turns_pauses_into_breaks_and_drops_notes() -> None:
    script = """# Title is never spoken

If you woke up today, go back to sleep. [pause 0.4] Because it's bad.

<!-- producer note -->
Second paragraph [laughs] here.
"""
    paragraphs = narrate.parse_script(script, keep_v3_tags=False)
    assert len(paragraphs) == 2
    assert '<break time="0.4s" />' in paragraphs[0]["text"]
    assert "[laughs]" not in paragraphs[1]["text"]
    assert "producer note" not in paragraphs[1]["text"]
    assert "Title" not in paragraphs[0]["text"]


# audio tags survive only when v3 tags are kept
def test_parse_script_keeps_v3_tags_only_for_v3() -> None:
    paragraphs = narrate.parse_script("Hello [sarcastic] world.", keep_v3_tags=True)
    assert "[sarcastic]" in paragraphs[0]["text"]
    paragraphs = narrate.parse_script(
        "Hello [sarcastic] world. [pause]", keep_v3_tags=False
    )
    assert "[sarcastic]" not in paragraphs[0]["text"]
    assert '<break time="0.5s" />' in paragraphs[0]["text"]


# a script with no speakable text is an error
def test_parse_script_rejects_empty_scripts() -> None:
    with pytest.raises(ValueError):
        narrate.parse_script("# only a heading\n\n<!-- note -->", keep_v3_tags=False)


# paragraphs are grouped under the limit and oversized ones are rejected
def test_chunk_paragraphs_respects_limit() -> None:
    paragraphs = [{"text": "a" * 1500} for _ in range(4)]
    chunks = narrate.chunk_paragraphs(paragraphs, max_chars=3200)
    assert len(chunks) == 2
    assert all(len(chunk) <= 3200 for chunk in chunks)
    with pytest.raises(ValueError):
        narrate.chunk_paragraphs([{"text": "b" * 5000}], max_chars=4200)


# ssml breaks are not counted as spoken characters
def test_spoken_characters_ignores_breaks() -> None:
    assert narrate.spoken_characters('Hi <break time="0.5s" /> there') == len(
        "Hi  there"
    )


# build a character level alignment from timed tokens
def _alignment(tokens: list[tuple[str, float, float]]) -> dict:
    chars, starts, ends = [], [], []
    for index, (token, start, end) in enumerate(tokens):
        step = (end - start) / max(1, len(token))
        for offset, ch in enumerate(token):
            chars.append(ch)
            starts.append(round(start + offset * step, 3))
            ends.append(round(start + (offset + 1) * step, 3))
        if index != len(tokens) - 1:
            chars.append(" ")
            starts.append(end)
            ends.append(end)
    return {
        "characters": chars,
        "character_start_times_seconds": starts,
        "character_end_times_seconds": ends,
    }


# markup tokens are dropped and word times are offset
def test_words_from_alignment_drops_markup_and_offsets_times() -> None:
    alignment = _alignment(
        [
            ("Because", 0.0, 0.4),
            ("<break", 0.4, 0.4),
            ('time="0.5s"', 0.4, 0.4),
            ("/>", 0.4, 0.4),
            ("it's", 0.9, 1.1),
            ("[laughs]", 1.1, 1.1),
            ("bad.", 1.2, 1.5),
        ]
    )
    words = narrate.words_from_alignment(alignment, offset=10.0)
    assert [w["text"] for w in words] == ["Because", "it's", "bad."]
    assert words[0]["start"] == 10.0
    assert words[-1]["end"] == pytest.approx(11.5, abs=0.01)
    assert all(w["end"] > w["start"] for w in words)


# alignment arrays of different lengths are an error
def test_words_from_alignment_rejects_mismatched_arrays() -> None:
    with pytest.raises(ValueError):
        narrate.words_from_alignment(
            {
                "characters": ["a"],
                "character_start_times_seconds": [],
                "character_end_times_seconds": [],
            }
        )


# srt cues break on sentence ends
def test_write_srt_breaks_on_sentences(tmp_path: Path) -> None:
    words = [
        {"text": t, "start": i * 0.3, "end": i * 0.3 + 0.25}
        for i, t in enumerate("If you woke up today. Go back to sleep.".split())
    ]
    cues = narrate.write_srt(words, tmp_path / "out.srt")
    assert cues == 2
    text = (tmp_path / "out.srt").read_text()
    assert "00:00:00,000 -->" in text
    assert "Go back to sleep." in text
    assert json.dumps(text)  # plain text, no binary


# v3 pause tags stay native and timed ssml pauses fail before generation
def test_v3_pause_contract():
    assert (
        narrate.parse_script("Hello [pause] world", keep_v3_tags=True)[0]["text"]
        == "Hello [pause] world"
    )
    for text in ["Hello [pause 0.5]", 'Hello <break time="1s" />']:
        with pytest.raises(ValueError):
            narrate.parse_script(text, keep_v3_tags=True)


# multiword tags do not hide the spoken words after them
def test_multiword_alignment_tags():
    words = narrate.words_from_alignment(
        _alignment([("[clears", 0, 0), ("throat]", 0, 0), ("Hello", 0.1, 0.5)])
    )
    assert [word["text"] for word in words] == ["Hello"]


# malformed provider times cannot produce apparently valid captions
@pytest.mark.parametrize(
    "start,end", [(-1, 1), (float("nan"), 1), (0, float("inf")), (1, 0), (0, 0)]
)
def test_reject_invalid_alignment_times(start, end):
    with pytest.raises(ValueError):
        narrate.words_from_alignment(
            {
                "characters": ["x"],
                "character_start_times_seconds": [start],
                "character_end_times_seconds": [end],
            }
        )


# short caption cues retain their true end instead of overlapping the next cue
def test_short_cues_do_not_extend(tmp_path):
    narrate.write_srt(
        [
            {"text": "Hi.", "start": 0, "end": 0.1},
            {"text": "Bye.", "start": 0.1, "end": 0.2},
        ],
        tmp_path / "out.srt",
    )
    assert "00:00:00,000 --> 00:00:00,100" in (tmp_path / "out.srt").read_text()


# silence markers alone do not trigger a speech request
def test_pause_only_script_rejected():
    with pytest.raises(ValueError):
        narrate.parse_script("[pause]", keep_v3_tags=False)


# ordinary bracketed speech survives provider alignment conversion
def test_review_bracketed_words():
    from helpers.narrate import words_from_alignment
    text = 'hello [world] [laughs]'
    words = words_from_alignment({'characters':list(text), 'character_start_times_seconds':[i * .1 for i in range(len(text))], 'character_end_times_seconds':[(i + 1) * .1 for i in range(len(text))]})
    assert [w['text'] for w in words] == ['hello', '[world]']


# paragraph silence terminates a subtitle cue even without punctuation
def test_review_caption_gap(tmp_path):
    from helpers.narrate import write_srt
    path = tmp_path / 'out.srt'
    assert write_srt([{'text':'hello','start':0,'end':.5},{'text':'world','start':.82,'end':1}], path) == 2
