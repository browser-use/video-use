"""Exercise narration delivery with fake provider responses and real audio encoding."""

import base64
import copy
import json
import subprocess
import sys
import wave

import pytest
import requests

from helpers import narrate


# build a small encoded provider fixture without a network call
@pytest.fixture
def response_audio():
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-ar",
            "44100",
            "-f",
            "mp3",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    alignment = {
        "characters": list("Hello"),
        "character_start_times_seconds": [0.1, 0.2, 0.3, 0.4, 0.5],
        "character_end_times_seconds": [0.2, 0.3, 0.4, 0.5, 0.6],
    }
    return {
        "audio_base64": base64.b64encode(result.stdout).decode(),
        "normalized_alignment": alignment,
        "alignment": {
            "characters": ["X"],
            "character_start_times_seconds": [0],
            "character_end_times_seconds": [0.1],
        },
    }


# install fake credentials and requests while retaining real provider request construction
@pytest.fixture
def provider(monkeypatch, response_audio):
    calls = []
    monkeypatch.setattr(narrate, "load_api_key", lambda: "test-key")
    monkeypatch.setattr(
        narrate, "list_voices", lambda key: [{"voice_id": "test-voice", "name": "Test"}]
    )

    # return a fresh provider payload for each request
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return type(
            "Response",
            (),
            {"status_code": 200, "json": lambda self: copy.deepcopy(response_audio)},
        )()

    monkeypatch.setattr(requests, "post", post)
    return calls


# invoke the public command against a reusable script and output prefix
def invoke(monkeypatch, tmp_path, *flags):
    script = tmp_path / "script.md"
    if not script.exists():
        script.write_text("Hello")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "narrate.py",
            str(script),
            "-o",
            str(tmp_path / "voice"),
            "--voice",
            "test-voice",
            *flags,
        ],
    )
    narrate.main()


# complete output is reusable while missing or changed sidecars cannot pass the cache
def test_generate_and_cache_complete_outputs(monkeypatch, tmp_path, provider):
    invoke(monkeypatch, tmp_path)
    paths = narrate.output_paths(tmp_path / "voice")
    assert all(p.is_file() for p in paths.values())
    with wave.open(str(paths[".wav"])) as handle:
        assert (
            handle.getframerate(),
            handle.getnchannels(),
            handle.getsampwidth(),
        ) == (48000, 1, 2)
    alignment = json.loads(paths[".alignment.json"].read_text())
    assert alignment["audio"] == "voice.wav"
    assert alignment["words"][0]["text"] == "Hello"
    metrics = json.loads(paths[".tts_metrics.json"].read_text())
    assert metrics["peak_dbfs"] > -60
    assert narrate.cache_valid(tmp_path / "voice", metrics["fingerprint"])
    invoke(monkeypatch, tmp_path)
    assert len(provider) == 1
    paths[".srt"].write_text("tampered")
    with pytest.raises(SystemExit, match="complete cache"):
        invoke(monkeypatch, tmp_path)
    paths[".srt"].unlink()
    with pytest.raises(SystemExit, match="complete cache"):
        invoke(monkeypatch, tmp_path)
    assert len(provider) == 1


# changed scripts require explicit overwrite and failed replacements retain prior files
def test_force_failure_preserves_previous_delivery(monkeypatch, tmp_path, provider):
    invoke(monkeypatch, tmp_path)
    paths = narrate.output_paths(tmp_path / "voice")
    before = {suffix: path.read_bytes() for suffix, path in paths.items()}
    (tmp_path / "script.md").write_text("Changed script")
    with pytest.raises(SystemExit, match="complete cache"):
        invoke(monkeypatch, tmp_path)
    monkeypatch.setattr(narrate, "measure_audible", lambda path: {"peak_dbfs": -120})
    with pytest.raises(SystemExit, match="silent"):
        invoke(monkeypatch, tmp_path, "--force")
    assert {suffix: path.read_bytes() for suffix, path in paths.items()} == before


# invalid settings fail before any provider access
@pytest.mark.parametrize(
    "flag,value",
    [
        ("--speed", "nan"),
        ("--stability", "-1"),
        ("--style", "2"),
        ("--similarity", "inf"),
    ],
)
def test_invalid_settings_fail_locally(monkeypatch, tmp_path, provider, flag, value):
    with pytest.raises(SystemExit):
        invoke(monkeypatch, tmp_path, flag, value)
    assert not provider


