"""extract_all_segments must probe each distinct source once, not once per range.

`is_portrait_source` and `is_hdr_source` read facts that belong to the source
file, not to the cut range. Probing them per range spawns two ffprobe processes
for every segment, which dominates the setup cost of a long EDL.
"""

import contextlib
import importlib.util
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "render.py"
SPEC = importlib.util.spec_from_file_location("video_use_render", MODULE_PATH)
assert SPEC and SPEC.loader
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)


PORTRAIT_SOURCES = {"a.mp4"}
HDR_SOURCES = {"b.mp4"}


class SourceProbeReuseTests(unittest.TestCase):
    def _edl(self) -> dict:
        return {
            "sources": {"a": "a.mp4", "b": "b.mp4"},
            "ranges": [
                {"source": "a", "start": 0, "end": 1},
                {"source": "a", "start": 2, "end": 3},
                {"source": "b", "start": 0, "end": 1},
                {"source": "a", "start": 4, "end": 5},
            ],
        }

    def _run(self):
        """Probe answers differ per source, so a leak across sources is visible."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(
                    render, "is_portrait_source",
                    side_effect=lambda p: p.name in PORTRAIT_SOURCES,
                ) as portrait,
                patch.object(
                    render, "is_hdr_source",
                    side_effect=lambda p: p.name in HDR_SOURCES,
                ) as hdr,
                patch.object(render, "extract_segment") as extract,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                render.extract_all_segments(
                    self._edl(), Path(temp_dir), preview=False, fps="30"
                )
            return portrait, hdr, extract

    def test_each_source_is_probed_once_across_four_ranges(self):
        portrait, hdr, _ = self._run()
        self.assertEqual(portrait.call_count, 2)
        self.assertEqual(hdr.call_count, 2)

    def test_each_segment_gets_the_facts_of_its_own_source(self):
        _, _, extract = self._run()
        got = [
            (call.args[0].name, call.kwargs["portrait"], call.kwargs["hdr"])
            for call in extract.call_args_list
        ]
        self.assertEqual(
            got,
            [
                ("a.mp4", True, False),
                ("a.mp4", True, False),
                ("b.mp4", False, True),
                ("a.mp4", True, False),
            ],
        )


class ExtractSegmentArgumentTests(unittest.TestCase):
    def _extract(self, **kwargs) -> list[str]:
        """Run extract_segment with the probes patched out, return the ffmpeg cmd."""
        def unreachable(path):
            raise AssertionError(f"probed {path} despite being told the answer")

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(render, "is_portrait_source", side_effect=unreachable),
                patch.object(render, "is_hdr_source", side_effect=unreachable),
                patch.object(render.subprocess, "run") as run,
            ):
                render.extract_segment(
                    Path("a.mp4"), 0.0, 1.0, "", Path(temp_dir) / "seg.mp4",
                    rate="30", **kwargs,
                )
        return list(run.call_args.args[0])

    def _vf(self, **kwargs) -> str:
        cmd = self._extract(**kwargs)
        return cmd[cmd.index("-vf") + 1]

    def test_hdr_true_prepends_the_tonemap_chain(self):
        self.assertTrue(self._vf(portrait=False, hdr=True).startswith(render.TONEMAP_CHAIN))

    def test_hdr_false_leaves_the_tonemap_chain_out(self):
        self.assertNotIn("tonemap", self._vf(portrait=False, hdr=False))

    def test_portrait_true_scales_by_height(self):
        self.assertIn("scale=-2:1920", self._vf(portrait=True, hdr=False))

    def test_portrait_false_scales_by_width(self):
        self.assertIn("scale=1920:-2", self._vf(portrait=False, hdr=False))

    def test_omitted_arguments_still_probe(self):
        """The new arguments are optional; old callers keep the old behavior."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(render, "is_portrait_source", return_value=False) as portrait,
                patch.object(render, "is_hdr_source", return_value=False) as hdr,
                patch.object(render.subprocess, "run"),
            ):
                render.extract_segment(
                    Path("a.mp4"), 0.0, 1.0, "", Path(temp_dir) / "seg.mp4",
                    rate="30",
                )
        portrait.assert_called_once()
        hdr.assert_called_once()


if __name__ == "__main__":
    unittest.main()
