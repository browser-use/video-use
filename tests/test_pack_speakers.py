import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "pack_transcripts", Path(__file__).resolve().parents[1] / "helpers" / "pack_transcripts.py"
)
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


class SpeakerGroupingTests(unittest.TestCase):
    def test_initial_unassigned_event_adopts_first_speaker(self):
        words = [
            {"type": "audio_event", "text": "laughter", "start": 0, "end": 0.2, "speaker_id": None},
            {"type": "word", "text": "Hello", "start": 0.3, "end": 0.5, "speaker_id": "speaker_0"},
            {"type": "word", "text": "Hi", "start": 0.6, "end": 0.8, "speaker_id": "speaker_1"},
        ]
        phrases = pack.group_into_phrases(words)
        self.assertEqual(phrases, [
            {"start": 0, "end": 0.5, "text": "(laughter) Hello", "speaker_id": "speaker_0"},
            {"start": 0.6, "end": 0.8, "text": "Hi", "speaker_id": "speaker_1"},
        ])
        markdown = pack.render_markdown([("take", 0.8, phrases)], 0.5)
        self.assertIn("S0 (laughter) Hello", markdown)
        self.assertIn("S1 Hi", markdown)

    def test_unassigned_event_keeps_an_existing_speaker(self):
        phrases = pack.group_into_phrases([
            {"type": "word", "text": "Hello", "start": 0, "end": 0.2, "speaker_id": "speaker_0"},
            {"type": "audio_event", "text": "laughter", "start": 0.3, "end": 0.5},
            {"type": "word", "text": "Hi", "start": 0.6, "end": 0.8, "speaker_id": "speaker_1"},
        ])
        self.assertEqual(len(phrases), 2)
        self.assertEqual(phrases[0]["speaker_id"], "speaker_0")
        self.assertEqual(phrases[0]["text"], "Hello (laughter)")
        self.assertEqual(phrases[1]["speaker_id"], "speaker_1")

    def test_unassigned_phrase_stays_unassigned(self):
        phrases = pack.group_into_phrases([
            {"type": "audio_event", "text": "laughter", "start": 0, "end": 0.2},
            {"type": "word", "text": "Hello", "start": 0.3, "end": 0.5, "speaker_id": None},
        ])
        self.assertEqual(len(phrases), 1)
        self.assertIsNone(phrases[0]["speaker_id"])
        self.assertEqual(phrases[0]["text"], "(laughter) Hello")


if __name__ == "__main__":
    unittest.main()
