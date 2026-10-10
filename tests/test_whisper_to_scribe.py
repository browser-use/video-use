import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "whisper_to_scribe.py"
SPEC = importlib.util.spec_from_file_location("whisper_to_scribe", MODULE_PATH)
assert SPEC and SPEC.loader
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


# Verbatim `whisper-cli -ml 1 --split-on-word -oj` output for the sentence
# "90% of what a web agent does is completely wasted. We fixed this."
# Millisecond `offsets`, leading space in `text`, one word per segment.
# The only gap over 240ms is between "wasted." and "We".
WHISPER_OUTPUT = {
    "systeminfo": "whisper.cpp",
    "model": {"type": "large-v3-turbo"},
    "params": {"language": "en"},
    "result": {"language": "en"},
    "transcription": [
        {"offsets": {"from": 0, "to": 0}, "text": "   "},
        {"offsets": {"from": 20, "to": 500}, "text": " 90%"},
        {"offsets": {"from": 500, "to": 640}, "text": " of"},
        {"offsets": {"from": 640, "to": 930}, "text": " what"},
        {"offsets": {"from": 930, "to": 1000}, "text": " a"},
        {"offsets": {"from": 1000, "to": 1210}, "text": " web"},
        {"offsets": {"from": 1210, "to": 1570}, "text": " agent"},
        {"offsets": {"from": 1570, "to": 1860}, "text": " does"},
        {"offsets": {"from": 1860, "to": 2090}, "text": " is"},
        {"offsets": {"from": 2090, "to": 2720}, "text": " completely"},
        {"offsets": {"from": 2720, "to": 3200}, "text": " wasted."},
        {"offsets": {"from": 3440, "to": 3460}, "text": " We"},
        {"offsets": {"from": 3460, "to": 3890}, "text": " fixed"},
        {"offsets": {"from": 3890, "to": 4240}, "text": " this."},
    ],
}

EXPECTED_WORDS = [
    "90%", "of", "what", "a", "web", "agent",
    "does", "is", "completely", "wasted.", "We", "fixed", "this.",
]


class WhisperToScribeTests(unittest.TestCase):
    def test_emits_the_words_key_the_packer_reads(self):
        # pack_transcripts.py does `data.get("words", [])`; whisper emits
        # `transcription`. Without this key the packer reports
        # "_no speech detected_" — a silent failure, not an error.
        result = adapter.convert(WHISPER_OUTPUT)
        self.assertIn("words", result)
        self.assertEqual(
            [w["text"] for w in result["words"] if w["type"] == "word"],
            EXPECTED_WORDS,
        )

    def test_converts_millisecond_offsets_to_seconds(self):
        words = [w for w in adapter.convert(WHISPER_OUTPUT)["words"] if w["type"] == "word"]
        self.assertEqual(words[0]["start"], 0.02)
        self.assertEqual(words[0]["end"], 0.5)
        self.assertEqual(words[1]["start"], 0.5)
        self.assertEqual(words[1]["end"], 0.64)

    def test_synthesizes_spacing_entries_for_long_gaps(self):
        # The real 240ms gap between "wasted." and "We" only registers as
        # silence below the default threshold. The packer treats a
        # 'spacing' entry as the phrase-break signal.
        words = adapter.convert(WHISPER_OUTPUT, silence_threshold=0.2)["words"]
        spacing = [w for w in words if w["type"] == "spacing"]
        self.assertEqual(len(spacing), 1)
        self.assertEqual(spacing[0]["start"], 3.2)
        self.assertEqual(spacing[0]["end"], 3.44)

    def test_short_gaps_do_not_become_spacing(self):
        # At the default 0.5s threshold the 240ms gap is not silence, so it
        # must stay a single phrase rather than being shredded in two.
        words = adapter.convert(WHISPER_OUTPUT)["words"]
        self.assertFalse(any(w["type"] == "spacing" for w in words))

    def test_adjacent_words_do_not_become_spacing(self):
        # "90%" ends at exactly 500ms where "of" begins. At any real
        # threshold that 0ms gap must not split the phrase in two.
        words = adapter.convert(WHISPER_OUTPUT, silence_threshold=0.001)["words"]
        self.assertFalse(
            any(w["type"] == "spacing" and w["start"] == 0.5 for w in words),
            "0ms gap between adjacent words was emitted as spacing",
        )

    def test_zero_gap_boundary_matches_the_packers_comparison(self):
        # pack_transcripts.py breaks phrases on `gap >= silence_threshold`,
        # so this adapter must use the same inclusive comparison. At a 0.0
        # threshold a 0ms gap therefore does register — deliberately, so the
        # two modules never disagree about what counts as silence.
        words = adapter.convert(WHISPER_OUTPUT, silence_threshold=0.0)["words"]
        self.assertTrue(
            any(w["type"] == "spacing" and w["start"] == 0.5 for w in words),
            "adapter diverged from the packer's >= boundary semantics",
        )

    def test_drops_blank_segments(self):
        # whisper emits blank padding segments with zeroed offsets.
        words = [w for w in adapter.convert(WHISPER_OUTPUT)["words"] if w["type"] == "word"]
        self.assertTrue(all(w["text"].strip() for w in words))
        self.assertEqual(len(words), len(EXPECTED_WORDS))

    def test_blank_segment_offsets_do_not_poison_gap_detection(self):
        # The blank padding segment leads the list with zeroed offsets. It
        # must not be read as a real silence before the first word.
        words = adapter.convert(WHISPER_OUTPUT, silence_threshold=0.2)["words"]
        spacing = [w for w in words if w["type"] == "spacing"]
        self.assertEqual(len(spacing), 1, "blank segment created a phantom gap")
        self.assertEqual(spacing[0]["start"], 3.2)

    def test_leaves_speaker_id_unset_rather_than_inventing_one(self):
        # whisper.cpp does not diarize. A fabricated ID would silently
        # split every phrase at a fake speaker change.
        words = adapter.convert(WHISPER_OUTPUT)["words"]
        self.assertTrue(all(w["speaker_id"] is None for w in words))

    def test_handles_empty_transcript(self):
        self.assertEqual(adapter.convert({"transcription": []})["words"], [])
        self.assertEqual(adapter.convert({})["words"], [])

    def test_respects_custom_silence_threshold(self):
        # With a 1.0s threshold even the real 240ms gap is not silence.
        words = adapter.convert(WHISPER_OUTPUT, silence_threshold=1.0)["words"]
        self.assertFalse(any(w["type"] == "spacing" for w in words))


if __name__ == "__main__":
    unittest.main()
