# Explicit composition rendering

`helpers/render.py` accepts numeric EDL version 3 for an explicit picture, audio
and caption timeline. EDL v1/v2 retains its existing renderer and deliverable
options. A composition uses 30 fps picture frames and 48 kHz stereo audio samples;
one picture frame spans 1600 audio samples. Other composition frame rates are
rejected before staging. Legacy `music-story-edit/1` inputs remain accepted.

This change connects the source, mixing, caption and effect helpers. It adds no
media assets, reference-derived profiles, fonts or separate editing application.
Install the existing `editing` extra for OpenCV. FFmpeg, ffprobe, Pillow, NumPy
and SciPy are required. RAQM is required only when the manifest requests it.

## Author the timeline

Start with inspected footage and declare each source's provenance. The following
example requires at least two seconds of picture and non-silent audio in clip.mp4:

```json
{
  "version": 3,
  "fps": 30,
  "total_frames": 60,
  "canvas": [320, 180],
  "picture": [0, 0, 320, 180],
  "font_layout": "basic",
  "fonts": {},
  "sources": {"clip": {"file": "clip.mp4", "provenance": "supplied project footage"}},
  "shots": [{"id": "opening", "source": "clip", "source_frame": 0,
             "start_frame": 0, "end_frame": 60,
             "beat": "introduction", "reason": "show the selected opening action"}],
  "audio": [{"id": "original", "source": "clip", "role": "voice",
             "start_sample": 0, "source_start_sample": 0, "sample_count": 96000}],
  "words": {},
  "cards": [],
  "layers": []
}
```

Picture intervals are half open and shots must cover the timeline exactly once.
Select source origins by seconds or native frame index, never both. Explicit
forward time maps select native source frames without invented interpolation.
Sources marked study_only, including aliases of declared study media, cannot be
rendered. HDR inputs require explicit preparation before SDR composition.

Audio clips are independent of picture cuts. Specify source and output sample
ranges; their role is voice, music or effects. Use the shared
[audio mixing](audio-mixing.md) contract for gain, fades and filters. This delivery
path requires declared non-silent audio: empty audio is rejected before staging,
and a silent mix cannot be loudness-normalized. It is not yet a silent-only video
export path. Voice source speed and sample placement must be authored explicitly.

Caption cards link to source-backed word IDs with output/source sample timestamps.
Displayed wording must match those words unless an explicit display_normalization
explains the difference. Non-speech titles should be authored image layers rather
than invented speech. Use [caption rendering](caption-rendering.md) for supported
font, line and mask fields and [effects](effects.md) for layers and transforms.
The renderer currently requires 30 fps even though individual helpers can encode
other rates. Validate the full composition before spending time on an encode.

## Render and inspect

```sh
python helpers/cut_list.py edit/edl.json
python helpers/render.py edit/edl.json -o edit/final.mp4
python helpers/verify_edit.py edit/edl.json edit/final.mp4 \
  --work-dir edit/final_build --out edit/final-review.json --sheets edit/review
```

Use a new output name and build directory. Source media, font files, masks and
the manifest must be outside the generated directory. Existing outputs and
verification reports are rejected. Failed renders can leave partial artifacts;
inspect them and retry with a new versioned output after correcting the problem.

The build directory holds the captured manifest, source hashes, picture stages,
audio stems/master, command logs and verification.json. Captions are composited
after picture transforms and foreground layers. Audio is mixed independently and
muxed after picture assembly. Source chapters are removed from picture stages.

Verification measures decoded frame count, geometry, frame timestamps, audio
format and duration, AAC tail padding, loudness, true peak and complete decoding.
With a build directory, it compares decoded audio to the master and checks any
declared music-presence intervals against the music stem. Missing master evidence
fails the requested alignment check. Caption schedule checks compare manifest
entry frames with word timestamps; they are not OCR of the final video.
A timing_exception records an intentional offset for human review.

A technical pass leaves visual and listening review pending. Inspect motion,
caption readability, masks and source choices, and listen to the delivered mix.
The report cannot prove source ownership, transcription accuracy or creative quality.

## Legacy renderer additions

For EDL v1/v2, this change connects static reframe/canvas treatments, graphic layers
and PIL subtitle rendering through the existing command. Preview scaling adjusts
pixel-based layouts while preserving fractional placement. The existing ASS
interface remains available through helpers/captions.py alongside the raster API.

Composition version 3 rejects legacy CLI switches such as --preview, --draft,
--fps, --no-subtitles, --no-loudnorm and deliverable selectors; author its timeline
and delivery settings in the manifest instead. This PR does not solve legacy
segment-based caption drift, copied AAC joins or unrelated renderer proposals.
The separate montage command and specialized workflow skills remain outside this
submission.
