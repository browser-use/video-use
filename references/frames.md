# Frame review

Use contact sheets to compare selected moments and native PNGs to inspect details.
These helpers depend on the source catalog and decoder in `source_scan.py`.
FFmpeg and ffprobe must be on PATH; Pillow and NumPy are base dependencies.
OpenCV is not required for frame review.

## Inspect native frame indices

```sh
python helpers/sheet.py /footage/source.mp4 --frames 0 29 89 -o /footage/edit/frames.jpg
```

Frame indices are zero-based decoded frames. The contact sheet labels each image
with its native index and original presentation timestamp (PTS). A JSON sidecar
records the source fingerprint and selected frame metadata. Thumbnails default to
320 pixels wide, with five columns and at most six rows per page. Additional pages
use numbered suffixes.

For exploratory sampling, omit `--frames` and use `--every 1` to sample roughly
every second according to the source timestamps. This mode creates thumbnail
sheets; it does not retain native PNGs or use the review cache.

## Review critical times at full resolution

```sh
python helpers/sheet.py /footage/final.mp4 --times 0 2.5 7 -o /footage/edit/review
```

Choose moments that need inspection, such as a title entering or an overlay
crossing a face. Times are seconds relative to the first decoded video timestamp.
Each request selects the first native frame at or after that time. Duplicate frame
selections collapse into one image. A time after the last decoded frame is rejected,
even if it is below the container's reported duration.

The review directory contains:

- `frame_000000.png` and similar files at native decoded resolution.
- `sheet_001.jpg` and later pages of labeled thumbnails.
- `review.json` recording request settings, source identity, selected frames and
  artifact fingerprints.

At most 120 times may be requested per review. Thumbnail widths range from 32 to
1920 pixels, with one to ten columns. Native PNGs remain full size regardless of
the thumbnail width. `--times` and `--frames` cannot be combined.

## Reuse only intact evidence

Repeating the same review skips metadata probing and video decoding when the
source bytes, requested times, thumbnail geometry and every saved artifact still
match their fingerprints. Reordered or duplicate time requests normalize to the
same request. Hashing still reads the source and output files; a cache hit is not
zero-cost and is not evidence that the video looks correct.

A changed source, missing or modified image, altered settings or invalid manifest
requires rebuilding. Use `--force` with `--times` to rebuild explicitly.

Review finished files. The helper checks source fingerprints before and after
extraction and refuses to publish if they change. It stages new artifacts before
publishing, rejects symlink output paths, and writes the manifest last. A decode
failure leaves prior published evidence intact. Publication is atomic per file,
not a transaction across the directory; a partial publication cannot pass cache
validation on the next run. Concurrent writers are not supported.

When a later request selects fewer frames, older unlisted files may remain in the
directory. Use `review.json` as the inventory of the current review.

## What this can and cannot establish

Inspect native PNGs for composition, text and overlay details. Watch the moving
output to judge smoothness, pacing and transitions, and listen to assess audio.
The manifest deliberately keeps visual review pending, even on a cache hit.
Frames use the shared RGB decoder; these checks do not establish color-managed
HDR review or correct geometry for every rotation-tagged source.

This is separate from `timeline_view.py`, which supplies a time-range filmstrip
and waveform. It does not fix that helper's final-frame range behavior.

## Tests

```sh
python -m pytest tests/test_sheet.py tests/test_sheet_review.py
```

The tests cover actual frame extraction and pagination, timestamp selection,
full-resolution output, cache invalidation, unsafe paths, interrupted extraction
and source changes during review. FFmpeg-dependent cases skip when its tools are
unavailable.