# script collisions and symlink destinations are rejected even with force
def test_output_protection(monkeypatch, tmp_path, provider):
    script = tmp_path / "script.md"
    script.write_text("Hello")
    (tmp_path / "voice.wav").symlink_to(script)
    with pytest.raises(SystemExit, match="symbolic link"):
        invoke(monkeypatch, tmp_path, "--force")
    assert script.read_text() == "Hello"
    assert not provider


# dry run neither authenticates nor produces files
def test_dry_run_needs_no_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(
        narrate, "load_api_key", lambda: pytest.fail("unexpected authentication")
    )
    invoke(monkeypatch, tmp_path, "--dry-run")
    assert not (tmp_path / "voice.wav").exists()


# concatenation uses decoded durations and whole sample gaps for subsequent word offsets
def test_chunk_offsets_follow_encoded_audio(tmp_path, response_audio):
    chunk = dict(response_audio, _text="Hello")
    words, duration, segments = narrate.assemble(
        [chunk, chunk], tmp_path / "voice", gap_s=0.320001
    )
    gap = round(0.320001 * 48000) / 48000
    assert words[1]["start"] == pytest.approx(segments[0]["end"] + gap + 0.1, abs=0.001)
    with wave.open(str(tmp_path / "voice.wav")) as handle:
        assert duration == pytest.approx(handle.getnframes() / 48000, abs=1 / 48000)
    with pytest.raises(FileExistsError):
        narrate.assemble([chunk], tmp_path / "voice")


# malformed alignment cannot be accepted as a successful provider generation
def test_missing_provider_alignment_rejected(monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **kw: type(
            "Response",
            (),
            {"status_code": 200, "json": lambda self: {"audio_base64": "AAAA"}},
        )(),
    )
    with pytest.raises(ValueError, match="nonempty"):
        narrate.synthesize_chunk(
            "key",
            "voice",
            "Hello",
            model="eleven_multilingual_v2",
            settings={},
            previous_text=None,
            next_text=None,
        )


# provider failures do not echo arbitrary response text or credentials
def test_provider_error_is_redacted(monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **kw: type(
            "Response", (), {"status_code": 403, "text": "private provider payload"}
        )(),
    )
    with pytest.raises(SystemExit) as error:
        narrate.synthesize_chunk(
            "key",
            "voice",
            "Hello",
            model="eleven_multilingual_v2",
            settings={},
            previous_text=None,
            next_text=None,
        )
    assert str(error.value) == "ElevenLabs request failed with HTTP 403"


# generation refuses a source script that would become an output sidecar
def test_script_output_collision(monkeypatch, tmp_path, provider):
    script = tmp_path / "voice.srt"
    script.write_text("Hello")
    monkeypatch.setattr(
        sys,
        "argv",
        ["narrate.py", str(script), "-o", str(tmp_path / "voice"), "--force"],
    )
    with pytest.raises(SystemExit, match="script"):
        narrate.main()
    assert script.read_text() == "Hello"
    assert not provider


# timestamps outside the decoded recording cannot become final narration metadata
def test_alignment_beyond_audio_rejected(tmp_path, response_audio):
    chunk = dict(response_audio, _text="Hello")
    chunk["normalized_alignment"]["character_end_times_seconds"][-1] = 5
    with pytest.raises(ValueError, match="exceeds"):
        narrate.assemble([chunk], tmp_path / "voice")
    assert not (tmp_path / "voice.wav").exists()


# voice name ambiguity is explicit and exact ids remain selectable
def test_voice_resolution(monkeypatch):
    monkeypatch.setattr(
        narrate,
        "list_voices",
        lambda key: [
            {"voice_id": "one", "name": "Alice"},
            {"voice_id": "two", "name": "Alice warm"},
        ],
    )
    assert narrate.resolve_voice("key", "one") == ("one", "Alice")
    with pytest.raises(SystemExit, match="ambiguous"):
        narrate.resolve_voice("key", "Alice")
    with pytest.raises(SystemExit, match="not found"):
        narrate.resolve_voice("key", "Missing")


# explicit replacement produces a new complete cache for changed script settings
def test_successful_force_replaces_cache(monkeypatch, tmp_path, provider):
    invoke(monkeypatch, tmp_path)
    old = json.loads((tmp_path / "voice.tts_metrics.json").read_text())
    invoke(monkeypatch, tmp_path, "--force", "--speed", "1.1")
    new = json.loads((tmp_path / "voice.tts_metrics.json").read_text())
    assert old["fingerprint"] != new["fingerprint"]
    assert narrate.cache_valid(tmp_path / "voice", new["fingerprint"])
    assert len(provider) == 2
