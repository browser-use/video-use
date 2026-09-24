import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "render.py"
SPEC = importlib.util.spec_from_file_location("video_use_render", MODULE_PATH)
assert SPEC and SPEC.loader
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)


class PortraitDetectionTests(unittest.TestCase):
    def _is_portrait(self, stream: dict) -> bool:
        result = subprocess.CompletedProcess(
            [], 0, stdout=json.dumps({"streams": [stream]}), stderr=""
        )
        with patch.object(render.subprocess, "run", return_value=result) as run:
            portrait = render.is_portrait_source(Path("source.mp4"))
        cmd = run.call_args.args[0]
        show_entries = cmd[cmd.index("-show_entries") + 1]
        self.assertIn("stream_side_data=rotation", show_entries)
        self.assertNotIn("stream_tags=rotate", show_entries)
        return portrait

    def test_native_portrait_dimensions(self):
        self.assertTrue(self._is_portrait({"width": 1080, "height": 1920}))

    def test_native_landscape_dimensions(self):
        self.assertFalse(self._is_portrait({"width": 1920, "height": 1080}))

    def test_side_data_rotation_turns_coded_landscape_into_portrait(self):
        stream = {
            "width": 1920,
            "height": 1080,
            "side_data_list": [{"rotation": -90}],
        }
        self.assertTrue(self._is_portrait(stream))

    def test_plain_rotation_tag_without_side_data_is_ignored(self):
        stream = {"width": 1920, "height": 1080, "tags": {"rotate": "270"}}
        self.assertFalse(self._is_portrait(stream))

    def test_half_turn_rotation_does_not_change_orientation(self):
        stream = {
            "width": 1920,
            "height": 1080,
            "side_data_list": [{"rotation": 180}],
        }
        self.assertFalse(self._is_portrait(stream))

    def test_rotation_can_turn_coded_portrait_into_landscape(self):
        stream = {
            "width": 1080,
            "height": 1920,
            "side_data_list": [{"rotation": 90}],
        }
        self.assertFalse(self._is_portrait(stream))

    def test_invalid_probe_output_falls_back_to_landscape(self):
        result = subprocess.CompletedProcess([], 0, stdout="not json", stderr="")
        with patch.object(render.subprocess, "run", return_value=result):
            self.assertFalse(render.is_portrait_source(Path("source.mp4")))


if __name__ == "__main__":
    unittest.main()
