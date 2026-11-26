import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HELPERS = Path(__file__).parents[1] / "helpers"


def load(name):
    spec = importlib.util.spec_from_file_location(name, HELPERS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scan = load("session_scan")
capture = load("session_capture")
lint = load("broll_lint")


class SessionScanTests(unittest.TestCase):
    def test_flags_private_details(self):
        text = "mail jane@clinic.org, see /Users/someone/notes and app.notion.com/p/abc, MRN 4471, call 512-555-0199"
        kinds = {k for k, _ in scan.scan_text(text)}
        self.assertTrue({"email", "home path", "private link", "patient identifier", "phone"} <= kinds)

    def test_denylist_is_whole_word_and_case_blind(self):
        self.assertEqual(scan.scan_text("met with acme health today", ["Acme Health"]), [("denylist", "acme health")])
        self.assertEqual(scan.scan_text("acmehealthy snacks", ["Acme Health"]), [])

    def test_clean_text_passes(self):
        self.assertEqual(scan.scan_text("Cloud threads get none of the hooks or permission rules."), [])

    def test_scans_metadata_not_only_messages(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"cwd": "/Users/someone/x", "message": {"content": "hi"}}) + "\n")
        texts = scan.texts_from_jsonl(f.name)
        self.assertTrue(any(scan.scan_text(t) for t in texts))


class SessionCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        rows = [
            {"type": "user", "timestamp": "2026-11-16T16:48:58.829Z", "cwd": "/Users/someone/private",
             "message": {"role": "user", "content": "Can you still do it locally? Also email jane@x.org"}},
            {"type": "assistant", "timestamp": "2026-11-16T16:54:35.876Z",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Yes. Cloud threads get none of the hooks."}]}},
        ]
        self.tmp.write("".join(json.dumps(r) + "\n" for r in rows))
        self.tmp.close()

    def spec(self, excerpt):
        return {"session": self.tmp.name, "keep": [
            {"at": "2026-11-16T16:48:58", "role": "user", "excerpts": ["Can you still do it locally?"]},
            {"at": "2026-11-16T16:54:35", "role": "assistant", "excerpts": [excerpt]}]}

    def test_keeps_only_excerpts_and_no_metadata(self):
        recs = capture.build_capture(self.spec("Cloud threads get none of the hooks."))
        blob = json.dumps(recs)
        self.assertNotIn("jane@x.org", blob)
        self.assertNotIn("/Users/", blob)
        self.assertEqual(recs[1]["parentUuid"], recs[0]["uuid"])

    def test_refuses_text_that_is_not_verbatim(self):
        with self.assertRaises(ValueError):
            capture.build_capture(self.spec("Cloud threads get all of the hooks."))


def frames(*pairs, step=0.25):
    return {"step": step, "frames": [{"t": t, "texts": list(ts)} for t, ts in pairs]}


def held(text, t0, t1, step=0.25):
    out, t = [], t0
    while t <= t1:
        out.append((t, [text]))
        t += step
    return out


class BrollLintTests(unittest.TestCase):
    WORDS = [{"type": "word", "text": w, "start": s} for w, s in
             [("staff", 10.0), ("felt", 10.4), ("twelve", 10.8), ("times", 11.1), ("faster,", 11.4), ("2,500", 30.0), ("changes", 30.5)]]

    def rules(self, probs):
        return [p[0] for p in probs]

    def test_spoken_numbers_pass_and_unspoken_fail(self):
        ok = lint.lint(frames(*held("12x FASTER", 9, 13)), self.WORDS)
        self.assertEqual(ok, [])
        bad = lint.lint(frames(*held("30,000 A DAY", 9, 13)), self.WORDS, window=5)
        self.assertEqual(self.rules(bad), ["B1"])

    def test_numerals_with_commas_match(self):
        self.assertEqual(lint.lint(frames(*held("2,500 A DAY", 28, 32)), self.WORDS), [])

    def test_dates_are_exempt(self):
        self.assertEqual(lint.lint(frames(*held("SESSION, NOVEMBER 16, 2026", 9, 13)), self.WORDS), [])

    def test_dash_and_banned_word(self):
        probs = lint.lint(frames(*held("Fast — genuinely", 9, 13)))
        self.assertEqual(sorted(self.rules(probs)), ["B2", "B2"])

    def test_flash_is_caught(self):
        self.assertEqual(self.rules(lint.lint(frames((5.0, ["BLINK"])))), ["B3"])

    def test_text_under_head_card(self):
        f = {"step": .25, "frames": [{"t": t, "texts": [{"s": "LEFT", "box": [110, 120, 600, 180]}, {"s": "RIGHT", "box": [1500, 300, 1700, 340]}]} for t in (1, 1.25, 1.5, 1.75)]}
        self.assertEqual([(r, w) for r, w, _ in lint.lint(f)], [("B5", "RIGHT")])

    def test_palette_and_fonts(self):
        page = "@font-face{font-family:M;src:url(fonts/Montserrat.ttf)}@font-face{font-family:X;src:url(fonts/Comic.ttf)} c='#0D1C37' d='#6FA8DC'"
        probs = lint.lint(frames(), None, page)
        self.assertEqual(sorted((r, w) for r, w, _ in probs), [("B4", "#6FA8DC"), ("B4", "fonts/Comic.ttf")])


if __name__ == "__main__":
    unittest.main()
