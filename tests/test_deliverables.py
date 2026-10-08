"""tests for deliverable normalization and the reframing and loudness parts of the renderer
they check alias handling tracked reframe validation and the ffmpeg commands built for deliveries
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from helpers.edl import EDLValidationError, normalize_deliverables, validate_edl
from helpers import render


# build a minimal version one edl pointing at the given source path
def _ready_edl(source: Path) -> dict:
    return {
        "version": 1,
        "sources": {"source": str(source)},
        "ranges": [{"source": "source", "start": 0, "end": 5}],
    }


# a blocked edl reports every problem at once before any render
def test_blocked_edl_is_rejected_before_render(tmp_path: Path) -> None:
    edl = {
        "status": "blocked_missing_source_media",
        "sources": {},
        "ranges": [],
        "handoff": {
            "render_ready": False,
            "blocking_reason": "stage one source video",
        },
    }

    with pytest.raises(EDLValidationError) as error:
        validate_edl(edl, tmp_path)

    message = str(error.value)
    assert "blocked_missing_source_media" in message
    assert "stage one source video" in message
    assert "at least one source video" in message
    assert "at least one playable edit range" in message


# agent style aliases for mode tracking asset and loudness normalize to the public names
def test_agent_authored_delivery_aliases_normalize_to_public_contract(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    track = tmp_path / "reframe_tracks.json"
    track.write_text(
        json.dumps(
            {
                "tracks": {
                    "social_9x16": [
                        {"time": 0, "center_x": 0.3, "center_y": 0.5},
                        {"time": 5, "center_x": 0.7, "center_y": 0.5},
                    ]
                }
            }
        )
    )
    edl = {
        **_ready_edl(source),
        "reframe": {
            "mode": "tracked_subject",
            "tracking_asset": "reframe_tracks.json",
        },
        "deliverables": [
            {
                "id": "social_9x16",
                "file": "deliverables/social.mp4",
                "width": 1080,
                "height": 1920,
                "fps": 30,
                "reframe_track": "social_9x16",
                "audio": {
                    "integrated_loudness_lufs": -16,
                    "true_peak_dbtp": -1,
                    "loudness_range_lu": 9,
                },
            }
        ],
    }

    validate_edl(edl, tmp_path)
    delivery = normalize_deliverables(edl, tmp_path)[0]

    assert delivery["fps"] == "30/1"
    assert delivery["reframe"] == {
        "mode": "track",
        "tracking_asset": "reframe_tracks.json",
        "track_id": "social_9x16",
        "track_file": "reframe_tracks.json",
        "interpolation": "linear",
    }
    assert delivery["loudness"] == {
        "integrated_lufs": -16.0,
        "true_peak_dbtp": -1.0,
        "lra": 9.0,
    }


# track mode without keyframes or a track file is rejected
def test_tracked_delivery_without_tracking_data_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    edl = {
        **_ready_edl(source),
        "deliverables": [
            {
                "id": "vertical",
                "resolution": "1080x1920",
                "reframe": {"mode": "track"},
            }
        ],
    }

    with pytest.raises(EDLValidationError, match="has no keyframes or track file"):
        validate_edl(edl, tmp_path)


# a named track that exists but is empty is rejected
def test_empty_named_track_is_rejected_before_render(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    (tmp_path / "tracks.json").write_text(
        json.dumps({"tracks": {"vertical": []}})
    )
    edl = {
        **_ready_edl(source),
        "reframe": {"mode": "track", "track_file": "tracks.json"},
        "deliverables": [
            {
                "id": "vertical",
                "width": 1080,
                "height": 1920,
                "reframe_track": "vertical",
            }
        ],
    }

    with pytest.raises(EDLValidationError, match="keyframes 'vertical' are empty"):
        validate_edl(edl, tmp_path)


# subject boxes in a named track are converted to center points
def test_named_track_supports_subject_boxes(tmp_path: Path) -> None:
    track = tmp_path / "track.json"
    track.write_text(
        json.dumps(
            {
                "tracks": {
                    "vertical": [
                        {
                            "time": 0,
                            "subject_box": {
                                "x": 0.2,
                                "y": 0.1,
                                "width": 0.2,
                                "height": 0.6,
                            },
                        },
                        {"time": 2, "center": [0.7, 0.5]},
                    ]
                }
            }
        )
    )

    keyframes = render._load_track_keyframes(
        {"mode": "track", "track_file": "track.json", "track_id": "vertical"},
        tmp_path,
    )

    assert keyframes == [
        {"time": 0.0, "center_x": pytest.approx(0.3), "center_y": pytest.approx(0.4)},
        {"time": 2.0, "center_x": 0.7, "center_y": 0.5},
    ]


# a codec other than h264 raises instead of being silently replaced
def test_unsupported_delivery_codec_is_rejected_instead_of_ignored(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    edl = {
        **_ready_edl(source),
        "deliverables": [
            {
                "id": "archive",
                "width": 1920,
                "height": 1080,
                "video_codec": "prores",
            }
        ],
    }

    with pytest.raises(EDLValidationError, match="supports only H.264"):
        validate_edl(edl, tmp_path)


# the reframe command crops to the target ratio and uses a time based expression and the requested fps
def test_reframe_command_uses_dynamic_track_and_requested_format(tmp_path: Path) -> None:
    output = tmp_path / "vertical.mp4"
    completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    with (
        patch.object(render, "probe_video_size", return_value=(1920, 1080)),
        patch.object(render.subprocess, "run", return_value=completed) as run,
    ):
        render.build_reframed_base(
            tmp_path / "base.mp4",
            output,
            width=1080,
            height=1920,
            fps="30/1",
            reframe={
                "mode": "track",
                "keyframes": [
                    {"time": 0, "center_x": 0.25, "center_y": 0.5},
                    {"time": 2, "center_x": 0.75, "center_y": 0.5},
                ],
            },
            edit_dir=tmp_path,
        )

    command = run.call_args.args[0]
    video_filter = command[command.index("-vf") + 1]
    assert "crop=606:1080" in video_filter
    assert "if(lt(t,2.00000000)" in video_filter
    assert "scale=1080:1920" in video_filter
    assert "fps=30/1" in video_filter


# smooth interpolation produces a smoothstep expression with per segment guards
def test_smooth_tracking_uses_eased_interpolation() -> None:
    expression = render._piecewise_track_expression(
        [
            {"time": 0, "center_x": 0.2},
            {"time": 2, "center_x": 0.8},
        ],
        "center_x",
        "smooth",
    )

    assert "3-2*" in expression
    assert "if(lt(t,2.00000000)" in expression


# the per delivery loudness targets reach both the measurement and the second pass filter
def test_loudnorm_passes_per_delivery_targets_to_measurement(tmp_path: Path) -> None:
    measurement = {
        "input_i": "-20.0",
        "input_tp": "-3.0",
        "input_lra": "5.0",
        "input_thresh": "-30.0",
        "target_offset": "0.0",
    }
    completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    with (
        patch.object(render, "measure_loudness", return_value=measurement) as measure,
        patch.object(render.subprocess, "run", return_value=completed) as run,
    ):
        render.apply_loudnorm_two_pass(
            tmp_path / "input.mp4",
            tmp_path / "output.mp4",
            integrated_lufs=-24,
            true_peak_dbtp=-2,
            lra=7,
        )

    measure.assert_called_once_with(
        tmp_path / "input.mp4",
        integrated_lufs=-24,
        true_peak_dbtp=-2,
        lra=7,
    )
    filter_value = run.call_args.args[0][run.call_args.args[0].index("-af") + 1]
    assert "I=-24" in filter_value
    assert "TP=-2" in filter_value
    assert "LRA=7" in filter_value
