# Audio analysis

Use measured audio to guide cuts and authored animation. These tools produce
analysis JSON; they do not edit audio, mix music, generate speech, choose scenes
or render animation. They use the base NumPy, SciPy and librosa dependencies.
FFmpeg is required for recording comparison and motion-audio decoding.

Keep reports under the footage directory's `edit/` folder. Listen to the material
before accepting measured beat, accent or recording-match candidates.

## Measure song timing

```sh
python helpers/song_scan.py /footage/music.wav --accents -o /footage/edit/song.json
```

`scan` returns estimated tempo, beat timestamps, median beat interval, per-second
RMS energy, and candidate energy rises. Optional `accents` are positive spectral
changes in low, mid and high frequency bands. They do not identify a kick, snare
or other instrument. Estimated beats can be half or double the perceived tempo;
energy rises are not proof of a musical drop. Silence produces no beat candidates.

This analysis loads the entire recording as 22.05 kHz mono audio. Work on a bounded
source when memory or analysis time matters. Input decoding support depends on the
installed librosa audio backend. Use a compatible WAV working copy if needed.
The helper may create a temporary Numba cache directory when none is configured.

## Compare supplied recordings

Create a JSON catalog with file paths relative to the catalog file:

```json
[
  {"id": "candidate-a", "file": "music-a.wav", "title": "Recording A"},
  {"id": "candidate-b", "file": "music-b.wav", "title": "Recording B"}
]
```

```sh
python helpers/identify_track.py /footage/excerpt.wav /footage/catalog.json --out /footage/edit/matches.json
```

`rank` decodes the first audio stream to 8 kHz mono and compares each supplied
candidate with the query. Results report a normalized waveform correlation and
sample/second offset. Normalization tolerates gain and constant DC offset; it
does not promise invariance to speed changes, remixes, pitch changes or dialogue
mixed over the recording. A high score identifies correspondence, not source rights.
Verify separate excerpts and the recording version before accepting a match.

The CLI compares only the first ten minutes of each input. The query must fit
within each candidate's decoded window. Invalid, silent or overlong queries raise
an error; one invalid candidate currently stops the ranking. The lower-level
`decode` function accepts a different bounded window up to one hour. The report
cannot replace the query, catalog, candidate recordings or their filesystem aliases.

## Export motion controls

```sh
python helpers/motion_audio.py /footage/interview.mp4 -o /footage/edit/voice.json \
  --start 12 --duration 8 --offset 4 --bands voice:250:2000,air:4000:10000
```

This analyzes source seconds 12–20 and positions measurements at timeline seconds
4–12. `--start` trims input; `--offset` shifts output timestamps. Default analysis
uses 24 kHz mono decoding, 2048-sample Hann windows, and 60 analysis frames per
second. Custom bands must fit below the sample rate's Nyquist frequency and contain
at least one FFT bin. `--bands none` omits band controls.

Schema version 1 records duration, sample/analysis rates, window settings, timestamp
convention, smoothing, normalization scales and source identity. Each frame contains:

- `time`: timeline seconds at the center of a zero-padded analysis window.
- `rms` and `peak`: normalized energy and amplitude.
- `envelope`: RMS smoothed with configurable attack and release.
- `onset`: positive spectral change, not a beat or BPM estimate.
- `bands`: independently normalized energy per frequency range.
- `raw`: unnormalized RMS, peak and band energy.

Normalization uses one percentile per feature over the selected recording, not a
separate normalization at every frame. Quiet passages remain relatively quiet and
silence remains zero. Values are clipped to 0–1. Compare raw band measurements when
judging which band dominates; independently normalized bands are not comparable.

Window centers can place energy before the physical attack by up to half a window.
Use smaller windows or an intentional timeline offset when precise sync matters,
and verify against the actual soundtrack. Mono downmixing can cancel opposite-phase
stereo content. Decoding and analysis retain the selected interval in memory;
use `--start` and `--duration` for long recordings.

An animation consumer must map these measurements to its own visual properties.
This PR does not include that consumer or prescribe a visual style.

## Tests

```sh
python -m pytest tests/test_song_scan.py tests/test_identify_track.py tests/test_motion_audio.py
```

Synthetic silence, pulses, known frequencies and known excerpt offsets provide
independent expectations. Tests also exercise actual decoding, timing offsets,
invalid inputs, and protection against overwriting input aliases. These checks
establish signal behavior rather than subjective musical or visual quality.
