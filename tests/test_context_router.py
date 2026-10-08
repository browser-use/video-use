"""Check guidance selection, mandatory rules, and portable command line receipts."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("context_router", ROOT / "helpers/context_router.py")
router = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(router)


# Exercise actual shipped instruction files without loading optional editing engines.
class SelectionTests(unittest.TestCase):
    def test_core_is_always_present(self):
        receipt = router.select_context()
        self.assertEqual([f["id"] for f in receipt["files"]], ["core"])
        text = router.read_bundle(receipt)
        self.assertIn("## Hard Rules", text)
        self.assertIn("12. **All session outputs", text)
        self.assertIn("## EDL format", text)

    def test_interview_does_not_load_animation_or_color(self):
        receipt = router.select_context(["speech", "cuts", "captions", "cuts"])
        self.assertEqual([f["id"] for f in receipt["files"]], ["core", "cuts", "speech", "captions"])
        text = router.read_bundle(receipt)
        self.assertIn("## The packed transcript", text)
        self.assertIn("## Subtitles", text)
        self.assertNotIn("**Typing text anchor trick:**", text)
        self.assertNotIn("Mental model is ASC CDL", text)

    def test_edl_adds_to_explicit_needs(self):
        edl = {"version": 1, "ranges": [{"source": "a"}], "subtitles": "master.srt",
               "grade": "neutral_punch", "overlays": [{"file": "overlay.mp4"}]}
        receipt = router.select_context(["sound"], edl)
        self.assertEqual([f["id"] for f in receipt["files"]], ["core", "cuts", "captions", "color", "animation", "sound"])
        self.assertIn("declared EDL operation", receipt["files"][1]["reasons"])

    def test_no_treatment_does_not_load_optional_cards(self):
        receipt = router.select_context(edl={"grade": "none", "overlays": [], "ranges": []})
        self.assertEqual(len(receipt["files"]), 1)

    def test_invalid_inputs_are_explicit(self):
        for edl in ([], {"version": True}, {"version": 3}, {"ranges": {}}, {"overlays": "x"}, {"grade": 1}):
            with self.subTest(edl=edl), self.assertRaises(ValueError):
                router.select_context(edl=edl)
        with self.assertRaisesRegex(ValueError, "Unknown guidance"):
            router.select_context(["../private"])

    def test_budget_keeps_required_guidance(self):
        with self.assertRaisesRegex(ValueError, "no required instructions were dropped"):
            router.select_context(["animation"], max_bytes=1)
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                router.select_context(max_bytes=value)

    def test_receipt_detects_changed_instructions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "SKILL.md").write_text("first", encoding="utf-8")
            receipt = router.select_context(root=root)
            (root / "SKILL.md").write_text("second", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed during selection"):
                router.read_bundle(receipt, root=root)

    def test_all_cards_exist_and_are_recorded_once(self):
        receipt = router.select_context(router.CARDS)
        self.assertEqual(len(receipt["files"]), 7)
        self.assertEqual(receipt["total_bytes"], sum(f["bytes"] for f in receipt["files"]))
        self.assertNotIn("task", receipt)
        router.read_bundle(receipt)

    def test_cli_works_from_another_directory_and_preserves_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            receipt_path = root / "edit/context.json"
            command = [sys.executable, str(ROOT / "helpers/context_router.py"), "--need", "captions", "--receipt", str(receipt_path)]
            result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            original = receipt_path.read_bytes()
            self.assertEqual(json.loads(result.stdout), json.loads(original))
            again = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(receipt_path.read_bytes(), original)

    def test_invalid_edl_creates_no_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            edl = root / "bad.json"
            for value in ("[]", "null"):
                edl.write_text(value, encoding="utf-8")
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    router.main(["--edl", str(edl), "--receipt", str(root / "record.json")])
            self.assertFalse((root / "record.json").exists())


if __name__ == "__main__":
    unittest.main()
