# Review large videos using smaller copies

```sh
python helpers/review_copy.py footage.mp4 --cache edit/review-copies --max-edge 720
```

The JSON result names a small H264/AAC movie and its `review.json`. Open the movie
for rough playback, or pass that record to the existing timeline tool:

```sh
python helpers/timeline_view.py footage.mp4 10 14 --n-frames 6 \
  --review-copy edit/review-copies/REPORTED_KEY/review.json -o edit/review.png
```

The timeline keeps the original filename, transcript and audio waveform. It uses
the smaller movie for pictures, applies the verified seek offset, and labels the
image as a review copy. The default long edge is 720 pixels, with no upscaling.
Copies retain the first video stream and first audio stream when present; extra
audio, subtitle and data tracks are omitted from this review artifact.

Timeline start/end values are playback seconds relative to the container's start,
as in FFmpeg's default input `-ss` seeking. They are not raw presentation
timestamps: a source whose timestamps start at 5 seconds still uses `0 2` to
review its first two seconds. The verified offset accounts for the source and
copy container starts as well as their first video frames.

Source contents, settings, tool builds and helper code select a cache entry.
Existing copies are checked against both source and movie checksums before reuse.
New movies and their records are published together only after full decoding,
frame-count and every-frame timestamp checks. Variable frame timing is retained;
timing changes above two microseconds are refused. Decoded audio start and end
relationships allow 50 ms for review encoding. These checks do not
certify sample-exact synchronization or audio quality.

Original media and EDL source paths are never rewritten. Normal final rendering
continues reading the original EDL; keep these review files out of its source map.
Use originals for final delivery and detailed color, focus, text and mask review.

The first copy takes a full encode and verification passes. Later reviews can
reuse it, though checksums still read both files. Smaller copies trade disk space
and initial work for lighter playback and seeking; no speedup is promised for tiny
inputs. There is no automatic cleanup. Interrupted attempts publish no complete
entry; a process killed without cleanup may leave an unused hidden temporary
folder. A damaged completed entry fails clearly: choose a fresh cache directory
or remove that generated entry while no review is using it.

Tagged HDR requires an explicit `--tonemap` choice. The resulting SDR review copy
is not a color-quality reference. Square pixels and quarter-turn display rotations
are supported; other geometries fail explicitly. FFmpeg and ffprobe are required,
with libx264/AAC and zscale/tonemap for HDR. The review-copy helper uses the base
Python dependencies and makes no paid calls. The included source helpers from
#164 provide optional screenshot matching via `uv sync --extra editing` (OpenCV);
tests use `uv sync --extra test` (pytest). Neither extra is needed to make a review
copy. EDL-wide draft rendering through these copies is a later
integration; this change covers rough source playback and timeline inspection.
