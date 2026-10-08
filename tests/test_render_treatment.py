"""test render treatment support for video use"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from helpers import render


# test composite orders treatment overlays then raster captions
def test_composite_orders_treatment_overlays_then_raster_captions(tmp_path: Path) -> None:
    base = tmp_path / "base.mp4"
    overlay = tmp_path / "overlay.png"
    subtitles = tmp_path / "master.srt"
    caption_track = tmp_path / "captions.ffconcat"
    for path in (base, overlay, subtitles, caption_track):
        path.write_text("fixture", encoding="utf-8")

    with (
        patch.object(
            render,
            "probe_video",
            return_value={"width": 1920, "height": 1080, "fps": "30/1", "duration": 2.0},
        ),
        patch.object(render, "choose_caption_renderer", return_value="pil"),
        patch.object(render, "build_caption_track", return_value=caption_track),
        patch.object(render.subprocess, "run") as run,
    ):
        render.build_final_composite(
            base,
            [{"file": str(overlay), "kind": "image", "start_in_output": 0, "duration": 2}],
            subtitles,
            tmp_path / "final.mp4",
            tmp_path,
            treatment={"canvas": {"width": 1080, "height": 1920, "fit": "cover"}},
            caption_config={"renderer": "pil"},
        )

    command = run.call_args.args[0]
    graph = command[command.index("-filter_complex") + 1]
    assert graph.index("crop=1080:1920") < graph.index("[1:v]format=rgba")
    assert graph.index("[1:v]format=rgba") < graph.index("[2:v]format=rgba")
    assert graph.endswith("[v1][raster_subs]overlay=eof_action=pass:repeatlast=0[outv]")
    assert command[-3:] == ["-t", "2.000000", str(tmp_path / "final.mp4")]


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg and ffprobe are required for the synthetic render",
)


# test synthetic render applies treatment graphics and pil captions
def test_synthetic_render_applies_treatment_graphics_and_pil_captions(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "1.2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source),
        ],
        check=True,
        timeout=60,
    )
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    (transcripts / "source.json").write_text(
        json.dumps(
            {
                "words": [
                    {"type": "word", "text": "clear", "start": 0.1, "end": 0.4},
                    {"type": "word", "text": "idea", "start": 0.5, "end": 0.9},
                ]
            }
        ),
        encoding="utf-8",
    )
    edl = {
        "version": 1,
        "sources": {"source": str(source)},
        "ranges": [{"source": "source", "start": 0.0, "end": 1.1, "reframe": {"zoom": 1.03}}],
        "treatment": {
            "canvas": {
                "width": 180,
                "height": 320,
                "fit": "blur",
                "foreground": {"width": 180, "height": 102, "fit": "cover", "y": 90},
            }
        },
        "captions": {
            "renderer": "pil",
            "max_words": 2,
            "case": "upper",
            "font_size": 24,
            "y": 0.75,
        },
        "graphics": [
            {
                "type": "text",
                "text": "TEST TITLE",
                "start": 0,
                "duration": 1.1,
                "x": 0.5,
                "y": 0.12,
                "font_size": 22,
                "max_width": 0.9,
            }
        ],
        "subtitles": "master.srt",
        "total_duration_s": 1.1,
    }
    edl_path = tmp_path / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    output = tmp_path / "final.mp4"
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "helpers" / "render.py"),
            str(edl_path),
            "-o", str(output),
            "--build-subtitles",
            "--no-loudnorm",
            "--fps", "30",
        ],
        check=True,
        timeout=120,
    )
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=codec_type,codec_name,width,height,avg_frame_rate",
            "-of", "json", str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    streams = json.loads(probe.stdout)["streams"]
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    audio = next(stream for stream in streams if stream["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (180, 320)
    assert video["codec_name"] == "h264"
    assert video["avg_frame_rate"] == "30/1"
    assert audio["codec_name"] == "aac"
    assert (tmp_path / "overlays" / "generated" / "graphic_000.png").is_file()
    assert (tmp_path / "overlays" / "captions" / "caption_0001.png").is_file()


# test preview render scales full vertical treatment to 720p
def test_preview_render_scales_full_vertical_treatment_to_720p(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "0.4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source),
        ],
        check=True,
        timeout=60,
    )
    edl = {
        "version": 1,
        "sources": {"source": str(source)},
        "ranges": [{"source": "source", "start": 0.0, "end": 0.3}],
        "treatment": {
            "canvas": {
                "width": 1080,
                "height": 1920,
                "fit": "blur",
                "blur": 30,
                "foreground": {
                    "width": 1080,
                    "height": 608,
                    "fit": "cover",
                    "y": 656,
                },
            }
        },
    }
    edl_path = tmp_path / "edl.json"
    edl_path.write_text(json.dumps(edl), encoding="utf-8")
    output = tmp_path / "preview.mp4"
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "helpers" / "render.py"),
            str(edl_path),
            "-o", str(output),
            "--preview",
            "--no-subtitles",
            "--no-loudnorm",
            "--fps", "30",
        ],
        check=True,
        timeout=120,
    )
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert probe.stdout.strip() == "720,1280"
