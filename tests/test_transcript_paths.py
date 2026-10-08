"""Transcript file naming, and the same-stem collision it used to have.

Two supported inputs in one directory can share a stem (intro.mp4 and intro.mov). Naming
transcripts from the stem alone made both map to one file: batch mode submitted both jobs,
they wrote concurrently and the slower one won, and a later run reported both as cached.
"""

import json
import sys
import tempfile
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


HELPERS = Path(__file__).parents[1] / "helpers"
sys.path.insert(0, str(HELPERS))

import transcribe  # noqa: E402
import transcribe_batch  # noqa: E402


class TranscriptPathTests(unittest.TestCase):
    def setUp(self):
        self.edit = Path(tempfile.mkdtemp()) / "edit"

    def test_same_stem_sources_get_distinct_transcripts(self):
        mp4 = transcribe.transcript_path(self.edit, Path("takes/intro.mp4"))
        mov = transcribe.transcript_path(self.edit, Path("takes/intro.mov"))
        self.assertNotEqual(mp4, mov)

    def test_distinct_stems_stay_distinct(self):
        paths = {transcribe.transcript_path(self.edit, Path(f"takes/C010{i}.mov")).name
                 for i in range(3)}
        self.assertEqual(len(paths), 3)

    def test_extension_is_part_of_the_name(self):
        self.assertEqual(
            transcribe.transcript_path(self.edit, Path("takes/intro.mov")).name,
            "intro.mov.json",
        )

    def test_audio_track_still_disambiguates(self):
        primary = transcribe.transcript_path(self.edit, Path("takes/intro.mov"), 0)
        second = transcribe.transcript_path(self.edit, Path("takes/intro.mov"), 1)
        self.assertEqual(primary.name, "intro.mov.json")
        self.assertEqual(second.name, "intro.mov.track1.json")

    def test_legacy_name_is_the_pre_extension_one(self):
        self.assertEqual(
            transcribe.legacy_transcript_path(self.edit, Path("takes/intro.mov")).name,
            "intro.json",
        )


class ResolveTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.edit = Path(tempfile.mkdtemp()) / "edit"
        (self.edit / "transcripts").mkdir(parents=True)
        self.video = Path("takes/intro.mov")

    def test_falls_back_to_a_legacy_stem_only_transcript(self):
        legacy = transcribe.legacy_transcript_path(self.edit, self.video)
        legacy.write_text("{}")
        self.assertEqual(transcribe.resolve_transcript(self.edit, self.video), legacy)

    def test_prefers_the_current_name_when_both_exist(self):
        current = transcribe.transcript_path(self.edit, self.video)
        current.write_text("{}")
        transcribe.legacy_transcript_path(self.edit, self.video).write_text("{}")
        self.assertEqual(transcribe.resolve_transcript(self.edit, self.video), current)

    def test_returns_the_write_target_when_nothing_exists_yet(self):
        expected = transcribe.transcript_path(self.edit, self.video)
        self.assertEqual(transcribe.resolve_transcript(self.edit, self.video), expected)
        self.assertFalse(expected.exists())

    def test_a_legacy_sibling_never_satisfies_the_other_extension(self):
        """intro.mp4's transcript must not be handed to intro.mov."""
        mp4_transcript = transcribe.transcript_path(self.edit, Path("takes/intro.mp4"))
        mp4_transcript.write_text("{}")
        mov_transcript = transcribe.resolve_transcript(self.edit, self.video)
        self.assertNotEqual(mov_transcript, mp4_transcript)
        self.assertFalse(mov_transcript.exists())


class ResolveForSourceTests(unittest.TestCase):
    """An EDL names the file it uses for each source, so the lookup is exact."""

    def setUp(self):
        self.edit = Path(tempfile.mkdtemp()) / "edit"
        (self.edit / "transcripts").mkdir(parents=True)

    def _write(self, name: str) -> Path:
        path = self.edit / "transcripts" / name
        path.write_text("{}")
        return path

    def test_finds_the_extension_qualified_transcript(self):
        expected = self._write("C0103.MP4.json")
        found = transcribe.resolve_transcript_for_source(self.edit, "/abs/takes/C0103.MP4")
        self.assertEqual(found, expected)

    def test_accepts_a_relative_source_path(self):
        expected = self._write("C0103.MP4.json")
        found = transcribe.resolve_transcript_for_source(self.edit, "../takes/C0103.MP4")
        self.assertEqual(found, expected)

    def test_falls_back_to_a_legacy_stem_only_transcript(self):
        expected = self._write("C0103.json")
        found = transcribe.resolve_transcript_for_source(self.edit, "/abs/takes/C0103.MP4")
        self.assertEqual(found, expected)

    def test_prefers_the_qualified_name_when_both_exist(self):
        self._write("C0103.json")
        expected = self._write("C0103.MP4.json")
        found = transcribe.resolve_transcript_for_source(self.edit, "/abs/takes/C0103.MP4")
        self.assertEqual(found, expected)

    def test_same_stem_sources_are_told_apart(self):
        mp4 = self._write("intro.mp4.json")
        mov = self._write("intro.mov.json")
        self.assertEqual(transcribe.resolve_transcript_for_source(self.edit, "takes/intro.mp4"), mp4)
        self.assertEqual(transcribe.resolve_transcript_for_source(self.edit, "takes/intro.mov"), mov)

    def test_a_stem_with_no_transcript_resolves_to_the_write_target(self):
        found = transcribe.resolve_transcript_for_source(self.edit, "takes/C0103.MP4")
        self.assertEqual(found.name, "C0103.MP4.json")
        self.assertFalse(found.exists())


