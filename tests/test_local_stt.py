"""unit tests for the local whisper engine in helpers local_stt py
they cover library choice install messages pinned downloads word normalization the hallucination guards and the payload contract
no model is loaded and no network call is made
"""

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


# load a helper straight from its file path under a private module name
def load_helper(name: str, alias: str):
    path = Path(__file__).parents[1] / "helpers" / name
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


local_stt = load_helper("local_stt.py", "video_use_local_stt")
render = load_helper("render.py", "video_use_render_for_stt")
pack_transcripts = load_helper("pack_transcripts.py", "video_use_pack_for_stt")


# a probe result for a given machine shape
def fake_probe(apple_silicon: bool, cuda: bool = False) -> dict:
    return {"apple_silicon": apple_silicon, "cuda": cuda, "installed": {}}


# library selection follows the machine and only a cli override changes it
class ChooseLibraryTests(unittest.TestCase):
    # apple silicon gets mlx whisper
    def test_apple_silicon_uses_mlx(self):
        self.assertEqual(local_stt.choose_library(fake_probe(True)), "mlx-whisper")

    # a cpu only linux box gets faster whisper
    def test_cpu_uses_faster_whisper(self):
        self.assertEqual(local_stt.choose_library(fake_probe(False)), "faster-whisper")

    # a cuda box also gets faster whisper which handles the gpu itself
    def test_cuda_uses_faster_whisper(self):
        self.assertEqual(local_stt.choose_library(fake_probe(False, cuda=True)), "faster-whisper")

    # the override wins over the probe
    def test_override_wins(self):
        self.assertEqual(local_stt.choose_library(fake_probe(True), "faster-whisper"), "faster-whisper")

    # an unknown override exits with the valid names
    def test_unknown_override_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            local_stt.choose_library(fake_probe(True), "whisperx")
        self.assertIn("mlx-whisper", str(ctx.exception))


# missing libraries and pinned downloads
class InstallAndModelTests(unittest.TestCase):
    # the install command names the extra for the library and the cuda extra when a gpu is present
    def test_install_command_names_extra(self):
        self.assertIn("stt-mlx", local_stt.install_command("mlx-whisper"))
        self.assertIn("stt-cpu", local_stt.install_command("faster-whisper"))
        self.assertIn("stt-cuda", local_stt.install_command("faster-whisper", cuda=True))
        self.assertIn("stt-mlx", local_stt.install_command("mlx-whisper", cuda=True))

    # without ctranslate2 or a visible device the cuda check is false instead of raising
    def test_cuda_available_is_false_without_runtime(self):
        with patch.dict(sys.modules, {"ctranslate2": None}):
            self.assertFalse(local_stt.cuda_available())
        fake = types.SimpleNamespace(get_cuda_device_count=lambda: 0)
        with patch.dict(sys.modules, {"ctranslate2": fake}):
            self.assertFalse(local_stt.cuda_available())
        fake = types.SimpleNamespace(get_cuda_device_count=lambda: 1)
        with patch.dict(sys.modules, {"ctranslate2": fake}):
            self.assertTrue(local_stt.cuda_available())

    # a missing library exits with the install command instead of a traceback
    def test_require_library_exits_with_install_command(self):
        # raise the way a missing package would
        def missing(_name):
            raise ImportError("no module")

        with patch.object(local_stt.importlib, "import_module", missing):
            with self.assertRaises(SystemExit) as ctx:
                local_stt.require_library("faster-whisper")
            with self.assertRaises(SystemExit) as gpu:
                local_stt.require_library("faster-whisper", cuda=True)
        self.assertIn("uv sync --extra stt-cpu", str(ctx.exception))
        self.assertIn("uv sync --extra stt-cuda", str(gpu.exception))

    # the preflight probes and imports once per process and hands back the same library afterwards
    def test_preflight_settles_once(self):
        calls = []

        # count how often the probe runs
        def counting_probe():
            calls.append(1)
            return fake_probe(False, cuda=True)

        with patch.object(local_stt, "probe", counting_probe), \
             patch.object(local_stt, "require_library", lambda _lib, _cuda: None), \
             patch.dict(local_stt._PREFLIGHTED, {}, clear=True):
            first = local_stt.preflight()
            second = local_stt.preflight()
        self.assertEqual((first, second), ("faster-whisper", "faster-whisper"))
        self.assertEqual(len(calls), 1)

    # the default model is downloaded at its pinned revision and the label records it
    def test_ensure_model_pins_revision(self):
        calls = []

        # stand in for huggingface_hub snapshot_download
        def snapshot_download(**kwargs):
            calls.append(kwargs)
            return "/tmp/fake-snapshot"

        fake_hub = types.SimpleNamespace(snapshot_download=snapshot_download)
        with patch.dict(sys.modules, {"huggingface_hub": fake_hub}):
            path, label = local_stt.ensure_model("mlx-whisper")
        expected = local_stt.MODELS["mlx-whisper"]
        self.assertEqual(calls[0]["repo_id"], expected["repo"])
        self.assertEqual(calls[0]["revision"], expected["revision"])
        self.assertEqual(path, Path("/tmp/fake-snapshot"))
        self.assertTrue(label.startswith(expected["repo"] + "@"))

    # a local model directory is used as is without any download
    def test_ensure_model_accepts_local_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_hub = types.SimpleNamespace(snapshot_download=lambda **_: self.fail("downloaded"))
            with patch.dict(sys.modules, {"huggingface_hub": fake_hub}):
                path, label = local_stt.ensure_model("faster-whisper", tmp)
        self.assertEqual(path, Path(tmp).resolve())
        self.assertEqual(label, str(Path(tmp).resolve()))


