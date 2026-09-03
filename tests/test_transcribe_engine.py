"""unit tests for engine selection and caching in helpers transcribe py
they cover dotenv precedence the printed engine line invalid values the provider boundary backwards compatibility and force
no upload runs and no model loads
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch


HELPERS = Path(__file__).parents[1] / "helpers"
sys.path.insert(0, str(HELPERS))


# load a helper straight from its file path under a private module name
def load_helper(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, HELPERS / name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


transcribe = load_helper("transcribe.py", "video_use_transcribe")
transcribe_batch = load_helper("transcribe_batch.py", "video_use_transcribe_batch")


# an env mapping in the shape load_env returns
def env_with(**names) -> dict:
    return {name: (value, "test") for name, value in names.items()}


# write a short audible wav so the silent track guard passes
def write_tone(dest: Path) -> None:
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        frames = bytearray()
        for i in range(16000):
            sample = 8000 if (i // 8) % 2 == 0 else -8000
            frames += int(sample).to_bytes(2, "little", signed=True)
        w.writeframes(bytes(frames))


# stand in for ffmpeg extraction that writes the tone instead
def fake_extract(_video, dest, _track=0):
    write_tone(dest)


# reading the two settings from dotenv files and the environment
class LoadEnvTests(unittest.TestCase):
    # a dotenv value wins over the environment and records its source
    def test_dotenv_wins_over_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text('VIDEO_USE_TRANSCRIBER="local"  # chosen at install\n')
            with patch.dict(os.environ, {"VIDEO_USE_TRANSCRIBER": "elevenlabs"}):
                env = transcribe.load_env([dotenv])
        self.assertEqual(env["VIDEO_USE_TRANSCRIBER"], ("local", ".env"))

    # the environment fills in anything the dotenv files leave out
    def test_environment_fills_gaps(self):
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "k"}, clear=False):
            env = transcribe.load_env([])
        self.assertEqual(env["ELEVENLABS_API_KEY"], ("k", "environment"))

    # a hash inside quotes is part of the value and a bare trailing comment is not
    def test_quoted_hash_is_kept(self):
        self.assertEqual(transcribe.dotenv_value('"abc#def"'), "abc#def")
        self.assertEqual(transcribe.dotenv_value("'abc#def'  # note"), "abc#def")
        self.assertEqual(transcribe.dotenv_value("abc # note"), "abc")
        self.assertEqual(transcribe.dotenv_value("  plain  "), "plain")

    # an empty assignment counts as unset
    def test_empty_value_is_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text("ELEVENLABS_API_KEY=\n")
            with patch.dict(os.environ, {}, clear=True):
                env = transcribe.load_env([dotenv])
        self.assertNotIn("ELEVENLABS_API_KEY", env)


# which engine runs and what gets printed about it
class ResolveEngineTests(unittest.TestCase):
    # the flag beats every setting
    def test_flag_wins(self):
        engine, source = transcribe.resolve_engine("local", env_with(ELEVENLABS_API_KEY="k"))
        self.assertEqual((engine, source), ("local", "--engine"))

    # the setting beats a present key and the announcement says the key is unused
    def test_setting_beats_key_and_is_announced(self):
        env = {"ELEVENLABS_API_KEY": ("k", ".env"), "VIDEO_USE_TRANSCRIBER": ("local", ".env")}
        engine, source = transcribe.resolve_engine(None, env)
        self.assertEqual((engine, source), ("local", ".env"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            transcribe.announce_engine(engine, source, env)
        self.assertIn("engine: local (from .env)", out.getvalue())
        self.assertIn("present but unused", out.getvalue())

    # a key alone selects elevenlabs
    def test_key_alone_selects_elevenlabs(self):
        engine, source = transcribe.resolve_engine(None, env_with(ELEVENLABS_API_KEY="k"))
        self.assertEqual(engine, "elevenlabs")
        self.assertIn("ELEVENLABS_API_KEY", source)

    # nothing configured exits naming both options
    def test_nothing_configured_exits_with_both_options(self):
        with self.assertRaises(SystemExit) as ctx:
            transcribe.resolve_engine(None, {})
        message = str(ctx.exception)
        self.assertIn("ELEVENLABS_API_KEY=", message)
        self.assertIn("VIDEO_USE_TRANSCRIBER=local", message)

    # a bad setting or flag exits before any subprocess runs
    def test_invalid_values_exit_before_work(self):
        with patch.object(transcribe.subprocess, "run", side_effect=AssertionError("ran a subprocess")):
            with self.assertRaises(SystemExit):
                transcribe.resolve_engine(None, env_with(VIDEO_USE_TRANSCRIBER="bogus"))
            with self.assertRaises(SystemExit):
                transcribe.resolve_engine("whisperx", {})
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):
                    transcribe.transcribe_one(Path("clip.mp4"), Path(tmp), engine="bogus")


# a fake http response for the scribe call
class FakeResponse:
    status_code = 200
    text = ""

    # the parsed body scribe would have returned
    def json(self):
        return {"words": [{"type": "word", "text": "Hello", "start": 0.1, "end": 0.5}]}


# caching provenance and the provider boundary
class TranscribeOneTests(unittest.TestCase):
    # patch the media probes so no ffmpeg runs
    def setUp(self):
        self.patches = [
            patch.object(transcribe, "extract_audio", fake_extract),
            patch.object(transcribe, "count_audio_tracks", lambda _video: 1),
        ]
        for item in self.patches:
            item.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.edit_dir = Path(self.tmp.name)
        self.video = Path("clip.mp4")
        self.out = transcribe.transcript_path(self.edit_dir, self.video)

    # undo the patches and remove the temp dir
    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    # with a key the scribe endpoint is called once and the file records the engine
    def test_elevenlabs_path_calls_scribe_once(self):
        with patch.object(transcribe.requests, "post", return_value=FakeResponse()) as post:
            with contextlib.redirect_stdout(io.StringIO()):
                transcribe.transcribe_one(self.video, self.edit_dir, engine="elevenlabs", api_key="k")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["headers"], {"xi-api-key": "k"})
        payload = json.loads(self.out.read_text())
        self.assertEqual(payload["engine"], "elevenlabs")
        self.assertEqual(payload["words"][0]["text"], "Hello")

    # the local path never touches the network and records the engine
    def test_local_path_never_posts(self):
        fake = {"engine": "local", "library": "x", "model": "y", "language_code": "en", "text": "hi", "words": []}
        with patch.object(transcribe.requests, "post", side_effect=AssertionError("posted")), \
             patch.object(transcribe, "preflight_local", lambda _options: "mlx-whisper"), \
             patch.object(transcribe, "call_local", lambda *_args: fake):
            with contextlib.redirect_stdout(io.StringIO()):
                transcribe.transcribe_one(self.video, self.edit_dir, engine="local")
        self.assertEqual(json.loads(self.out.read_text())["engine"], "local")

    # a missing local library stops the run before any audio is extracted
    def test_missing_library_fails_before_extraction(self):
        with patch.object(transcribe, "preflight_local", side_effect=SystemExit("install it")), \
             patch.object(transcribe, "extract_audio", side_effect=AssertionError("extracted")):
            with self.assertRaises(SystemExit):
                transcribe.transcribe_one(self.video, self.edit_dir, engine="local")
        self.assertFalse(self.out.exists())

    # callers from before the engine flag still pass the key as the third positional argument
    def test_legacy_positional_api_key(self):
        with patch.object(transcribe.requests, "post", return_value=FakeResponse()) as post:
            with contextlib.redirect_stdout(io.StringIO()):
                transcribe.transcribe_one(self.video, self.edit_dir, "k", None, None, False, 0)
        self.assertEqual(post.call_args.kwargs["headers"], {"xi-api-key": "k"})
        self.assertEqual(json.loads(self.out.read_text())["engine"], "elevenlabs")

    # a transcript from before the engine key existed is reused without a mismatch note
    def test_old_transcript_without_engine_is_reused(self):
        self.out.parent.mkdir(parents=True)
        original = json.dumps({"words": []})
        self.out.write_text(original)
        out = io.StringIO()
        with patch.object(transcribe, "extract_audio", side_effect=AssertionError("extracted")):
            with contextlib.redirect_stdout(out):
                transcribe.transcribe_one(self.video, self.edit_dir, engine="elevenlabs", api_key="k")
        self.assertEqual(self.out.read_text(), original)
        self.assertIn("cached:", out.getvalue())
        self.assertNotIn("made by", out.getvalue())

    # a cached file from the other engine is reused with an advisory note
    def test_cross_engine_cache_note(self):
        self.out.parent.mkdir(parents=True)
        self.out.write_text(json.dumps({"engine": "local", "words": []}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            transcribe.transcribe_one(self.video, self.edit_dir, engine="elevenlabs", api_key="k")
        self.assertIn("made by local", out.getvalue())
        self.assertIn("--force", out.getvalue())

    # force keeps the old file when the new run fails and replaces it when the run succeeds
    def test_force_replaces_only_on_success(self):
        self.out.parent.mkdir(parents=True)
        original = json.dumps({"engine": "elevenlabs", "words": [{"type": "word", "text": "paid"}]})
        self.out.write_text(original)
        with patch.object(transcribe, "preflight_local", lambda _options: "mlx-whisper"), \
             patch.object(transcribe, "call_local", side_effect=RuntimeError("model crashed")):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    transcribe.transcribe_one(self.video, self.edit_dir, engine="local", force=True)
        self.assertEqual(self.out.read_text(), original)
        self.assertEqual(list(self.out.parent.glob("*.tmp")), [])
        fake = {"engine": "local", "words": []}
        with patch.object(transcribe, "preflight_local", lambda _options: "mlx-whisper"), \
             patch.object(transcribe, "call_local", lambda *_args: fake):
            with contextlib.redirect_stdout(io.StringIO()):
                transcribe.transcribe_one(self.video, self.edit_dir, engine="local", force=True)
        self.assertEqual(json.loads(self.out.read_text())["engine"], "local")


# batch specific behavior
class BatchTests(unittest.TestCase):
    # the local engine runs one file at a time whatever was requested
    def test_local_engine_forces_one_worker(self):
        self.assertEqual(transcribe_batch.worker_count("local", 4), 1)
        self.assertEqual(transcribe_batch.worker_count("elevenlabs", 4), 4)


if __name__ == "__main__":
    unittest.main()
