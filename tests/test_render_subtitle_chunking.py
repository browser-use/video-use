"""subtitle_style chunking: break_on, balance and min_words.

All three default to the shipped behaviour, so an EDL that does not mention them
produces exactly the captions it did before.
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "render.py"
SPEC = importlib.util.spec_from_file_location("video_use_render", MODULE_PATH)
assert SPEC and SPEC.loader
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)

SENTENCE = "If you are between eighteen and twenty five, can I ask you a question?"


class ChunkingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.edit = Path(self.tmp.name)
        (self.edit / "transcripts").mkdir()
        words = [
            {"type": "word", "text": t, "start": 1.0 + i * 0.5, "end": 1.4 + i * 0.5}
            for i, t in enumerate(SENTENCE.split())
        ]
        (self.edit / "transcripts" / "A.json").write_text(json.dumps({"words": words}))
        self.addCleanup(self.tmp.cleanup)

    def cues(self, style):
        edl = {"sources": {"A": "/tmp/A.mov"},
               "ranges": [{"source": "A", "start": 0.0, "end": 30.0}]}
        if style is not None:
            edl["subtitle_style"] = style
        out = self.edit / "master.srt"
        render.build_master_srt(edl, self.edit, out)
        blocks = out.read_text().strip().split("\n\n")
        return [b.split("\n", 2)[2].strip() for b in blocks]

    def test_default_breaks_on_every_comma(self):
        # the shipped behaviour: 2-word cues, and the comma after "five" forces a
        # break that a phrase-aware read would not want
        c = self.cues({"case": "sentence"})
        self.assertEqual(c, ["If you", "are between", "eighteen and", "twenty five",
                             "can I", "ask you", "a question?"])

    def test_break_on_can_ignore_commas(self):
        c = self.cues({"case": "sentence", "words_per_chunk": 8, "break_on": ".!?"})
        self.assertEqual(c[0], "If you are between eighteen and twenty five")

    def test_balance_avoids_a_stranded_remainder(self):
        greedy = self.cues({"case": "sentence", "words_per_chunk": 8, "break_on": ".!?"})
        even = self.cues({"case": "sentence", "words_per_chunk": 8,
                          "break_on": ".!?", "balance": True})
        self.assertEqual(len(greedy), len(even))          # same number of cues
        self.assertEqual([len(x.split()) for x in greedy], [8, 6])
        self.assertEqual([len(x.split()) for x in even], [7, 7])

    def test_min_words_folds_a_short_cue_backwards(self):
        c = self.cues({"case": "sentence", "words_per_chunk": 12,
                       "break_on": ".!?", "min_words": 4})
        self.assertEqual(len(c), 1)
        self.assertTrue(c[0].endswith("a question?"))

    def test_min_words_rejects_nonsense(self):
        for bad in ("x", 0, -1):
            with self.assertRaises(ValueError):
                self.cues({"min_words": bad})


if __name__ == "__main__":
    unittest.main()
