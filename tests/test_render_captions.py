import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "render.py"
SPEC = importlib.util.spec_from_file_location("video_use_render", MODULE_PATH)
assert SPEC and SPEC.loader
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)


def w(text, start, end):
    return {"type": "word", "text": text, "start": start, "end": end}


# Scribe word timings of a real take: "90% of what a web agent does is completely wasted. We fix this."
TAKE = [
    w("90%", 2.64, 3.04), w("of", 3.06, 3.14), w("what", 3.16, 3.26), w("a", 3.30, 3.34),
    w("web", 3.36, 3.50), w("agent", 3.54, 3.82), w("does", 3.86, 4.14), w("is", 4.48, 4.56),
    w("completely", 4.58, 4.90), w("wasted.", 4.94, 5.34), w("We", 6.08, 6.18), w("fix", 6.24, 6.42),
    w("this.", 6.48, 6.70),
]


def texts(chunks):
    return [" ".join(x["text"] for x in c) for c in chunks]


class ChunkWordsTests(unittest.TestCase):
    def test_real_take(self):
        self.assertEqual(
            texts(render.chunk_words(TAKE)),
            ["90% of", "what a web", "agent does", "is completely", "wasted.", "We fix this."],
        )

    def test_pause_splits_across_words(self):
        # the old fixed pairs produced "does is" across a 0.34 s pause
        chunks = texts(render.chunk_words(TAKE))
        self.assertNotIn("does is", [c.lower() for c in chunks])

    def test_no_multiword_cue_flashes(self):
        for c in render.chunk_words(TAKE):
            # only the word cap may leave a multi-word cue under the minimum (very fast speech)
            if len(c) > 1 and len(c) < render.CHUNK_MAX_WORDS:
                self.assertGreaterEqual(c[-1]["end"] - c[0]["start"], render.CHUNK_MIN_S, texts([c]))

    def test_punctuation_still_breaks(self):
        self.assertEqual(texts(render.chunk_words([w("Hi,", 0, 0.1), w("there", 0.12, 0.2)])), ["Hi,", "there"])

    def test_caps_at_max_words(self):
        fast = [w(f"w{i}", i * 0.05, i * 0.05 + 0.04) for i in range(7)]
        self.assertTrue(all(len(c) <= render.CHUNK_MAX_WORDS for c in render.chunk_words(fast)))

    def test_skips_empty_words(self):
        self.assertEqual(texts(render.chunk_words([w(" ", 0, 0.1), w("ok", 0.1, 0.5)])), ["ok"])


class BuildMasterSrtTests(unittest.TestCase):
    def test_output_timeline_cues(self):
        with tempfile.TemporaryDirectory() as d:
            edit = Path(d)
            (edit / "transcripts").mkdir()
            (edit / "transcripts" / "C0103.json").write_text(json.dumps({"words": TAKE}))
            edl = {"sources": {"C0103": "C0103.mp4"}, "ranges": [{"source": "C0103", "start": 2.55, "end": 6.8}]}
            out = edit / "master.srt"
            render.build_master_srt(edl, edit, out)
            cues = [b.splitlines() for b in out.read_text().strip().split("\n\n")]
            self.assertEqual([c[2] for c in cues], ["90% OF", "WHAT A WEB", "AGENT DOES", "IS COMPLETELY", "WASTED.", "WE FIX THIS."])
            self.assertEqual(cues[0][1], "00:00:00,090 --> 00:00:00,590")

    def test_captions_find_the_extension_qualified_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            edit = Path(d)
            (edit / "transcripts").mkdir()
            (edit / "transcripts" / "C0103.MP4.json").write_text(json.dumps({"words": TAKE}))
            edl = {"sources": {"C0103": "/abs/takes/C0103.MP4"}, "ranges": [{"source": "C0103", "start": 2.55, "end": 6.8}]}
            out = edit / "master.srt"
            render.build_master_srt(edl, edit, out)
            self.assertEqual(len(out.read_text().strip().split("\n\n")), 6)

    def test_each_same_stem_take_gets_its_own_captions(self):
        with tempfile.TemporaryDirectory() as d:
            edit = Path(d)
            (edit / "transcripts").mkdir()
            (edit / "transcripts" / "intro.mp4.json").write_text(json.dumps({"words": TAKE}))
            # the .mov take gets its own words, so picking the wrong transcript - or picking
            # none at all, which also leaves master.srt empty - cannot pass this
            mov_words = [w("MOV", 2.60, 2.90), w("TAKE", 2.90, 3.10),
                         w("ONLY", 3.10, 3.45)]
            (edit / "transcripts" / "intro.mov.json").write_text(json.dumps({"words": mov_words}))
            edl = {"sources": {"intro": "/abs/takes/intro.mov"}, "ranges": [{"source": "intro", "start": 2.55, "end": 6.8}]}
            out = edit / "master.srt"
            render.build_master_srt(edl, edit, out)
            cues = [b.splitlines() for b in out.read_text().strip().split("\n\n")]
            # chunk_words closes on a 2-word cue that lasts >= CHUNK_MIN_S, so the exact
            # split does not matter - whose words appear is the whole point
            self.assertEqual([c[2] for c in cues], ["MOV TAKE", "ONLY"])
            self.assertNotIn("WASTED.", out.read_text())


class SubtitlesPathTests(unittest.TestCase):
    def test_relative_to_edl_dir(self):
        with tempfile.TemporaryDirectory() as d:
            edit = Path(d)
            (edit / "master.srt").write_text("")
            self.assertEqual(render.resolve_subtitles_path("master.srt", edit), (edit / "master.srt").resolve())

    def test_falls_back_to_cwd(self):
        # EDL in edit/ that says "edit/master.srt" (path written from the project root)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "edit").mkdir()
            (root / "edit" / "master.srt").write_text("")
            cwd = os.getcwd()
            try:
                os.chdir(root)
                got = render.resolve_subtitles_path("edit/master.srt", root / "edit")
            finally:
                os.chdir(cwd)
            self.assertEqual(got.resolve(), (root / "edit" / "master.srt").resolve())

    def test_missing_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as e:
                render.resolve_subtitles_path("nope.srt", Path(d))
            self.assertIn("--no-subtitles", str(e.exception))


if __name__ == "__main__":
    unittest.main()
