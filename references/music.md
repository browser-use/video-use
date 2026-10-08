# Local background music synthesis

`helpers/music_bed.py` creates a simple four-chord ambient loop using NumPy.
It combines detuned sine pads, a bass pulse and seeded noise ticks, with
alternating minor and major chord shapes. It requires no API, network request,
model, sampled recording, external instrument or bundled audio asset.

```sh
python helpers/music_bed.py -o edit/assets/bed.wav
python helpers/music_bed.py -o edit/assets/bed_v2.wav \
  --bpm 120 --key 57 --progression=0,-4,-9,-2 \
  --bars-per-chord 1 --seed 3 --peak-dbfs -18
```

The output is mono 48 kHz signed 16 bit PCM WAV. Use a new filename; existing
files and symbolic links are rejected. The complete WAV is staged in the same
directory and published through an exclusive hard link, so publication requires
a filesystem that supports hard links. A failed encode does not publish a partial
WAV, and an existing destination cannot be overwritten during publication.

## When to choose this helper

Use this helper when the user wants a simple ambient backing track generated
locally without external recordings or paid services. For example, it can provide
quiet background music beneath narration when that procedural style fits the brief.

Do not choose it for a specific song, a broad genre request, or polished custom
composition. It produces the fixed ambient style described above. A request for
background music alone does not establish a preference for this sound; select it
only when it fits the agreed direction. Listen to the loop before using it, then
place and level the WAV through a separate mixing step.

## Settings and timing

- bpm: 50 to 160, including fractional tempos.
- key: integer MIDI note for the root, for example 57 is A3.
- progression: exactly four integer semitone offsets from that root. Every
  resulting chord root must be between MIDI 0 and 115 so the octave fits.
- bars-per-chord: 1 to 8, with four beats per bar.
- seed: integer from zero through 2 to the power 64 minus one.
- peak-dbfs: finite sample-peak target from -60 to 0 dBFS; default -12.

Duration is `60 / bpm * 4 * bars_per_chord * 4`, rounded to the nearest
48 kHz sample. The Python synthesize function returns the samples and their
actual encoded duration. Chord and beat boundaries are placed on that sample
clock rather than accumulating rounded segment durations.

The same settings and seed produce the same output in the tested environment.
Bit-identical output across NumPy versions or platforms is not guaranteed.
The helper holds the whole loop in memory; the settings limit duration to at
most 153.6 seconds. It has no arbitrary-duration or streaming export mode.

## Mixing and review

Place, repeat or trim the resulting file in the existing audio mixer or editor.
This helper does not perform dialogue ducking, beat analysis, loudness
normalization or final video assembly. Those remain separate operations.

Short five millisecond fades make the first and last samples zero to remove a
wrap discontinuity. That numerical check does not prove the musical transition
is seamless. Listen across repeats and check the pad transitions and balance.
The sound is a fixed procedural ambient style, not general music generation.

The peak setting controls digital sample amplitude, not perceived loudness or
encoded true peak. Leave headroom when mixing. WAV quantization slightly changes
the measured peak, especially at very quiet settings. Tests check reproducibility,
finite audio, duration, loop endpoints, PCM encoding and output protection;
listening review remains pending.