# normalizing library output into the canonical word list
class WordsFromSegmentsTests(unittest.TestCase):
    # words keep their order carry the canonical fields and lose surrounding whitespace
    def test_canonical_shape(self):
        segments = [
            {"no_speech_prob": 0.1, "words": [
                {"text": " Hello", "start": 0.5, "end": 0.9},
                {"text": " world.", "start": "1.0", "end": "1.4"},
            ]},
        ]
        words = local_stt.words_from_segments(segments)
        self.assertEqual([w["text"] for w in words], ["Hello", "world."])
        for word in words:
            self.assertEqual(word["type"], "word")
            self.assertIsInstance(word["start"], float)
            self.assertIsInstance(word["end"], float)
            self.assertIsNone(word["speaker_id"])

    # empty text missing times and zero length words are dropped
    def test_drops_unusable_words(self):
        segments = [
            {"no_speech_prob": 0.0, "words": [
                {"text": "  ", "start": 0.0, "end": 0.2},
                {"text": "no-start", "start": None, "end": 0.2},
                {"text": "zero", "start": 1.0, "end": 1.0},
                {"text": "keep", "start": 2.0, "end": 2.3},
            ]},
        ]
        words = local_stt.words_from_segments(segments)
        self.assertEqual([w["text"] for w in words], ["keep"])

    # a segment whisper marks as silence contributes nothing
    def test_drops_silent_segments(self):
        segments = [
            {"no_speech_prob": 0.95, "words": [{"text": "Thanks", "start": 0.0, "end": 0.4}]},
            {"no_speech_prob": 0.2, "words": [{"text": "real", "start": 1.0, "end": 1.3}]},
        ]
        words = local_stt.words_from_segments(segments)
        self.assertEqual([w["text"] for w in words], ["real"])


