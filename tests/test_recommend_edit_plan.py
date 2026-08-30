"""Tests for recommend_edit_plan."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def rec():
    """Convenience: import the module under test as a fixture."""
    import recommend_edit_plan as r
    return r


@pytest.fixture
def sde():
    import srt_driven_edit as s
    return s


def write_transcript(path: Path, words: list[dict]) -> None:
    """Wrap a flat list of {text,start,end,type} dicts in a Scribe-style envelope."""
    path.write_text(
        json.dumps({"language_code": "en", "words": words}, ensure_ascii=False),
        encoding="utf-8",
    )


def write_srt_cues(path, cues, helpers_ns):
    helpers_ns.write_srt(path, cues, encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. English exact match — high score, correct range
# ---------------------------------------------------------------------------


def test_english_exact_match(rec, helpers_ns, tmp_path):
    srt = tmp_path / "script.srt"
    transcript = tmp_path / "transcript.json"
    # Cue duration matches candidate duration exactly so duration warnings stay quiet.
    helpers_ns.write_srt(srt, [
        (1, 0.0, 1.0, "Hello world"),
    ])
    write_transcript(transcript, [
        {"text": "Hello",  "start": 5.0, "end": 5.4, "type": "word"},
        {"text": "world.", "start": 5.4, "end": 6.0, "type": "word"},
        {"text": "Other",  "start": 10.0, "end": 10.5, "type": "word"},
    ])

    assignments = rec.recommend(
        script_srt=srt, transcript=transcript, source=Path("fake.mp4"),
        output=tmp_path / "plan.json",
    )
    assert len(assignments) == 1
    a = assignments[0]
    assert a.cand is not None
    assert abs(a.cand.start - 5.0) < 1e-6
    assert abs(a.cand.end - 6.0) < 1e-6
    assert a.score > 0.85, f"exact-text match should score high, got {a.score}"
    assert not a.warnings, f"unexpected warnings: {a.warnings}"


# ---------------------------------------------------------------------------
# 2. Chinese match — CJK Jaccard path
# ---------------------------------------------------------------------------


def test_chinese_match(rec, helpers_ns, tmp_path):
    srt = tmp_path / "script.srt"
    transcript = tmp_path / "transcript.json"
    helpers_ns.write_srt(srt, [
        (1, 0.0, 3.0, "我们这季度把规划器重写了。"),
    ])
    write_transcript(transcript, [
        {"text": "我们",   "start": 12.0, "end": 12.4, "type": "word"},
        {"text": "这",     "start": 12.4, "end": 12.5, "type": "word"},
        {"text": "季度",   "start": 12.5, "end": 13.0, "type": "word"},
        {"text": "把",     "start": 13.0, "end": 13.1, "type": "word"},
        {"text": "规划器", "start": 13.1, "end": 14.0, "type": "word"},
        {"text": "重写了。", "start": 14.0, "end": 15.0, "type": "word"},
        # A distractor far away
        {"text": "不相关的内容。", "start": 25.0, "end": 26.0, "type": "word"},
    ])

    assignments = rec.recommend(
        script_srt=srt, transcript=transcript, source=Path("fake.mp4"),
        output=tmp_path / "plan.json",
    )
    a = assignments[0]
    assert a.cand is not None
    assert abs(a.cand.start - 12.0) < 1e-6
    assert abs(a.cand.end - 15.0) < 1e-6
    assert a.score > 0.7


# ---------------------------------------------------------------------------
# 3. Punctuation + case differences still match
# ---------------------------------------------------------------------------


def test_punct_and_case_invariant(rec, helpers_ns, tmp_path):
    srt = tmp_path / "script.srt"
    transcript = tmp_path / "transcript.json"
    # SRT: lowercase, no punct, matching duration
    helpers_ns.write_srt(srt, [
        (1, 0.0, 2.0, "hello there friends"),
    ])
    # Transcript: mixed case + phrase punct (commas keep words grouped); the
    # SENTENCE-end '!' only on the last word so all three stay in one candidate.
    write_transcript(transcript, [
        {"text": "HELLO,",   "start": 1.0, "end": 1.5, "type": "word"},
        {"text": "There,",   "start": 1.5, "end": 2.0, "type": "word"},
        {"text": "FRIENDS!", "start": 2.0, "end": 3.0, "type": "word"},
    ])
    assignments = rec.recommend(
        script_srt=srt, transcript=transcript, source=Path("fake.mp4"),
        output=tmp_path / "plan.json",
    )
    a = assignments[0]
    assert a.cand is not None
    assert a.score > 0.85, f"normalization should erase case+punct, got {a.score}"


# ---------------------------------------------------------------------------
# 4. Silence gap splits candidates
# ---------------------------------------------------------------------------


def test_silence_gap_splits(rec, tmp_path):
    """Two phrases separated by a 1.0s silence should produce two candidates,
    not one — even though neither phrase ends in sentence-end punctuation.
    """
    transcript = tmp_path / "transcript.json"
    write_transcript(transcript, [
        {"text": "alpha", "start": 1.0, "end": 1.4, "type": "word"},
        {"text": "beta",  "start": 1.4, "end": 2.0, "type": "word"},
        # 1.0s silence
        {"text": "gamma", "start": 3.0, "end": 3.4, "type": "word"},
        {"text": "delta", "start": 3.4, "end": 4.0, "type": "word"},
    ])
    words = rec.load_transcript_words(transcript)
    candidates = rec.build_candidates(words, gap_threshold=0.5)
    assert len(candidates) == 2
    assert abs(candidates[0].start - 1.0) < 1e-6 and abs(candidates[0].end - 2.0) < 1e-6
    assert abs(candidates[1].start - 3.0) < 1e-6 and abs(candidates[1].end - 4.0) < 1e-6
    # Tightening the gap shouldn't merge them (still well over threshold)
    # Loosening past 1.0s should:
    merged = rec.build_candidates(words, gap_threshold=1.1)
    assert len(merged) == 1


# ---------------------------------------------------------------------------
# 5. Low-score match emits warning
# ---------------------------------------------------------------------------


def test_low_score_warning(rec, helpers_ns, tmp_path):
    srt = tmp_path / "script.srt"
    transcript = tmp_path / "transcript.json"
    # Cue text shares almost no tokens with any candidate
    helpers_ns.write_srt(srt, [
        (1, 0.0, 2.0, "quantum entanglement decoherence"),
    ])
    write_transcript(transcript, [
        {"text": "apple",  "start": 1.0, "end": 1.5, "type": "word"},
        {"text": "banana", "start": 1.5, "end": 2.0, "type": "word"},
    ])
    assignments = rec.recommend(
        script_srt=srt, transcript=transcript, source=Path("fake.mp4"),
        output=tmp_path / "plan.json",
        min_score=0.5,  # set high to force the warning
    )
    a = assignments[0]
    assert a.cand is not None  # still got SOME candidate
    assert any("low score" in w for w in a.warnings)


# ---------------------------------------------------------------------------
# 6. SRT id ordering preserved in output
# ---------------------------------------------------------------------------


def test_ids_preserved(rec, helpers_ns, tmp_path):
    srt = tmp_path / "script.srt"
    transcript = tmp_path / "transcript.json"
    helpers_ns.write_srt(srt, [
        (1, 0.0, 1.0, "alpha"),
        (2, 1.0, 2.0, "beta"),
        (3, 2.0, 3.0, "gamma"),
    ])
    write_transcript(transcript, [
        {"text": "alpha.", "start": 1.0, "end": 1.5, "type": "word"},
        {"text": "beta.",  "start": 5.0, "end": 5.5, "type": "word"},
        {"text": "gamma.", "start": 10.0, "end": 10.5, "type": "word"},
    ])
    out = tmp_path / "plan.json"
    rec.recommend(
        script_srt=srt, transcript=transcript, source=Path("fake.mp4"),
        output=out,
    )
    plan_rows = json.loads(out.read_text(encoding="utf-8"))
    assert [r["id"] for r in plan_rows] == [1, 2, 3]


# ---------------------------------------------------------------------------
# 7. Output is parseable by srt_driven_edit.parse_plan
# ---------------------------------------------------------------------------


def test_output_is_parseable_by_sde(rec, sde, helpers_ns, tmp_path):
    srt = tmp_path / "script.srt"
    transcript = tmp_path / "transcript.json"
    helpers_ns.write_srt(srt, [
        (1, 0.0, 1.0, "alpha"),
        (2, 1.0, 2.0, "beta"),
    ])
    write_transcript(transcript, [
        {"text": "alpha.", "start": 1.0, "end": 1.5, "type": "word"},
        {"text": "beta.",  "start": 5.0, "end": 5.5, "type": "word"},
    ])
    out = tmp_path / "plan.json"
    rec.recommend(
        script_srt=srt, transcript=transcript, source=Path("fake.mp4"),
        output=out,
    )

    sources, voices, entries = sde.parse_plan(out)
    assert sources == {} and voices == {}  # Form A — no maps
    assert [e.id for e in entries] == [1, 2]
    assert all(e.source_name == "_default" for e in entries)
    assert entries[0].source_start == 1.0 and entries[0].source_end == 1.5
    assert entries[1].source_start == 5.0 and entries[1].source_end == 5.5


# ---------------------------------------------------------------------------
# 8. Form B output carries the source name
# ---------------------------------------------------------------------------


def test_form_b_output(rec, sde, helpers_ns, tmp_path):
    srt = tmp_path / "script.srt"
    transcript = tmp_path / "transcript.json"
    helpers_ns.write_srt(srt, [(1, 0.0, 1.0, "alpha")])
    write_transcript(transcript, [
        {"text": "alpha.", "start": 1.0, "end": 1.5, "type": "word"},
    ])
    out = tmp_path / "plan.json"
    rec.recommend(
        script_srt=srt, transcript=transcript,
        source=tmp_path / "src.mp4", source_name="TAKE_A",
        output_format="form-b", output=out,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "TAKE_A" in data["sources"]
    assert data["segments"][0]["source"] == "TAKE_A"
    # And it's parseable by sde.parse_plan too
    sources, _, entries = sde.parse_plan(out)
    assert "TAKE_A" in sources
    assert entries[0].source_name == "TAKE_A"


# ---------------------------------------------------------------------------
# 9. No candidates → hard fail (per spec)
# ---------------------------------------------------------------------------


def test_no_candidates_aborts(rec, helpers_ns, tmp_path):
    srt = tmp_path / "script.srt"
    transcript = tmp_path / "transcript.json"
    helpers_ns.write_srt(srt, [(1, 0.0, 1.0, "alpha")])
    # Transcript has only audio_event (no words)
    write_transcript(transcript, [
        {"text": "(laughter)", "start": 1.0, "end": 2.0, "type": "audio_event"},
    ])
    with pytest.raises(SystemExit):
        rec.recommend(
            script_srt=srt, transcript=transcript, source=Path("fake.mp4"),
            output=tmp_path / "plan.json",
        )


# ---------------------------------------------------------------------------
# 10. Review markdown shows score + warnings
# ---------------------------------------------------------------------------


def test_review_markdown_content(rec, helpers_ns, tmp_path):
    srt = tmp_path / "script.srt"
    transcript = tmp_path / "transcript.json"
    helpers_ns.write_srt(srt, [(1, 0.0, 2.0, "Hello world")])
    write_transcript(transcript, [
        {"text": "Hello",  "start": 1.0, "end": 1.5, "type": "word"},
        {"text": "world.", "start": 1.5, "end": 2.0, "type": "word"},
    ])
    out = tmp_path / "plan.json"
    rec.recommend(
        script_srt=srt, transcript=transcript, source=Path("fake.mp4"),
        output=out,
    )
    review = (out.with_name(out.stem + "_review.md")).read_text(encoding="utf-8")
    assert "cue id=1" in review
    assert "Hello world" in review
    assert "**score**" in review
    assert "**source range**" in review


# ---------------------------------------------------------------------------
# 11. End-to-end: recommend → run_job → final mp4 exists
# ---------------------------------------------------------------------------


def test_e2e_recommend_then_render(
    rec, sde, helpers_ns, synth_av, tmp_path
):
    """Full chain: fabricated transcript → recommend → run_job → final.mp4."""
    srt = tmp_path / "script.srt"
    transcript = tmp_path / "transcript.json"
    plan = tmp_path / "plan.json"
    out_mp4 = tmp_path / "final.mp4"

    # 3 cues totaling 6s of output
    helpers_ns.write_srt(srt, [
        (1, 0.0, 2.0, "alpha beta"),
        (2, 2.0, 4.0, "gamma delta"),
        (3, 4.0, 6.0, "epsilon zeta"),
    ])
    # Transcript: words that match each cue at distinct, valid times in synth_av (30s)
    # Each candidate is exactly 2s — matches cue duration exactly so no on-short needed.
    write_transcript(transcript, [
        {"text": "alpha",   "start": 1.0, "end": 1.8, "type": "word"},
        {"text": "beta.",   "start": 1.8, "end": 3.0, "type": "word"},
        # silence gap
        {"text": "gamma",   "start": 8.0, "end": 8.8, "type": "word"},
        {"text": "delta.",  "start": 8.8, "end": 10.0, "type": "word"},
        # silence gap
        {"text": "epsilon", "start": 18.0, "end": 18.8, "type": "word"},
        {"text": "zeta.",   "start": 18.8, "end": 20.0, "type": "word"},
    ])

    assignments = rec.recommend(
        script_srt=srt, transcript=transcript, source=synth_av,
        output=plan,
    )
    assert len(assignments) == 3
    assert all(a.cand is not None for a in assignments)

    # Render via the existing pipeline
    ffmpeg_version = sde.preflight()["ffmpeg"]
    job = sde.Job(
        source=synth_av,
        srt=srt, plan=plan,
        voice=None, bg_volume=0.0,
        tolerance=0.5, trim_direction="tail", on_short="error",
        style="auto", fontsdir=None,
        output=out_mp4,
        name="e2e",
        no_cache=False, keep_intermediates=False, no_overwrite=False,
    )
    qc = sde.run_job(job, ffmpeg_version)
    assert qc["ok"] is True
    assert out_mp4.exists()
    assert abs(qc["duration"]["drift_ms"]) <= 200
