# Audio mixing and transcript timing

These helpers place existing recordings on a common 48 kHz stereo sample clock.
They do not choose music, stretch speech, synthesize audio, run transcription or
render video. They use FFmpeg, NumPy and SciPy. Shared media IO and sample-clock
conversion come from the Sources helpers.

## Declare an audio mix

Paths in `sources` are relative to the manifest. Start offsets and counts are
integer samples after decoding and resampling to 48 kHz, not the original file's
sample rate. One second is 48,000 samples. A minimal four-second example is:

```json
{
  "fps": 30,
  "total_frames": 120,
  "sources": {
    "music": {"file": "../music.wav", "provenance": "selected recording"},
    "voice": {"file": "../voice.wav", "provenance": "recorded narration"}
  },
  "audio": [
    {"id": "bed", "role": "music", "source": "music", "source_start_sample": 0,
     "start_sample": 0, "sample_count": 192000, "gain_db": -12},
    {"id": "speech", "role": "voice", "source": "voice", "source_start_sample": 0,
     "start_sample": 48000, "sample_count": 96000}
  ],
  "delivery": {"lufs": -14, "true_peak": -1.5}
}
```

```sh
python helpers/mix_audio.py /footage/edit/audio.json --out-dir /footage/edit/mix-v1
```

The mixer derives the total sample count from `total_frames` and `fps` (30 if
omitted). Fractional rates may be given as strings such as `30000/1001`. Clips must
fit within that duration; each needs a unique id, a role of `voice`, `music` or
`effects`, nonnegative integer starts and a positive sample count. Render sources
must have provenance and must not be marked for reference-only study.

## Gain and filters

`gain_db` adjusts the whole clip. `gain_points` adds gain automation using pairs
of clip-relative sample offsets and decibels, interpolated in decibel space:
`[[0, -12], [48000, -6]]`. Points must begin at zero and use unique ascending offsets.
This lets the editor explicitly lower music under speech; there is no automatic
ducking or beat-aware adjustment.

Voice clips default to 1,440-sample (30 ms) fades at each edge. Music and effects
have no default edge fades. Override with `fade_in_samples` and `fade_out_samples`;
a fade cannot exceed the clip's sample count. Very short voice clips need explicit
shorter fades. Overlapping fades multiply, and overlapping clips add together.
Picture-cut boundaries never introduce music fades.

Optional `filters` support highpass/lowpass with `frequency_hz`, or equalizer with
`frequency_hz`, `q` and `gain_db`. Rate-changing filters such as atempo are rejected.
Filters are applied after resampling and sample trimming; they preserve duration
but may change waveform phase and have startup transients at clip boundaries.

## Outputs and loudness

Each run writes `voice.wav`, `music.wav`, `effects.wav`, an unnormalized `mix.wav`,
a normalized `master.wav` and `mix_report.json`. All WAVs use 48 kHz stereo float
PCM. The report contains clip ranges, per-second stem energy and both loudness
normalization measurements. The helper applies FFmpeg loudnorm in two passes;
LUFS describes integrated loudness and true peak limits reconstructed peak level.

Choose a fresh output location: existing mix artifacts are rejected, including
filesystem aliases, to protect recordings and reviewed outputs. There is no
transaction across all files; a failed normalization can leave unnormalized stems.
Silent material cannot be loudness-normalized. Path checks assume one writer;
they are not protection against concurrent filesystem changes.

Decoded clips and three complete timeline buses are retained in memory. Long
projects can consume substantial RAM. Raw stems and the unnormalized float mix
may exceed full-scale amplitude; use the normalized master for delivery and listen
for unwanted effects. Normalization does not repair distortion already in a source.

## Map words without changing speech

A clip JSON contains the same timing fields as one voice clip above. Supply a
word-level transcript with `text`, `start` and `end` timestamps in seconds:

```sh
python helpers/map_transcript.py map /footage/edit/transcript.json /footage/edit/voice-clip.json \
  --source voice --prefix speech_ --out /footage/edit/words.json
```

Words outside the clip are excluded. Any trim through an overlapping word fails
instead of silently shortening the caption. Each retained word records its original
sample interval and translated output interval. This assumes unchanged speech speed.
Use distinct prefixes when combining mappings from several clips.

For a final transcript comparison, supply a manifest with `words` containing those
mapped entries and `audio` listing its voice clips:

```sh
python helpers/map_transcript.py compare /footage/edit/plan.json /footage/edit/final-asr.json \
  --out /footage/edit/speech-review.json
```

Comparison ignores case/punctuation and reports matching-word onset differences
plus replacements, insertions and deletions. Only observations whose start lies
inside a declared voice interval are considered. Supply chronological ASR words.
A disagreement is a review candidate, not proof that the edit is wrong. Listen to
the recording; this helper does not run ASR or claim its timestamps are exact.

## Tests

```sh
python -m pytest tests/test_audio_tracks.py tests/test_mix_audio.py tests/test_map_transcript.py
```

Tests cover exact sample extraction, gain and fades, EQ effects, independent stems,
real loudness normalization, frame-rate duration, source preservation, intact word
mapping and explicit transcript disagreement. Audio tests skip when FFmpeg is absent.