# the guards that remove decoder artifacts
class CleanWordsTests(unittest.TestCase):
    # a word longer than any spoken word is an artifact
    def test_drops_overlong_words(self):
        words = [
            {"type": "word", "text": "ok", "start": 0.0, "end": 0.3, "speaker_id": None},
            {"type": "word", "text": "stuck", "start": 0.3, "end": 9.0, "speaker_id": None},
        ]
        self.assertEqual([w["text"] for w in local_stt.clean_words(words)], ["ok"])

    # a repetition loop is capped at the allowed run length
    def test_collapses_repetition_loops(self):
        words = [
            {"type": "word", "text": "the", "start": i * 0.2, "end": i * 0.2 + 0.1, "speaker_id": None}
            for i in range(10)
        ]
        kept = local_stt.clean_words(words)
        self.assertEqual(len(kept), local_stt.MAX_REPEATS)

    # distinct words in a non latin script are never mistaken for a repetition loop
    def test_non_latin_words_are_kept(self):
        texts = ["これ", "は", "テスト", "です", "ね", "Привет", "мир"]
        words = [
            {"type": "word", "text": t, "start": i * 0.3, "end": i * 0.3 + 0.2, "speaker_id": None}
            for i, t in enumerate(texts)
        ]
        self.assertEqual([w["text"] for w in local_stt.clean_words(words)], texts)
        repeated = [
            {"type": "word", "text": "はい", "start": i * 0.3, "end": i * 0.3 + 0.2, "speaker_id": None}
            for i in range(10)
        ]
        self.assertEqual(len(local_stt.clean_words(repeated)), local_stt.MAX_REPEATS)

    # a run of words that repeats the prompt is removed and everything else stays
    def test_removes_prompt_echo(self):
        prompt = "Umm, let me think"
        texts = ["Hi", "Umm,", "let", "me", "think", "again", "later"]
        words = [
            {"type": "word", "text": t, "start": i * 0.3, "end": i * 0.3 + 0.2, "speaker_id": None}
            for i, t in enumerate(texts)
        ]
        kept = local_stt.clean_words(words, prompt)
        self.assertEqual([w["text"] for w in kept], ["Hi", "again", "later"])


