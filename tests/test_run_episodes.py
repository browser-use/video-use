"""Tests for the multi-episode batch runner."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture
def runner():
    import run_episodes
    return run_episodes


@pytest.fixture
def ffmpeg_version(helpers_ns):
    return helpers_ns.sde.preflight()["ffmpeg"]


def _make_ep(ep_dir: Path, source: Path, helpers_ns, *,
             cues=None, plan=None, voice: Path | None = None) -> None:
    ep_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, ep_dir / "source.mp4")
    helpers_ns.write_srt(ep_dir / "script.srt", cues or [
        (1, 0.0, 1.5, "alpha"),
        (2, 1.5, 3.0, "beta"),
    ])
    helpers_ns.write_plan_form_a(ep_dir / "edit_plan.json", plan or [
        (1, 1.0, 2.5),
        (2, 5.0, 6.5),
    ])
    if voice is not None:
        shutil.copy2(voice, ep_dir / "voice.wav")


# ---------------------------------------------------------------------------
# 1. Discovery: pick up complete dirs, skip incomplete ones
# ---------------------------------------------------------------------------


def test_discover_skips_incomplete_dirs(runner, helpers_ns, synth_av, tmp_path):
    batch = tmp_path / "batch"
    _make_ep(batch / "ep01", synth_av, helpers_ns)
    _make_ep(batch / "ep02", synth_av, helpers_ns)
    # incomplete: missing edit_plan.json
    bad = batch / "ep03"
    bad.mkdir(parents=True)
    shutil.copy2(synth_av, bad / "source.mp4")
    helpers_ns.write_srt(bad / "script.srt", [(1, 0.0, 1.5, "x")])
    # not a dir at all
    (batch / "stray.txt").write_text("ignore me", encoding="utf-8")

    eps = runner.discover_episodes(batch)
    names = [e.name for e in eps]
    assert names == ["ep01", "ep02"]


def test_discover_sees_voice_wav_if_present(
    runner, helpers_ns, synth_av, synth_voice, tmp_path
):
    batch = tmp_path / "batch"
    _make_ep(batch / "ep01", synth_av, helpers_ns)
    _make_ep(batch / "ep02", synth_av, helpers_ns, voice=synth_voice)

    eps = runner.discover_episodes(batch)
    by_name = {e.name: e for e in eps}
    assert by_name["ep01"].voice is None
    assert by_name["ep02"].voice is not None and by_name["ep02"].voice.is_file()


def test_discover_hard_fails_on_empty_root(runner, tmp_path):
    batch = tmp_path / "empty"
    batch.mkdir()
    with pytest.raises(SystemExit) as exc:
        runner.discover_episodes(batch)
    assert "no usable" in str(exc.value)


# ---------------------------------------------------------------------------
# 2. End-to-end: 3 eps run sequentially, each produces final.mp4
# ---------------------------------------------------------------------------


def test_run_episodes_e2e(runner, helpers_ns, ffmpeg_version, synth_av, tmp_path):
    batch = tmp_path / "batch"
    for name in ("ep01", "ep02", "ep03"):
        _make_ep(batch / name, synth_av, helpers_ns)

    summary = runner.run_episodes(batch, ffmpeg_version=ffmpeg_version)

    assert summary["episodes_total"] == 3
    assert summary["ok"] == 3
    for name in ("ep01", "ep02", "ep03"):
        final = batch / name / "final.mp4"
        assert final.exists(), f"{name}/final.mp4 missing"

    # Summary artifact
    summary_file = batch / "run_episodes_summary.json"
    assert summary_file.exists()


# ---------------------------------------------------------------------------
# 3. continue-on-error skips a broken ep, finishes the rest
# ---------------------------------------------------------------------------


def test_run_episodes_continue_on_error(
    runner, helpers_ns, ffmpeg_version, synth_av, tmp_path
):
    batch = tmp_path / "batch"
    _make_ep(batch / "ep01", synth_av, helpers_ns)
    # ep02: range exceeds the synth source (30s) — pre-extract range check fires
    _make_ep(batch / "ep02", synth_av, helpers_ns,
             plan=[(1, 1.0, 2.5), (2, 60.0, 61.5)])
    _make_ep(batch / "ep03", synth_av, helpers_ns)

    summary = runner.run_episodes(
        batch, ffmpeg_version=ffmpeg_version,
        continue_on_error=True,
    )
    assert summary["episodes_total"] == 3
    assert summary["ok"] == 2
    # ep01 + ep03 produced output, ep02 did not
    assert (batch / "ep01" / "final.mp4").exists()
    assert not (batch / "ep02" / "final.mp4").exists()
    assert (batch / "ep03" / "final.mp4").exists()


def test_run_episodes_aborts_without_continue_on_error(
    runner, helpers_ns, ffmpeg_version, synth_av, tmp_path
):
    batch = tmp_path / "batch"
    _make_ep(batch / "ep01", synth_av, helpers_ns)
    _make_ep(batch / "ep02", synth_av, helpers_ns,
             plan=[(1, 1.0, 2.5), (2, 60.0, 61.5)])  # bad
    _make_ep(batch / "ep03", synth_av, helpers_ns)

    with pytest.raises(SystemExit):
        runner.run_episodes(batch, ffmpeg_version=ffmpeg_version)
    # ep03 was never reached
    assert not (batch / "ep03" / "final.mp4").exists()


# ---------------------------------------------------------------------------
# 4. Per-ep voice.wav becomes a global voice for that ep
# ---------------------------------------------------------------------------


def test_run_episodes_failure_record_includes_paths(
    runner, helpers_ns, ffmpeg_version, synth_av, tmp_path
):
    """When --continue-on-error skips an ep, the record must carry enough
    context to triage without re-reading the terminal."""
    batch = tmp_path / "batch"
    _make_ep(batch / "ep01", synth_av, helpers_ns)
    _make_ep(batch / "ep02", synth_av, helpers_ns,
             plan=[(1, 1.0, 2.5), (2, 60.0, 61.5)])  # range overruns 30s synth
    _make_ep(batch / "ep03", synth_av, helpers_ns)

    summary = runner.run_episodes(
        batch, ffmpeg_version=ffmpeg_version,
        continue_on_error=True,
    )
    failed = [r for r in summary["results"] if not r.get("ok")]
    assert len(failed) == 1
    rec = failed[0]
    assert rec["job"] == "ep02"
    assert rec["index"] == 1
    assert rec["srt"].endswith("script.srt")
    assert rec["plan"].endswith("edit_plan.json")
    assert rec["source"].endswith("source.mp4")
    assert rec["output"].endswith("final.mp4")
    assert rec["error"]
    # Pre-extract range-bounds check → no ffmpeg → empty stderr
    assert rec["stderr_tail"] == ""


def test_run_episodes_continues_past_corrupt_plan_json(
    runner, helpers_ns, ffmpeg_version, synth_av, tmp_path
):
    """A non-SystemExit (JSONDecodeError) inside run_job must NOT abort
    --continue-on-error. Pre-fix the loop only caught SystemExit, so a
    malformed edit_plan.json in one ep would crash the whole batch."""
    batch = tmp_path / "batch"
    _make_ep(batch / "ep01", synth_av, helpers_ns)
    # ep02: valid SRT + source, but plan.json is garbage
    ep02 = batch / "ep02"
    ep02.mkdir()
    import shutil
    shutil.copy2(synth_av, ep02 / "source.mp4")
    helpers_ns.write_srt(ep02 / "script.srt", [(1, 0.0, 1.5, "x")])
    (ep02 / "edit_plan.json").write_text("{ not json", encoding="utf-8")
    # ep03 should still run
    _make_ep(batch / "ep03", synth_av, helpers_ns)

    summary = runner.run_episodes(
        batch, ffmpeg_version=ffmpeg_version,
        continue_on_error=True,
    )
    assert summary["episodes_total"] == 3
    assert summary["ok"] == 2

    failed = [r for r in summary["results"] if not r.get("ok")]
    assert len(failed) == 1 and failed[0]["job"] == "ep02"
    assert "JSON" in failed[0]["error"] or "json" in failed[0]["error"]
    # ep03 (the post-bad one) must have run
    assert (batch / "ep03" / "final.mp4").exists()


def test_run_episodes_extract_mode(
    runner, helpers_ns, ffmpeg_version, synth_av, tmp_path
):
    """--mode extract across multiple eps: each ep produces clip_*.mp4 in its
    own edit/extracted_clips_<ep>/ and NOT a final.mp4."""
    batch = tmp_path / "batch"
    for name in ("ep01", "ep02"):
        _make_ep(batch / name, synth_av, helpers_ns)

    summary = runner.run_episodes(
        batch, ffmpeg_version=ffmpeg_version, mode="extract",
    )

    assert summary["episodes_total"] == 2
    assert summary["ok"] == 2
    for r in summary["results"]:
        assert r["mode"] == "extract"
        assert r["clip_count"] == 2  # CUES_2 has 2 cues
        extracted_dir = Path(r["extracted_dir"])
        assert extracted_dir.is_dir()
        assert (extracted_dir / "clip_001.mp4").exists()
        assert (extracted_dir / "clip_002.mp4").exists()
    # No final.mp4 in any ep dir
    for name in ("ep01", "ep02"):
        assert not (batch / name / "final.mp4").exists()


def test_run_episodes_per_ep_voice(
    runner, helpers_ns, ffmpeg_version, synth_av, synth_voice, tmp_path
):
    batch = tmp_path / "batch"
    _make_ep(batch / "ep01", synth_av, helpers_ns)
    _make_ep(batch / "ep02", synth_av, helpers_ns, voice=synth_voice)

    summary = runner.run_episodes(batch, ffmpeg_version=ffmpeg_version)

    by_name = {r["job"]: r for r in summary["results"]}
    assert by_name["ep01"]["audio"]["voice_used"] is False
    assert by_name["ep02"]["audio"]["voice_used"] is True
    assert by_name["ep02"]["audio"]["mode"] == "voice_replace"
