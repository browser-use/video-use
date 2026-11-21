import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


HELPERS = Path(__file__).parents[1] / "helpers"
# visual_notes imports its sibling helpers by bare name, as it does when run as a script.
sys.path.insert(0, str(HELPERS))
SPEC = importlib.util.spec_from_file_location("video_use_visual_notes", HELPERS / "visual_notes.py")
assert SPEC and SPEC.loader
vn = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vn)


def gemini_answer(data, finish="STOP"):
    return {
        "candidates": [{
            "content": {"parts": [
                {"text": "thinking about the clip", "thought": True},
                {"text": json.dumps(data)},
            ]},
            "finishReason": finish,
        }],
        "usageMetadata": {"totalTokenCount": 1234},
    }


class ParseTimestampTests(unittest.TestCase):
    def test_clock_formats(self):
        cases = {"00:05": 5.0, "01:30": 90.0, "1:02:03": 3723.0, "02:03.5": 123.5,
                 "75": 75.0, "12s": 12.0, 42: 42.0, 1.5: 1.5}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(vn.parse_timestamp(value), expected)

    def test_rejects_garbage(self):
        for value in ("", "soon", "1:xx", "-3"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    vn.parse_timestamp(value)


class PlanWindowsTests(unittest.TestCase):
    def test_short_take_is_one_window(self):
        self.assertEqual(vn.plan_windows(43.0, 900), [(0.0, 43.0)])

    def test_long_take_splits_evenly(self):
        self.assertEqual(vn.plan_windows(2200.0, 900), [(0.0, 900.0), (900.0, 1800.0), (1800.0, 2200.0)])

    def test_small_tail_folds_into_previous_window(self):
        self.assertEqual(vn.plan_windows(910.0, 900), [(0.0, 910.0)])

    def test_empty(self):
        self.assertEqual(vn.plan_windows(0.0, 900), [])


class ProxyCmdTests(unittest.TestCase):
    def test_trims_scales_and_keeps_optional_audio(self):
        cmd = vn.proxy_cmd(Path("in.mov"), Path("out.mp4"), 900.0, 1800.0)
        self.assertEqual(cmd[cmd.index("-ss") + 1], "900.000")
        self.assertEqual(cmd[cmd.index("-to") + 1], "1800.000")
        self.assertIn("fps=1,scale=-2:360", cmd)
        self.assertIn("0:a:0?", cmd)
        self.assertEqual(cmd[-1], "out.mp4")


class OffsetMomentsTests(unittest.TestCase):
    def test_offsets_into_source_timeline_and_clamps(self):
        raw = [
            {"start": "00:10", "end": "00:20", "note": "wide shot"},
            {"start": "14:50", "end": "16:00", "note": "runs past the window"},
            {"start": "later", "end": "00:05", "note": "unparseable start"},
            {"start": "00:01", "end": "00:02", "note": "   "},
        ]
        out = vn.offset_moments(raw, 900.0, 1800.0)
        self.assertEqual(out, [
            {"start": 910.0, "end": 920.0, "note": "wide shot"},
            {"start": 1790.0, "end": 1800.0, "note": "runs past the window"},
        ])

    def test_end_before_start_collapses_to_start(self):
        out = vn.offset_moments([{"start": "00:30", "end": "00:10", "note": "x"}], 0.0, 60.0)
        self.assertEqual(out[0]["start"], out[0]["end"])


class ResponseTextTests(unittest.TestCase):
    def test_skips_thought_parts(self):
        self.assertEqual(json.loads(vn.response_text(gemini_answer({"a": 1}))), {"a": 1})

    def test_truncated_answer_raises(self):
        with self.assertRaisesRegex(RuntimeError, "MAX_TOKENS"):
            vn.response_text(gemini_answer({}, finish="MAX_TOKENS"))

    def test_blocked_prompt_raises(self):
        with self.assertRaisesRegex(RuntimeError, "SAFETY"):
            vn.response_text({"promptFeedback": {"blockReason": "SAFETY"}})


class RenderMarkdownTests(unittest.TestCase):
    def test_matches_packed_transcript_style(self):
        md = vn.render_markdown([{
            "source": "C0103", "duration": 43.0,
            "summaries": ["Medium shot, office."],
            "moments": [{"start": 2.0, "end": 6.5, "note": "Looks off camera."}],
        }])
        self.assertIn("## C0103  (duration: 43.0s, 1 notes)", md)
        self.assertIn("  Medium shot, office.", md)
        self.assertIn("  [002.00-006.50] Looks off camera.", md)


class NotesOneTests(unittest.TestCase):
    def run_notes_one(self, duration, answers, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        edit_dir = Path(tmp.name) / "edit"
        video = Path(tmp.name) / "C0103.mp4"
        video.write_bytes(b"")

        def fake_ffmpeg(cmd, **_):
            Path(cmd[-1]).write_bytes(b"proxy")
            return MagicMock(returncode=0, stderr="")

        upload = MagicMock(side_effect=lambda p, k: {"name": f"files/{p.stem}", "uri": f"u/{p.stem}",
                                                       "mimeType": "video/mp4"})
        generate = MagicMock(side_effect=[gemini_answer(a) for a in answers])
        delete = MagicMock()
        with patch.object(vn, "has_video_stream", return_value=True), \
             patch.object(vn, "probe_duration", return_value=duration), \
             patch.object(vn.subprocess, "run", side_effect=fake_ffmpeg), \
             patch.object(vn, "upload_file", upload), \
             patch.object(vn, "generate_notes", generate), \
             patch.object(vn, "delete_file", delete), \
             patch("builtins.print"):
            out = vn.notes_one(video, edit_dir, "key", **kw)
        return out, upload, generate, delete

    def test_windows_are_offset_merged_and_uploads_deleted(self):
        answers = [
            {"summary": "Part one.", "moments": [{"start": "00:00", "end": "00:30", "note": "slate"}]},
            {"summary": "Part two.", "moments": [{"start": "01:00", "end": "01:10", "note": "glances away"}]},
        ]
        out, upload, generate, delete = self.run_notes_one(1500.0, answers, window=900)
        data = json.loads(out.read_text(encoding="utf-8"))

        self.assertEqual(out.name, "C0103.json")
        self.assertEqual(data["windows"], [[0.0, 900.0], [900.0, 1500.0]])
        self.assertEqual([(m["start"], m["note"]) for m in data["moments"]],
                         [(0.0, "slate"), (960.0, "glances away")])
        self.assertEqual(data["summaries"], ["Part one.", "Part two."])
        self.assertEqual(upload.call_count, 2)
        self.assertEqual(delete.call_count, 2)
        self.assertIn("part 2 of 2", generate.call_args_list[1].args[1])

    def test_cached_source_is_not_uploaded(self):
        out, upload, _, _ = self.run_notes_one(60.0, [{"summary": "s", "moments": []}])
        with patch.object(vn, "upload_file") as again, patch("builtins.print"):
            vn.notes_one(Path(out.parents[2] / "C0103.mp4"), out.parents[1], "key")
        again.assert_not_called()

    def test_upload_is_deleted_even_when_generation_fails(self):
        with self.assertRaises(RuntimeError):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            video = Path(tmp.name) / "C0104.mp4"
            delete = MagicMock()
            with patch.object(vn, "has_video_stream", return_value=True), \
                 patch.object(vn, "probe_duration", return_value=30.0), \
                 patch.object(vn.subprocess, "run",
                              side_effect=lambda cmd, **_: (Path(cmd[-1]).write_bytes(b"x"),
                                                            MagicMock(returncode=0))[1]), \
                 patch.object(vn, "upload_file", return_value={"name": "files/x", "uri": "u"}), \
                 patch.object(vn, "generate_notes", side_effect=RuntimeError("boom")), \
                 patch.object(vn, "delete_file", delete), \
                 patch("builtins.print"):
                try:
                    vn.notes_one(video, Path(tmp.name) / "edit", "key")
                finally:
                    delete.assert_called_once_with("files/x", "key")


class LoadApiKeyTests(unittest.TestCase):
    def test_reads_named_key_and_skips_empty_values(self):
        import transcribe
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("GEMINI_API_KEY=\nELEVENLABS_API_KEY=el\nGEMINI_API_KEY='gm'\n")
            with patch.object(transcribe, "__file__", str(Path(tmp) / "helpers" / "transcribe.py")):
                self.assertEqual(transcribe.load_api_key("GEMINI_API_KEY"), "gm")
                self.assertEqual(transcribe.load_api_key(), "el")


if __name__ == "__main__":
    unittest.main()
