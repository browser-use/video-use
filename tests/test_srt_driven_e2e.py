"""End-to-end tests for srt_driven_edit.

Each test crafts an SRT + plan file inside tmp_path, runs run_job against
the session-scoped synthetic source video, and verifies output existence,
duration accuracy (within 200ms), and QC report contents.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

DEFAULT_CUES = [
    (1, 0.0, 2.0, "first cue"),
    (2, 2.0, 4.5, "second cue"),
    (3, 6.0, 8.5, "third cue with leading gap"),  # 1.5s gap before this
]

DEFAULT_PLAN = [
    (1, 1.0, 3.0),   # 2.0s from source[1.0-3.0]
    (2, 5.0, 7.5),   # 2.5s
    (3, 10.0, 12.5),  # 2.5s
]


def make_job(helpers_ns, srt_path, plan_path, tmp_path, *,
             source=None, voice=None, bg_volume=0.0,
             style="auto", no_overwrite=False, output=None):
    sde = helpers_ns.sde
    return sde.Job(
        source=source,
        srt=srt_path,
        plan=plan_path,
        voice=voice,
        bg_volume=bg_volume,
        tolerance=0.5,
        trim_direction="tail",
        on_short="error",
        style=style,
        fontsdir=None,
        output=output or (tmp_path / "out.mp4"),
        name=srt_path.stem,
        no_cache=False,
        keep_intermediates=False,
        no_overwrite=no_overwrite,
    )


@pytest.fixture
def ffmpeg_version(helpers_ns) -> str:
    return helpers_ns.sde.preflight()["ffmpeg"]


# ---------------------------------------------------------------------------
# 1. Basic e2e: source.mp4 + 3 cues → final has expected duration
# ---------------------------------------------------------------------------


def test_basic_single_job(helpers_ns, ffmpeg_version, synth_av, tmp_path):
    srt = tmp_path / "script.srt"
    plan = tmp_path / "plan.json"
    helpers_ns.write_srt(srt, DEFAULT_CUES)
    helpers_ns.write_plan_form_a(plan, DEFAULT_PLAN)

    job = make_job(helpers_ns, srt, plan, tmp_path, source=synth_av)
    qc = helpers_ns.sde.run_job(job, ffmpeg_version)

    assert qc["ok"] is True
    assert qc["duration"]["expected_s"] == 8.5
    assert abs(qc["duration"]["drift_ms"]) <= 200
    assert (tmp_path / "out.mp4").exists()
    assert qc["audio"]["mode"] == "silent"  # bg_volume=0, no voice


# ---------------------------------------------------------------------------
# 2. GB18030 SRT input — encoding fallback must let the pipeline complete
# ---------------------------------------------------------------------------


def test_gbk_srt_input(helpers_ns, ffmpeg_version, synth_av, tmp_path):
    srt = tmp_path / "script_gbk.srt"
    plan = tmp_path / "plan.json"
    cjk_cues = [
        (1, 0.0, 2.0, "第一条"),
        (2, 2.0, 4.5, "第二条"),
        (3, 6.0, 8.5, "第三条 含 gap"),
    ]
    helpers_ns.write_srt(srt, cjk_cues, encoding="gb18030")
    helpers_ns.write_plan_form_a(plan, DEFAULT_PLAN)

    job = make_job(helpers_ns, srt, plan, tmp_path, source=synth_av, style="auto")
    qc = helpers_ns.sde.run_job(job, ffmpeg_version)

    assert qc["ok"] is True
    assert "Microsoft YaHei UI" in qc["subtitles"]["force_style"], \
        "auto style should pick cjk-natural when SRT contains CJK"


# ---------------------------------------------------------------------------
# 3. CJK in output path — work_dir + ensure_safe_subs_path must save us
# ---------------------------------------------------------------------------


def test_cjk_in_output_path(helpers_ns, ffmpeg_version, synth_av, tmp_path):
    cjk_dir = tmp_path / "中文 目录"
    cjk_dir.mkdir()
    srt = cjk_dir / "字幕.srt"
    plan = cjk_dir / "plan.json"
    helpers_ns.write_srt(srt, DEFAULT_CUES)
    helpers_ns.write_plan_form_a(plan, DEFAULT_PLAN)

    out = cjk_dir / "成片.mp4"
    job = make_job(helpers_ns, srt, plan, tmp_path,
                   source=synth_av, output=out)
    qc = helpers_ns.sde.run_job(job, ffmpeg_version)

    assert qc["ok"] is True
    assert out.exists()


# ---------------------------------------------------------------------------
# 4. Per-segment voice — audio.mode should reflect voice usage
# ---------------------------------------------------------------------------


def test_per_segment_voice(helpers_ns, ffmpeg_version, synth_av, synth_voice, tmp_path):
    srt = tmp_path / "script.srt"
    plan = tmp_path / "plan.json"
    helpers_ns.write_srt(srt, DEFAULT_CUES)

    helpers_ns.write_plan_form_b(
        plan,
        sources={"A": str(synth_av)},
        voices={"v1": str(synth_voice)},
        segments=[
            {"id": 1, "source": "A", "source_start": "00:00:01,000",
             "source_end": "00:00:03,000", "voice": "v1"},
            {"id": 2, "source": "A", "source_start": "00:00:05,000",
             "source_end": "00:00:07,500"},
            {"id": 3, "source": "A", "source_start": "00:00:10,000",
             "source_end": "00:00:12,500"},
        ],
    )

    job = make_job(helpers_ns, srt, plan, tmp_path)  # source=None — Form B
    qc = helpers_ns.sde.run_job(job, ffmpeg_version)

    assert qc["ok"] is True
    assert qc["audio"]["voice_used"] is True
    assert qc["audio"]["mode"] == "voice_replace"  # bg_volume == 0


# ---------------------------------------------------------------------------
# 5. Video-only source + bg_volume > 0 → auto-degrade, no crash
# ---------------------------------------------------------------------------


def test_video_only_source_with_bg_volume(
    helpers_ns, ffmpeg_version, synth_v_only, tmp_path, capsys
):
    srt = tmp_path / "script.srt"
    plan = tmp_path / "plan.json"
    helpers_ns.write_srt(srt, DEFAULT_CUES)
    helpers_ns.write_plan_form_a(plan, DEFAULT_PLAN)

    job = make_job(helpers_ns, srt, plan, tmp_path,
                   source=synth_v_only, bg_volume=0.5)
    qc = helpers_ns.sde.run_job(job, ffmpeg_version)

    captured = capsys.readouterr()
    assert "no audio track" in captured.out, \
        "expected a WARNING about source having no audio"
    assert qc["ok"] is True


# ---------------------------------------------------------------------------
# 6. Source range out of bounds → SystemExit before extraction
# ---------------------------------------------------------------------------


def test_range_out_of_bounds_fails_fast(
    helpers_ns, ffmpeg_version, synth_av, tmp_path
):
    srt = tmp_path / "script.srt"
    plan = tmp_path / "plan.json"
    helpers_ns.write_srt(srt, DEFAULT_CUES)
    # source is 30s, but ask for 0:50 — way over
    helpers_ns.write_plan_form_a(plan, [
        (1, 1.0, 3.0),
        (2, 5.0, 7.5),
        (3, 50.0, 52.5),  # bad
    ])

    job = make_job(helpers_ns, srt, plan, tmp_path, source=synth_av)
    with pytest.raises(SystemExit) as exc:
        helpers_ns.sde.run_job(job, ffmpeg_version)
    assert "exceeds source" in str(exc.value)
    # And the failure happened pre-extract, so no out.mp4
    assert not (tmp_path / "out.mp4").exists()


# ---------------------------------------------------------------------------
# 7. Second run hits cache for every segment
# ---------------------------------------------------------------------------


def test_cache_hit_on_rerun(helpers_ns, ffmpeg_version, synth_av, tmp_path):
    srt = tmp_path / "script.srt"
    plan = tmp_path / "plan.json"
    helpers_ns.write_srt(srt, DEFAULT_CUES)
    helpers_ns.write_plan_form_a(plan, DEFAULT_PLAN)

    job = make_job(helpers_ns, srt, plan, tmp_path, source=synth_av)
    qc1 = helpers_ns.sde.run_job(job, ffmpeg_version)
    qc2 = helpers_ns.sde.run_job(job, ffmpeg_version)

    assert all(s["cached"] is False for s in qc1["segments"])
    assert all(s["cached"] is True for s in qc2["segments"])
    # Cache hits should be measurably faster
    assert qc2["elapsed_s"] <= qc1["elapsed_s"]


# ---------------------------------------------------------------------------
# 8. --no-overwrite refuses to clobber existing output
# ---------------------------------------------------------------------------


def test_no_overwrite_refuses(helpers_ns, ffmpeg_version, synth_av, tmp_path):
    srt = tmp_path / "script.srt"
    plan = tmp_path / "plan.json"
    helpers_ns.write_srt(srt, DEFAULT_CUES)
    helpers_ns.write_plan_form_a(plan, DEFAULT_PLAN)

    job1 = make_job(helpers_ns, srt, plan, tmp_path, source=synth_av)
    helpers_ns.sde.run_job(job1, ffmpeg_version)

    job2 = make_job(helpers_ns, srt, plan, tmp_path,
                    source=synth_av, no_overwrite=True)
    with pytest.raises(SystemExit) as exc:
        helpers_ns.sde.run_job(job2, ffmpeg_version)
    assert "no-overwrite" in str(exc.value)


# ---------------------------------------------------------------------------
# 9. SRT gap → output duration includes the gap as black+silent
# ---------------------------------------------------------------------------


def test_global_voice_spans_timeline(
    helpers_ns, ffmpeg_version, synth_av, synth_voice, tmp_path
):
    """Global --voice must span the WHOLE output timeline, not restart per segment.

    Regression: earlier implementation expanded --voice into a synthetic
    per-segment voice on every entry, which made each segment apad/atrim
    voice.wav from t=0 — so a 5s voice would replay at every cut. The fix
    moves global-voice mixing into the final compose step where voice is
    apad'd / atrim'd to total_duration once.
    """
    srt = tmp_path / "script.srt"
    plan = tmp_path / "plan.json"
    helpers_ns.write_srt(srt, DEFAULT_CUES)  # total 8.5s
    helpers_ns.write_plan_form_a(plan, DEFAULT_PLAN)

    job = make_job(helpers_ns, srt, plan, tmp_path,
                   source=synth_av, voice=synth_voice)
    qc = helpers_ns.sde.run_job(job, ffmpeg_version)

    assert qc["ok"] is True
    assert qc["audio"]["voice_used"] is True
    assert qc["audio"]["mode"] == "voice_replace"
    assert qc["audio"]["bg_volume"] == 0.0
    # Per-segment voice slot must be None — proves we are NOT smuggling the
    # global voice in via the per-segment expansion hack.
    assert all(s["voice"] is None for s in qc["segments"])

    # Output duration matches SRT total (voice apad'd from 5s → 8.5s)
    actual = helpers_ns.sde.probe_duration(tmp_path / "out.mp4")
    assert abs(actual - 8.5) < 0.25, f"actual {actual}s vs expected 8.5s"


def test_global_voice_with_bg_volume_mix(
    helpers_ns, ffmpeg_version, synth_av, synth_voice, tmp_path
):
    """With bg_volume>0 and global voice, base audio (source*bg) is mixed
    under voice. The bg_volume is applied ONCE at extract; the final compose
    must not re-scale it.
    """
    srt = tmp_path / "script.srt"
    plan = tmp_path / "plan.json"
    helpers_ns.write_srt(srt, DEFAULT_CUES)
    helpers_ns.write_plan_form_a(plan, DEFAULT_PLAN)

    job = make_job(helpers_ns, srt, plan, tmp_path,
                   source=synth_av, voice=synth_voice, bg_volume=0.1)
    qc = helpers_ns.sde.run_job(job, ffmpeg_version)

    assert qc["ok"] is True
    assert qc["audio"]["mode"] == "voice_mix"
    assert qc["audio"]["bg_volume"] == 0.1


def test_global_voice_cache_independence(
    helpers_ns, ffmpeg_version, synth_av, synth_voice, tmp_path
):
    """Segment cache must NOT depend on the global voice file. Running once
    without voice then again with voice should reuse all segment caches —
    voice gets mixed in the final pass, segments are identical.
    """
    srt = tmp_path / "script.srt"
    plan = tmp_path / "plan.json"
    helpers_ns.write_srt(srt, DEFAULT_CUES)
    helpers_ns.write_plan_form_a(plan, DEFAULT_PLAN)

    job_no_voice = make_job(helpers_ns, srt, plan, tmp_path, source=synth_av)
    qc1 = helpers_ns.sde.run_job(job_no_voice, ffmpeg_version)

    job_with_voice = make_job(
        helpers_ns, srt, plan, tmp_path,
        source=synth_av, voice=synth_voice,
        output=tmp_path / "out_voiced.mp4",
    )
    qc2 = helpers_ns.sde.run_job(job_with_voice, ffmpeg_version)

    assert all(s["cached"] is False for s in qc1["segments"]), \
        "first run should not have cache hits"
    assert all(s["cached"] is True for s in qc2["segments"]), \
        "second run with global voice should hit segment cache — voice is " \
        "mixed in the final pass, not baked into segments"


def test_gap_inserted_in_output(helpers_ns, ffmpeg_version, synth_av, tmp_path):
    srt = tmp_path / "script.srt"
    plan = tmp_path / "plan.json"
    # 2 cues with a 1.5s gap between them: total output = 2 + 1.5 + 2.5 = 6.0s
    cues = [
        (1, 0.0, 2.0, "first"),
        (2, 3.5, 6.0, "second after gap"),
    ]
    helpers_ns.write_srt(srt, cues)
    helpers_ns.write_plan_form_a(plan, [(1, 1.0, 3.0), (2, 5.0, 7.5)])

    job = make_job(helpers_ns, srt, plan, tmp_path, source=synth_av)
    qc = helpers_ns.sde.run_job(job, ffmpeg_version)

    assert qc["ok"] is True
    assert qc["duration"]["expected_s"] == 6.0
    assert abs(qc["duration"]["drift_ms"]) <= 200
    # ffprobe the actual output to double-check
    actual = helpers_ns.sde.probe_duration(tmp_path / "out.mp4")
    assert abs(actual - 6.0) < 0.25, f"actual {actual}s, expected 6.0s"