# moving word starts to the first audible frame
class TrimOnsetsTests(unittest.TestCase):
    # a word that begins with silence starts where the energy rises
    def test_leading_silence_is_trimmed(self):
        energy = [0.0] * 50 + [1000.0] * 30
        words = [{"type": "word", "text": "hi", "start": 0.0, "end": 0.8, "speaker_id": None}]
        trimmed = local_stt.trim_onsets(words, energy)
        self.assertAlmostEqual(trimmed[0]["start"], 0.5)
        self.assertEqual(trimmed[0]["end"], 0.8)
        self.assertEqual(words[0]["start"], 0.0)

    # a word that is loud from its first frame keeps its start
    def test_loud_word_is_unchanged(self):
        energy = [900.0] * 80
        words = [{"type": "word", "text": "hi", "start": 0.1, "end": 0.6, "speaker_id": None}]
        self.assertEqual(local_stt.trim_onsets(words, energy)[0]["start"], 0.1)

    # the start never crosses the minimum duration before the end and never moves backward
    def test_start_is_bounded(self):
        energy = [0.0] * 79 + [1000.0]
        words = [{"type": "word", "text": "hi", "start": 0.2, "end": 0.8, "speaker_id": None}]
        self.assertAlmostEqual(local_stt.trim_onsets(words, energy)[0]["start"], 0.8 - local_stt.ONSET_MIN_KEEP)
        silent = [0.0] * 80
        self.assertEqual(local_stt.trim_onsets(words, silent)[0]["start"], 0.2)

    # frame energy of a synthetic wav has one value per ten milliseconds and is zero in silence
    def test_frame_energy_reads_wav(self):
        import wave

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tone.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                silence = b"\x00\x00" * 1600
                tone = b"".join((5000 if (i // 8) % 2 == 0 else -5000).to_bytes(2, "little", signed=True) for i in range(1600))
                handle.writeframes(silence + tone)
            energy = local_stt.frame_energy(path)
        self.assertEqual(len(energy), 20)
        self.assertEqual(float(energy[0]), 0.0)
        self.assertGreater(float(energy[-1]), 1000.0)

    # anything but mono sixteen bit pcm is rejected instead of producing wrong energies
    def test_frame_energy_rejects_other_formats(self):
        import wave

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stereo.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(2)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(b"\x00\x00" * 3200)
            with self.assertRaises(ValueError):
                local_stt.frame_energy(path)

    # chunked reading gives the same frames as one pass over a file longer than a single read
    def test_frame_energy_chunks_match(self):
        import wave

        import numpy as np

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.wav"
            rng = np.random.default_rng(0)
            samples = (rng.standard_normal(16000 * 3) * 3000).astype(np.int16)
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(samples.tobytes())
            with patch.object(local_stt, "ENERGY_READ_FRAMES", 7):
                energy = local_stt.frame_energy(path)
        expected = np.sqrt(np.mean(samples[: 300 * 160].reshape(300, 160).astype(np.float32) ** 2, axis=1))
        self.assertEqual(len(energy), 300)
        self.assertTrue(np.allclose(energy, expected))


# when the verbatim prompt applies
class VerbatimPromptTests(unittest.TestCase):
    # english and auto detect both get the prompt
    def test_english_and_auto_get_prompt(self):
        self.assertEqual(local_stt.verbatim_prompt_for("en", True), local_stt.VERBATIM_PROMPT)
        self.assertEqual(local_stt.verbatim_prompt_for(None, True), local_stt.VERBATIM_PROMPT)

    # other languages and the off switch get none
    def test_other_languages_and_off_get_none(self):
        self.assertIsNone(local_stt.verbatim_prompt_for("de", True))
        self.assertIsNone(local_stt.verbatim_prompt_for("en", False))


# the full local path with a fake runner
class TranscribeWavTests(unittest.TestCase):
    # a non english detection repeats the pass without the english prompt
    def test_redoes_non_english_without_prompt(self):
        calls = []

        # fake runner that reports german the first time
        def runner(wav, model_path, language, prompt):
            calls.append((language, prompt))
            segments = [{"no_speech_prob": 0.0, "words": [{"text": "Hallo", "start": 0.0, "end": 0.4}]}]
            return segments, "de"

        with patch.object(local_stt, "probe", lambda: fake_probe(True)), \
             patch.object(local_stt, "require_library", lambda _lib, _cuda: None), \
             patch.object(local_stt, "ensure_model", lambda _lib, _model: (Path("/m"), "repo@abc")), \
             patch.object(local_stt, "frame_energy", lambda _wav: [1.0] * 100), \
             patch.dict(local_stt._PREFLIGHTED, {}, clear=True), \
             patch.dict(local_stt.RUNNERS, {"mlx-whisper": runner}):
            payload = local_stt.transcribe_wav(Path("clip.wav"))
        self.assertEqual(calls, [(None, local_stt.VERBATIM_PROMPT), ("de", None)])
        self.assertEqual(payload["engine"], "local")
        self.assertEqual(payload["library"], "mlx-whisper")
        self.assertEqual(payload["model"], "repo@abc")
        self.assertEqual(payload["language_code"], "de")
        self.assertEqual(payload["text"], "Hallo")

    # the payload satisfies the strictest consumers on this branch
    def test_payload_passes_render_and_packer(self):
        segments = [{"no_speech_prob": 0.0, "words": [
            {"text": " Hello", "start": 0.1, "end": 0.5},
            {"text": " there", "start": 0.6, "end": 0.9},
            {"text": " friend", "start": 2.0, "end": 2.4},
        ]}]
        words = local_stt.clean_words(local_stt.words_from_segments(segments))
        payload = local_stt.build_payload("faster-whisper", "repo@abc", "en", words)
        self.assertEqual(len(render._words_in_range(payload, 0.0, 3.0)), 3)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.json"
            path.write_text(json.dumps(payload))
            _name, _duration, phrases = pack_transcripts.pack_one_file(path, 0.5)
        self.assertEqual([p["text"] for p in phrases], ["Hello there", "friend"])


if __name__ == "__main__":
    unittest.main()
