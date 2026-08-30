"""Batch-manifest tests for srt_driven_edit.

Exercises load_manifest + job_from_dict + run_job in the loop pattern that
the CLI uses, without depending on argv parsing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Common cue/plan helpers used across batch jobs
# ---------------------------------------------------------------------------

CUES_2 = [
    (1, 0.0, 2.0, "alpha"),
    (2, 2.0, 4.0, "beta"),
]

PLAN_2 = [
    (1, 1.0, 3.0),
    (2, 5.0, 7.0),
]


def default_args_namespace() -> argparse.Namespace:
    """Build the defaults Namespace job_from_dict expects."""
    return argparse.Namespace(
        bg_volume=0.0,
        tolerance=0.5,
        trim_direction="tail",
        on_short="error",
        style="auto",
        no_cache=False,
        keep_intermediates=False,
        no_overwrite=False,
    )


def run_batch(helpers_ns, manifest_path, ffmpeg_version, *,
              continue_on_error: bool = False) -> list[dict]:
    """Mirror the CLI's batch loop so we can unit-test it."""
    sde = helpers_ns.sde
    defaults = default_args_namespace()
    rows = sde.load_manifest(manifest_path)
    results: list[dict] = []
    for i, row in enumerate(rows):
        try:
            job = sde.job_from_dict(row, defaults, manifest_path.parent, i)
        except SystemExit as e:
            if continue_on_error:
                results.append({
                    "job": row.get("name", f"row{i}"),
                    "ok": False,
                    "error": str(e),
                })
                continue
            raise
        try:
            results.append(sde.run_job(job, ffmpeg_version))
        except SystemExit as e:
            if continue_on_error:
                results.append({"job": job.name, "ok": False, "error": str(e)})
                continue
            raise
    return results


@pytest.fixture
def ffmpeg_version(helpers_ns) -> str:
    return helpers_ns.sde.preflight()["ffmpeg"]


# ---------------------------------------------------------------------------
# 1. Two jobs same name, no output specified → auto-isolated outputs
# ---------------------------------------------------------------------------


def test_batch_auto_isolation(helpers_ns, ffmpeg_version, synth_av, tmp_path):
    # Two SRTs / plans with distinct content but identical job name
    for i in range(2):
        srt = tmp_path / f"script_{i}.srt"
        plan = tmp_path / f"plan_{i}.json"
        helpers_ns.write_srt(srt, CUES_2)
        helpers_ns.write_plan_form_a(plan, PLAN_2)

    manifest_path = tmp_path / "jobs.json"
    manifest_path.write_text(json.dumps([
        {"name": "promo",  # same name on purpose
         "source": str(synth_av),
         "srt": "script_0.srt",
         "plan": "plan_0.json"},
        {"name": "promo",  # collision
         "source": str(synth_av),
         "srt": "script_1.srt",
         "plan": "plan_1.json"},
    ]), encoding="utf-8")

    results = run_batch(helpers_ns, manifest_path, ffmpeg_version)
    assert len(results) == 2
    assert all(r["ok"] for r in results)

    out_paths = [Path(r["output_path"]) for r in results]
    # auto-isolated → distinct
    assert out_paths[0] != out_paths[1]
    # Names should contain the index suffix
    assert "_00" in out_paths[0].name
    assert "_01" in out_paths[1].name
    for p in out_paths:
        assert p.exists()


# ---------------------------------------------------------------------------
# 2. continue-on-error skips a malformed row, finishes the rest
# ---------------------------------------------------------------------------


def test_batch_continue_on_error(helpers_ns, ffmpeg_version, synth_av, tmp_path):
    # Three jobs: 0 ok, 1 has a missing 'plan' field, 2 ok
    for i in (0, 2):
        helpers_ns.write_srt(tmp_path / f"s{i}.srt", CUES_2)
        helpers_ns.write_plan_form_a(tmp_path / f"p{i}.json", PLAN_2)

    manifest_path = tmp_path / "jobs.json"
    manifest_path.write_text(json.dumps([
        {"name": "ok0", "source": str(synth_av),
         "srt": "s0.srt", "plan": "p0.json"},
        {"name": "broken", "source": str(synth_av),
         "srt": "s_missing.srt"},  # no plan, srt also missing
        {"name": "ok2", "source": str(synth_av),
         "srt": "s2.srt", "plan": "p2.json"},
    ]), encoding="utf-8")

    results = run_batch(helpers_ns, manifest_path, ffmpeg_version,
                        continue_on_error=True)
    assert len(results) == 3
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False and "error" in results[1]
    assert results[2]["ok"] is True


def test_batch_aborts_without_continue_on_error(
    helpers_ns, ffmpeg_version, synth_av, tmp_path
):
    helpers_ns.write_srt(tmp_path / "s0.srt", CUES_2)
    helpers_ns.write_plan_form_a(tmp_path / "p0.json", PLAN_2)

    manifest_path = tmp_path / "jobs.json"
    manifest_path.write_text(json.dumps([
        {"name": "ok0", "source": str(synth_av),
         "srt": "s0.srt", "plan": "p0.json"},
        {"name": "broken", "source": str(synth_av),
         "srt": "s_missing.srt"},  # no plan
    ]), encoding="utf-8")

    with pytest.raises(SystemExit):
        run_batch(helpers_ns, manifest_path, ffmpeg_version,
                  continue_on_error=False)


# ---------------------------------------------------------------------------
# 3. CSV manifest is supported
# ---------------------------------------------------------------------------


def test_batch_csv_manifest(helpers_ns, ffmpeg_version, synth_av, tmp_path):
    helpers_ns.write_srt(tmp_path / "s.srt", CUES_2)
    helpers_ns.write_plan_form_a(tmp_path / "p.json", PLAN_2)

    manifest = tmp_path / "jobs.csv"
    manifest.write_text(
        "name,source,srt,plan,bg_volume\n"
        f"promo,{synth_av},s.srt,p.json,0.0\n",
        encoding="utf-8",
    )
    results = run_batch(helpers_ns, manifest, ffmpeg_version)
    assert len(results) == 1 and results[0]["ok"] is True


# ---------------------------------------------------------------------------
# 4. Different bg_volume per job is honored (cache must NOT collide)
# ---------------------------------------------------------------------------


def test_batch_per_job_bg_volume(helpers_ns, ffmpeg_version, synth_av, tmp_path):
    helpers_ns.write_srt(tmp_path / "s.srt", CUES_2)
    helpers_ns.write_plan_form_a(tmp_path / "p.json", PLAN_2)

    manifest = tmp_path / "jobs.json"
    manifest.write_text(json.dumps([
        {"name": "silent", "source": str(synth_av),
         "srt": "s.srt", "plan": "p.json", "bg_volume": 0.0},
        {"name": "bg10", "source": str(synth_av),
         "srt": "s.srt", "plan": "p.json", "bg_volume": 0.1},
    ]), encoding="utf-8")

    results = run_batch(helpers_ns, manifest, ffmpeg_version)
    assert len(results) == 2 and all(r["ok"] for r in results)
    assert results[0]["audio"]["mode"] == "silent"
    assert results[1]["audio"]["mode"] == "original_only"
    # bg10 should NOT have hit cache from silent (different effective_bg → different key)
    assert all(s["cached"] is False for s in results[1]["segments"])
