"""tests for the caption provenance rules in the edl validator
they cover the version gate the strict handoff override and evidence files with and without timed words
"""

import json
from pathlib import Path

import pytest

from helpers.edl import EDLValidationError, validate_edl


# build a minimal renderable edl with one real source file and one range
def _ready_edl(tmp_path: Path, *, version: int = 2) -> dict:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    return {
        "version": version,
        "sources": {"source": "source.mp4"},
        "ranges": [{"source": "source", "start": 0, "end": 3}],
    }


# write a tiny ASS file so the subtitles path exists on disk
def _write_subtitles(tmp_path: Path) -> None:
    (tmp_path / "master.ass").write_text(
        "[Events]\nDialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Hello\n",
        encoding="utf-8",
    )


# an edl with no captions block passes even under the strict handoff policy
def test_music_only_edl_needs_no_caption_contract(tmp_path: Path) -> None:
    validate_edl(
        _ready_edl(tmp_path),
        tmp_path,
        require_caption_provenance=True,
    )


# version two edls with subtitles must declare captions provenance
def test_version_two_rejects_subtitles_without_speech_provenance(
    tmp_path: Path,
) -> None:
    edl = _ready_edl(tmp_path)
    _write_subtitles(tmp_path)
    edl["subtitles"] = "master.ass"

    with pytest.raises(EDLValidationError, match="captions.provenance"):
        validate_edl(edl, tmp_path)


# the strict override applies the speech evidence rule to legacy version one edls
def test_fresh_handoff_policy_rejects_legacy_unproven_captions(
    tmp_path: Path,
) -> None:
    edl = _ready_edl(tmp_path, version=1)
    _write_subtitles(tmp_path)
    edl["subtitles"] = "master.ass"

    with pytest.raises(EDLValidationError, match="audible speech"):
        validate_edl(edl, tmp_path, require_caption_provenance=True)


# version one edls with bare subtitles still validate by default
def test_version_one_caption_edl_remains_legacy_compatible(tmp_path: Path) -> None:
    edl = _ready_edl(tmp_path, version=1)
    _write_subtitles(tmp_path)
    edl["subtitles"] = "master.ass"

    validate_edl(edl, tmp_path)


# a transcript with timestamped words satisfies the provenance contract
def test_caption_contract_accepts_timestamped_source_speech(tmp_path: Path) -> None:
    edl = _ready_edl(tmp_path)
    _write_subtitles(tmp_path)
    transcript = tmp_path / "transcripts" / "source.json"
    transcript.parent.mkdir()
    transcript.write_text(
        json.dumps(
            {
                "words": [
                    {"type": "word", "text": "Hello", "start": 0.1, "end": 0.5}
                ]
            }
        ),
        encoding="utf-8",
    )
    edl.update(
        {
            "subtitles": "master.ass",
            "captions": {
                "provenance": {
                    "kind": "source_transcript",
                    "files": ["transcripts/source.json"],
                },
                "safe_region": {"x": 0, "y": 0.84, "width": 1, "height": 0.16},
            },
        }
    )

    validate_edl(edl, tmp_path)


# evidence json with an empty words list is rejected
def test_caption_contract_rejects_evidence_without_spoken_words(
    tmp_path: Path,
) -> None:
    edl = _ready_edl(tmp_path)
    _write_subtitles(tmp_path)
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({"words": []}), encoding="utf-8")
    edl.update(
        {
            "subtitles": "master.ass",
            "captions": {
                "provenance": {
                    "kind": "narration_alignment",
                    "files": ["transcript.json"],
                }
            },
        }
    )

    with pytest.raises(EDLValidationError, match="no timestamped spoken words"):
        validate_edl(edl, tmp_path)
