# Narration with word timing

`helpers/narrate.py` converts a supplied script into narration using ElevenLabs.
It is independent of the rendering, mixing and transcription helpers. It requires
Python, the existing requests dependency, and FFmpeg with MP3 decoding and
libmp3lame encoding. Real generation requires an ElevenLabs account and a voice
available to that account.

## Prepare and generate

Set ELEVENLABS_API_KEY in the process environment and choose a voice with --voice
or ELEVENLABS_VOICE_ID. The existing simple root configuration file fallback is
also supported. Do not commit credentials or generated project artifacts.

```sh
python helpers/narrate.py --list-voices
python helpers/narrate.py script.md -o edit/narration --dry-run
python helpers/narrate.py script.md -o edit/narration --voice YOUR_VOICE_ID
```

Dry run parses and counts text without authentication, FFmpeg or API requests.
Its character count and fixed 200 words per minute estimate are planning aids,
not billing or duration guarantees. Generation sends the prepared script to the
provider and may incur charges. Voice names must match exactly one available
name fragment; an exact voice ID avoids ambiguous names.

Heading lines and HTML comments are removed. Blank lines separate paragraphs.
Paragraphs are grouped into requests capped at 4200 characters; split any single
paragraph longer than that. Ordinary Markdown emphasis, links and list syntax
are not rendered into prose automatically, so supply speech-ready text.

For the default eleven_multilingual_v2 model, `[pause 0.6]` requests a 0.6 second
SSML break; `[pause]` requests 0.5 seconds. Supported durations are 0.1 to 3
seconds, and the provider controls the actual delivery. Known expressive tags
are removed for non v3 models. With --model eleven_v3, expressive tags and
untimed `[pause]` remain; numeric pauses and SSML breaks are rejected because
v3 does not support SSML breaks. V3 stability accepts 0, 0.5 or 1, and request
stitching text is omitted. Unknown bracketed text is passed to the provider.
See the official [pause guide](https://elevenlabs.io/docs/help-center/product/core-capabilities/text-to-speech/how-can-i-add-pauses)
and [timestamped speech API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps).

## Output contract

| Suffix | Contents |
| --- | --- |
| .wav | 48 kHz mono signed 16 bit PCM narration |
| .mp3 | The assembled narration encoded for playback |
| .alignment.json | Word text and start/end seconds plus chunk spans and voice settings |
| .srt | Sidecar captions derived from those word timings |
| .provider.json | Original per-chunk audio and character alignment responses |
| .tts_metrics.json | Generation settings, latency, fingerprint and output hashes |

The -o value is a base path: any existing suffix is replaced for each artifact.
Chunks are decoded and separated by 0.32 seconds of digital silence. Subsequent
word timings use the decoded audio duration and exact silence sample count.
Normalized provider alignment is preferred, with original alignment as fallback.
Missing, unordered, nonfinite or zero-duration speech timing is rejected, as are
word timings extending beyond a chunk's decoded duration tolerance of 50 ms.
A peak below -60 dBFS is rejected as effectively silent.

Existing files require either a complete matching cache or explicit --force.
Cache checks include every artifact hash and the script, voice, model and
settings fingerprint. A cache hit still resolves the account voice over the
network but makes no speech generation request. Changed, missing or corrupt
artifacts do not silently count as cached results.

Generation and encoding finish in a temporary directory before publication.
Failures during generation, decoding or silence checks preserve previous output.
The metrics completion marker is written last. Publishing several files is not a
single filesystem transaction: interruption during that step can leave a mixed
set, which will fail cache validation. Avoid concurrent runs using the same base.
--force intentionally replaces that output set, but never a script input or
symbolic-link destination. Use a versioned base to retain prior deliveries.

## Limits and review

This helper does not create a video, mix music, clone a voice, or transcribe
existing recordings. Raw provider timing is retained for inspection; it is not
independent ASR evidence. Listen to generated speech and check the wording and
caption timing before delivery. MP3 playback can introduce encoder delay; use
the WAV as the timing master.

Only complete outputs are cached. Failed multi-request runs do not resume from
individual chunks and rerunning them may repeat paid requests. Provider responses
and decoded audio are held in memory, so avoid unbounded scripts. Word extraction
splits on whitespace; languages without spaces require separate segmentation.
The helper does not guarantee provider pronunciation, timing, voice rights or
creative quality. Automated tests use synthetic audio and mocked API responses;
live service generation and listening remain a separate validation step.