class BatchCacheTests(unittest.TestCase):
    """The batch cache check has to agree with the writer, or a rerun loses a take."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.takes = self.root / "takes"
        self.takes.mkdir()
        self.edit = self.root / "edit"

    def test_same_stem_sources_are_not_all_reported_as_cached(self):
        (self.takes / "intro.mp4").write_bytes(b"")
        (self.takes / "intro.mov").write_bytes(b"")
        videos = transcribe_batch.find_videos(self.takes)
        self.assertEqual(len(videos), 2)

        # transcript one of them; the other must not look cached because of it
        cached_path = transcribe.transcript_path(self.edit, self.takes / "intro.mp4")
        cached_path.parent.mkdir(parents=True, exist_ok=True)
        cached_path.write_text("{}")

        # select_pending() is the helper transcribe_batch.main() itself calls
        cached, pending = transcribe_batch.select_pending(videos, self.edit)
        self.assertEqual([v.name for v in cached], ["intro.mp4"])
        self.assertEqual([v.name for v in pending], ["intro.mov"])

    def test_a_stem_only_file_does_not_make_a_new_take_look_cached(self):
        """The collision has to stay fixed for projects that predate the extension."""
        (self.takes / "intro.mp4").write_bytes(b"")
        (self.takes / "intro.mov").write_bytes(b"")
        legacy = self.edit / "transcripts" / "intro.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("{}")  # written by the pre-extension layout, from the mp4

        videos = transcribe_batch.find_videos(self.takes)
        cached, pending = transcribe_batch.select_pending(videos, self.edit)
        # the stem-only file cannot be attributed to either take, so neither claims it
        self.assertEqual(cached, [])
        # find_videos sorts, so .mov comes before .mp4
        self.assertEqual([v.name for v in pending], ["intro.mov", "intro.mp4"])
        for v in videos:
            self.assertEqual(transcribe.resolve_transcript(self.edit, v).name,
                             f"intro{v.suffix}.json")

    def test_a_stem_only_file_still_serves_a_take_that_shares_its_stem_with_nothing(self):
        (self.takes / "intro.mp4").write_bytes(b"")
        legacy = self.edit / "transcripts" / "intro.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("{}")

        videos = transcribe_batch.find_videos(self.takes)
        cached, pending = transcribe_batch.select_pending(videos, self.edit)
        self.assertEqual([v.name for v in cached], ["intro.mp4"])
        self.assertEqual(pending, [])
        # pre-existing projects keep reading the file they already have
        self.assertEqual(transcribe.resolve_transcript(self.edit, self.takes / "intro.mp4"),
                         legacy)


class ParallelWriteTests(unittest.TestCase):
    """Both sources must survive a concurrent batch run."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.takes = self.root / "takes"
        self.takes.mkdir()
        self.edit = self.root / "edit"

        self.mp4 = self.takes / "intro.mp4"
        self.mov = self.takes / "intro.mov"
        self.mp4.write_bytes(b"")
        self.mov.write_bytes(b"")

        self._real = {
            name: getattr(transcribe, name)
            for name in ("count_audio_tracks", "extract_audio", "call_scribe")
        }
        self._take_of = {}
        transcribe.count_audio_tracks = lambda video_path: 1
        transcribe.extract_audio = self._fake_extract
        transcribe.call_scribe = self._fake_call_scribe

    def tearDown(self):
        for name, fn in self._real.items():
            setattr(transcribe, name, fn)

    def _fake_extract(self, video_path: Path, dest: Path, audio_track: int = 0) -> None:
        sample = int(0.5 * 32767).to_bytes(2, "little", signed=True)
        with wave.open(str(dest), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(sample * 1600)
        self._take_of[dest] = video_path.name

    def _fake_call_scribe(self, audio_path, api_key, language=None, num_speakers=None) -> dict:
        # Stand in for the Scribe response, labelled with the take it came from.
        return {"words": [], "text": self._take_of.get(audio_path, "unknown")}

    def test_both_transcripts_survive(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    transcribe.transcribe_one,
                    video=v,
                    edit_dir=self.edit,
                    api_key="k",
                    verbose=False,
                )
                for v in (self.mp4, self.mov)
            ]
            written = [f.result() for f in futures]

        self.assertEqual(len(set(written)), 2, "both sources wrote the same transcript file")
        self.assertEqual(
            sorted(p.name for p in written),
            ["intro.mov.json", "intro.mp4.json"],
        )

        # Each take kept its own response, rather than one overwriting the other.
        labels = {}
        for path in written:
            self.assertTrue(path.exists(), f"{path.name} was never written")
            with path.open(encoding="utf-8") as fh:
                labels[path.name] = json.load(fh)["text"]
        self.assertEqual(labels, {"intro.mp4.json": "intro.mp4", "intro.mov.json": "intro.mov"})


if __name__ == "__main__":
    unittest.main()