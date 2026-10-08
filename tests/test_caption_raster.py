"""test captions support for video use"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from helpers import caption_raster as captions


# word
def word(text: str, start: float, end: float) -> dict:
    return {"type": "word", "text": text, "start": start, "end": end}


# test chunk words honors punctuation and limit
def test_chunk_words_honors_punctuation_and_limit() -> None:
    words = [
        word("One", 0.0, 0.2),
        word("idea.", 0.2, 0.5),
        word("Then", 0.6, 0.8),
        word("another", 0.8, 1.0),
        word("one", 1.0, 1.2),
    ]
    chunks = captions.chunk_words(words, max_words=3, break_on_punctuation=True)
    assert [[item["text"] for item in chunk] for chunk in chunks] == [
        ["One", "idea."],
        ["Then", "another", "one"],
    ]


# test build master srt uses edl caption style and output offsets
def test_build_master_srt_uses_edl_caption_style_and_output_offsets(
    tmp_path: Path,
) -> None:
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    transcript = {
        "words": [
            word("first", 10.0, 10.3),
            word("thought.", 10.4, 10.8),
            word("second", 20.0, 20.3),
            word("thought", 20.4, 20.8),
        ]
    }
    (transcripts / "source.json").write_text(json.dumps(transcript), encoding="utf-8")
    edl = {
        "sources": {"source": "source.mp4"},
        "ranges": [
            {"source": "source", "start": 10.0, "end": 11.0},
            {"source": "source", "start": 20.0, "end": 21.0},
        ],
        "captions": {"max_words": 4, "case": "natural"},
    }
    output = tmp_path / "master.srt"
    cues = captions.build_master_srt(edl, tmp_path, output)

    assert [cue.text for cue in cues] == ["first thought.", "second thought"]
    assert cues[0].start == 0.0
    assert cues[1].start == 1.0
    assert "00:00:01,000 --> 00:00:01,800" in output.read_text(encoding="utf-8")


# test render caption image is transparent outside text
def test_render_caption_image_is_transparent_outside_text(tmp_path: Path) -> None:
    output = tmp_path / "caption.png"
    captions.render_caption_image(
        captions.CaptionCue(0.0, 1.0, "Readable caption"),
        output,
        width=640,
        height=360,
        config={"font_size": 36, "max_width": 0.8, "y": 0.7},
    )
    image = Image.open(output).convert("RGBA")
    assert image.size == (640, 360)
    assert image.getpixel((0, 0))[3] == 0
    assert image.getbbox() is not None


# test caption render never silently drops text
def test_caption_render_never_silently_drops_text(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot fit"):
        captions.render_caption_image(
            captions.CaptionCue(0.0, 1.0, "TOO MANY WORDS FOR ONE TINY LINE"),
            tmp_path / "caption.png",
            width=320,
            height=180,
            config={
                "font_size": 36,
                "min_font_size": 36,
                "max_width": 0.2,
                "max_lines": 1,
            },
        )


# test caption render rejects clipped position
def test_caption_render_rejects_clipped_position(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exceeds the canvas"):
        captions.render_caption_image(
            captions.CaptionCue(0.0, 1.0, "VISIBLE"),
            tmp_path / "caption.png",
            width=320,
            height=180,
            config={"font_size": 30, "x": 0, "y": 0.5},
        )


# test build caption track keeps gaps and total duration
def test_build_caption_track_keeps_gaps_and_total_duration(tmp_path: Path) -> None:
    srt = tmp_path / "captions.srt"
    srt.write_text(
        "1\n00:00:00,500 --> 00:00:01,000\nHELLO\n\n"
        "2\n00:00:01,500 --> 00:00:02,000\nWORLD\n",
        encoding="utf-8",
    )
    timeline = captions.build_caption_track(
        srt,
        tmp_path / "track",
        width=320,
        height=180,
        total_duration=2.5,
    )
    text = timeline.read_text(encoding="utf-8")
    assert text.startswith("ffconcat version 1.0")
    assert text.count("duration 0.500") == 5
    assert (tmp_path / "track" / "caption_0001.png").is_file()
    assert (tmp_path / "track" / "caption_0002.png").is_file()


# test caption track clamps cues after video without duplicate duration
def test_caption_track_clamps_cues_after_video_without_duplicate_duration(
    tmp_path: Path,
) -> None:
    srt = tmp_path / "captions.srt"
    srt.write_text(
        "1\n00:00:03,000 --> 00:00:04,000\nTOO LATE\n",
        encoding="utf-8",
    )
    timeline = captions.build_caption_track(
        srt,
        tmp_path / "track",
        width=320,
        height=180,
        total_duration=1.0,
    )
    text = timeline.read_text(encoding="utf-8")
    assert text.count("duration 1.000") == 1
    assert not (tmp_path / "track" / "caption_0001.png").exists()


# test caption renderer falls back when libass is missing
def test_caption_renderer_falls_back_when_libass_is_missing() -> None:
    with patch.object(captions, "ffmpeg_has_subtitle_filter", return_value=False):
        assert captions.choose_caption_renderer({}) == "pil"
        assert captions.choose_caption_renderer({"renderer": "libass"}) == "pil"


# test raster style selects pil even when libass exists
def test_raster_style_selects_pil_even_when_libass_exists() -> None:
    with patch.object(captions, "ffmpeg_has_subtitle_filter", return_value=True):
        assert captions.choose_caption_renderer({"font_size": 64}) == "pil"
        assert captions.choose_caption_renderer({}) == "libass"


# short subtitle cues must keep their declared timing rather than expanding to forty milliseconds
def test_short_cues_preserve_timeline(tmp_path):
    srt = tmp_path / "short.srt"
    srt.write_text("1\n00:00:00,010 --> 00:00:00,030\nHI\n")
    track = captions.build_caption_track(
        srt, tmp_path / "track", width=320, height=180, total_duration=0.05
    )
    durations = [
        float(line.split()[1])
        for line in track.read_text().splitlines()
        if line.startswith("duration ")
    ]
    assert durations == pytest.approx([0.01, 0.02, 0.02])
    assert sum(durations) == pytest.approx(0.05)


# encoded image timestamps must retain short cue boundaries in a real FFmpeg consumer
def test_ffmpeg_preserves_short_cue_boundaries(tmp_path):
    import shutil
    import subprocess

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg required")
    srt = tmp_path / "short.srt"
    srt.write_text("1\n00:00:00,010 --> 00:00:00,030\nHI\n")
    track = captions.build_caption_track(
        srt, tmp_path / "track", width=320, height=180, total_duration=0.05
    )
    movie = tmp_path / "captions.mov"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(track),
            "-t",
            "0.05",
            "-fps_mode",
            "vfr",
            "-c:v",
            "qtrle",
            str(movie),
        ],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "json",
            str(movie),
        ],
        check=True,
        capture_output=True,
    )
    pts = [
        float(row["best_effort_timestamp_time"])
        for row in json.loads(result.stdout)["frames"]
    ]
    assert pts == pytest.approx([0, 0.01, 0.03], abs=0.001)


# existing caption artifacts remain untouched when a build is requested again
def test_existing_outputs_are_not_deleted(tmp_path):
    srt = tmp_path / "input.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHELLO\n")
    destination = tmp_path / "track"
    destination.mkdir()
    existing = destination / "caption_0001.png"
    existing.write_bytes(b"existing evidence")
    with pytest.raises(FileExistsError):
        captions.build_caption_track(
            srt, destination, width=320, height=180, total_duration=1
        )
    assert existing.read_bytes() == b"existing evidence"


# invalid timing fails before generating image files
@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_track_rejects_invalid_duration(tmp_path, duration):
    source = tmp_path / "empty.srt"
    source.write_text("")
    with pytest.raises(ValueError):
        captions.build_caption_track(
            source, tmp_path / "track", width=320, height=180, total_duration=duration
        )
    assert not (tmp_path / "track").exists()
