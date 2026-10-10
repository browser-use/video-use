# CLAUDE.md

## TRANSCRIPTION RULES

Use local `whisper-cli` (whisper.cpp) for all transcription. Never use ElevenLabs unless the user specifically requests it.

Model path comes from `WHISPER_MODEL`, defaulting to `~/whisper-models/ggml-large-v3-turbo.bin`. Set it in `.env` at the repo root if your model lives elsewhere — never hardcode a machine-specific absolute path in a committed file.

`whisper-cli` only accepts wav, mp3, flac and ogg, so extract audio from video first.

Transcribe command:

```bash
# Resolve once per session; falls back to the conventional location.
WHISPER_MODEL="${WHISPER_MODEL:-$HOME/whisper-models/ggml-large-v3-turbo.bin}"

ffmpeg -y -i input.mp4 -ar 16000 -ac 1 audio.wav
whisper-cli -m "$WHISPER_MODEL" -l auto -ml 1 --split-on-word -f audio.wav -oj --output-file transcript
```

- Language: `-l auto` (auto-detect; the model supports Hindi and English).
- Output: `transcript.json` with timestamps.
- `-ml 1 --split-on-word` is required, not optional: it emits one segment per word. Without it you get phrase-level segments, which loses the sub-second gap data that `SKILL.md` Hard Rule 8 and the cut-edge snapping depend on.
- **Convert before packing.** `whisper-cli -oj` writes `{"transcription": […]}` with millisecond `offsets`; `pack_transcripts.py` expects Scribe's `{"words": […]}` with seconds. Feeding whisper JSON straight into the packer yields `_no speech detected_` — a silent failure, not an error. Always run:

    ```bash
    python helpers/whisper_to_scribe.py transcript.json
    # writes transcript.words.json into edit/transcripts/
    ```

- Local whisper has **no speaker diarization and no audio events**. When a session needs either, that's the one case to ask the user before falling back to hosted Scribe.
