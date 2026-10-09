# CLAUDE.md

## TRANSCRIPTION RULES

Use local `whisper-cli` (whisper.cpp) for all transcription. Never use ElevenLabs unless the user specifically requests it.

Model path: `/Users/rajesh/whisper-models/ggml-large-v3-turbo.bin`

`whisper-cli` only accepts wav, mp3, flac and ogg, so extract audio from video first.

Transcribe command:

```bash
ffmpeg -y -i input.mp4 -ar 16000 -ac 1 audio.wav
whisper-cli -m /Users/rajesh/whisper-models/ggml-large-v3-turbo.bin -l auto -f audio.wav -oj --output-file transcript
```

- Language: `-l auto` (auto-detect; the model supports Hindi and English).
- Output: `transcript.json` with timestamps.
