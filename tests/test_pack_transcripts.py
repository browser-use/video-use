import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "pack_transcripts.py"
SPEC = importlib.util.spec_from_file_location("video_use_pack_transcripts", MODULE_PATH)
assert SPEC and SPEC.loader
pack_transcripts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pack_transcripts)


class FormatTimeTests(unittest.TestCase):
    def test_pads_to_fixed_width(self):
        self.assertEqual(pack_transcripts.format_time(5.2), "005.20")

    def test_handles_large_values(self):
        self.assertEqual(pack_transcripts.format_time(123.456), "123.46")

    def test_handles_zero(self):
        self.assertEqual(pack_transcripts.format_time(0.0), "000.00")


class FormatDurationTests(unittest.TestCase):
    def test_under_a_minute_uses_seconds_only(self):
        self.assertEqual(pack_transcripts.format_duration(43.0), "43.0s")

    def test_at_or_over_a_minute_uses_minutes_and_seconds(self):
        self.assertEqual(pack_transcripts.format_duration(65.5), "1m 05.5s")

    def test_exact_minute_boundary(self):
        self.assertEqual(pack_transcripts.format_duration(60.0), "1m 00.0s")


class GroupIntoPhrasesTests(unittest.TestCase):
    def test_single_speaker_no_gaps_forms_one_phrase(self):
        words = [
            {"type": "word", "text": "hello", "start": 0.0, "end": 0.3, "speaker_id": "speaker_0"},
            {"type": "spacing", "start": 0.3, "end": 0.35},
            {"type": "word", "text": "world", "start": 0.35, "end": 0.7, "speaker_id": "speaker_0"},
        ]
        phrases = pack_transcripts.group_into_phrases(words, silence_threshold=0.5)
        self.assertEqual(len(phrases), 1)
        self.assertEqual(phrases[0]["text"], "hello world")
        self.assertEqual(phrases[0]["start"], 0.0)
        self.assertEqual(phrases[0]["end"], 0.7)
        self.assertEqual(phrases[0]["speaker_id"], "speaker_0")

    def test_long_silence_splits_into_two_phrases(self):
        words = [
            {"type": "word", "text": "hello", "start": 0.0, "end": 0.3, "speaker_id": "speaker_0"},
            {"type": "spacing", "start": 0.3, "end": 1.0},
            {"type": "word", "text": "world", "start": 1.0, "end": 1.3, "speaker_id": "speaker_0"},
        ]
        phrases = pack_transcripts.group_into_phrases(words, silence_threshold=0.5)
        self.assertEqual(len(phrases), 2)
        self.assertEqual(phrases[0]["text"], "hello")
        self.assertEqual(phrases[1]["text"], "world")

    def test_speaker_change_splits_into_two_phrases_even_without_gap(self):
        words = [
            {"type": "word", "text": "hello", "start": 0.0, "end": 0.3, "speaker_id": "speaker_0"},
            {"type": "word", "text": "hi", "start": 0.3, "end": 0.5, "speaker_id": "speaker_1"},
        ]
        phrases = pack_transcripts.group_into_phrases(words, silence_threshold=0.5)
        self.assertEqual(len(phrases), 2)
        self.assertEqual(phrases[0]["speaker_id"], "speaker_0")
        self.assertEqual(phrases[1]["speaker_id"], "speaker_1")

    def test_audio_event_is_wrapped_in_parentheses(self):
        words = [
            {"type": "audio_event", "text": "laughter", "start": 0.0, "end": 0.5, "speaker_id": "speaker_0"},
        ]
        phrases = pack_transcripts.group_into_phrases(words, silence_threshold=0.5)
        self.assertEqual(phrases[0]["text"], "(laughter)")

    def test_punctuation_spacing_is_collapsed(self):
        words = [
            {"type": "word", "text": "hello", "start": 0.0, "end": 0.3, "speaker_id": "speaker_0"},
            {"type": "word", "text": ",", "start": 0.3, "end": 0.3, "speaker_id": "speaker_0"},
            {"type": "word", "text": "world", "start": 0.35, "end": 0.7, "speaker_id": "speaker_0"},
            {"type": "word", "text": "?", "start": 0.7, "end": 0.7, "speaker_id": "speaker_0"},
        ]
        phrases = pack_transcripts.group_into_phrases(words, silence_threshold=0.5)
        self.assertEqual(phrases[0]["text"], "hello, world?")

    def test_no_words_returns_no_phrases(self):
        self.assertEqual(pack_transcripts.group_into_phrases([], silence_threshold=0.5), [])


if __name__ == "__main__":
    unittest.main()
