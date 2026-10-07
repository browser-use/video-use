import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


MODULE_PATH = Path(__file__).parents[1] / "helpers" / "transcribe.py"


def load_transcribe():
    spec = importlib.util.spec_from_file_location("video_use_transcribe", MODULE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ScribeModelTests(unittest.TestCase):
    def _sent_model(self, env):
        with patch.dict(os.environ, env, clear=False):
            if "SCRIBE_MODEL" not in env:
                os.environ.pop("SCRIBE_MODEL", None)
            mod = load_transcribe()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"words": []}
        with tempfile.NamedTemporaryFile(suffix=".wav") as f, \
             patch.object(mod.requests, "post", return_value=resp) as post:
            mod.call_scribe(Path(f.name), "key")
        return post.call_args.kwargs["data"]["model_id"]

    def test_defaults_to_scribe_v2(self):
        self.assertEqual(self._sent_model({}), "scribe_v2")

    def test_env_override(self):
        self.assertEqual(self._sent_model({"SCRIBE_MODEL": "scribe_v1"}), "scribe_v1")


if __name__ == "__main__":
    unittest.main()
