import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
HELPERS = ROOT / "helpers"
sys.path.insert(0, str(HELPERS))

import transcribe  # noqa: E402
import transcribe_batch  # noqa: E402


def load_helper(name):
    path = HELPERS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"video_use_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render = load_helper("render")
timeline_view = load_helper("timeline_view")


class TranscriptCacheTests(unittest.TestCase):
    def test_ambiguous_fingerprintless_legacy_transcript_is_not_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            edit_dir = root / "edit"
            transcripts = edit_dir / "transcripts"
            transcripts.mkdir(parents=True)
            (transcripts / "clip.json").write_text('{"words": []}')
            (transcripts / "clip.12345678.json").write_text('{"words": []}')
            video = root / "clip.mp4"
            video.write_bytes(b"video")

            self.assertIsNone(
                transcribe._legacy_transcript_path(edit_dir, video)
            )

    def test_custom_shared_legacy_cache_is_not_assigned_without_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            edit_dir = root / "shared-edit"
            transcripts = edit_dir / "transcripts"
            transcripts.mkdir(parents=True)
            (transcripts / "clip.json").write_text('{"words": []}')
            video = root / "takes" / "B" / "clip.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"video B")

            self.assertIsNone(
                transcribe._legacy_transcript_path(edit_dir, video)
            )

    def test_unambiguous_fingerprintless_legacy_transcript_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            edit_dir = root / "edit"
            transcripts = edit_dir / "transcripts"
            transcripts.mkdir(parents=True)
            legacy = transcripts / "clip.json"
            legacy.write_text('{"words": []}')
            video = root / "clip.mp4"
            video.write_bytes(b"video")

            self.assertEqual(
                transcribe._legacy_transcript_path(edit_dir, video), legacy
            )

    def test_matching_hashed_transcript_is_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "clip.mp4"
            video.write_bytes(b"current source")
            edit_dir = root / "edit"
            output = transcribe.transcript_path(edit_dir, video)
            output.parent.mkdir(parents=True)
            output.write_text(
                json.dumps({"_source": transcribe._source_fingerprint(video)})
            )

            self.assertEqual(
                transcribe.cached_transcript_path(edit_dir, video), output
            )

    def test_batch_does_not_skip_a_stale_hashed_transcript(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            videos = root / "videos"
            videos.mkdir()
            video = videos / "clip.mp4"
            video.write_bytes(b"current source")
            edit_dir = videos / "edit"
            output = transcribe.transcript_path(edit_dir, video)
            output.parent.mkdir(parents=True)
            output.write_text(
                json.dumps(
                    {
                        "_source": {
                            "path": str(video.resolve()),
                            "mtime_ns": 0,
                            "size": 1,
                        }
                    }
                )
            )

            with (
                patch.object(sys, "argv", ["transcribe_batch.py", str(videos)]),
                patch.object(transcribe_batch, "load_api_key", return_value="test"),
                patch.object(
                    transcribe_batch, "transcribe_one", return_value=output
                ) as transcribe_one,
            ):
                transcribe_batch.main()

            transcribe_one.assert_called_once()

    def test_batch_does_not_reuse_legacy_cache_for_duplicate_stems(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            videos = root / "videos"
            videos.mkdir()
            (videos / "clip.mp4").write_bytes(b"first source")
            (videos / "clip.mov").write_bytes(b"second source")
            edit_dir = videos / "edit"
            transcripts = edit_dir / "transcripts"
            transcripts.mkdir(parents=True)
            (transcripts / "clip.json").write_text('{"words": []}')

            with (
                patch.object(sys, "argv", ["transcribe_batch.py", str(videos)]),
                patch.object(transcribe_batch, "load_api_key", return_value="test"),
                patch.object(
                    transcribe_batch,
                    "transcribe_one",
                    return_value=transcripts / "result.json",
                ) as transcribe_one,
            ):
                transcribe_batch.main()

            self.assertEqual(transcribe_one.call_count, 2)
            self.assertEqual(
                {call.kwargs["allow_legacy_cache"] for call in transcribe_one.call_args_list},
                {False},
            )

    def test_glob_characters_in_source_stem_are_matched_literally(self):
        with tempfile.TemporaryDirectory() as temp:
            transcripts = Path(temp)
            expected = transcripts / "clip[1].deadbeef.json"
            unrelated = transcripts / "clip1.12345678.json"
            expected.write_text("{}")
            unrelated.write_text("{}")

            self.assertEqual(render._find_transcript(transcripts, "clip[1]"), expected)
            self.assertEqual(
                timeline_view._resolve_transcript(transcripts, "clip[1]"),
                expected,
            )


if __name__ == "__main__":
    unittest.main()
