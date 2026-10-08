# Editing tests

Run the full suite with `python -m pytest -q -rs` after installing the project and
pytest. The CI workflow installs those dependencies explicitly.

`test_video_exports.py` runs the public render command on temporary generated
sources. It requires FFmpeg with `libx264` and `subtitles` (libass), plus ffprobe.
It makes no network or model-provider calls. Run it alone with:

```sh
python -m unittest discover -s tests -p test_video_exports.py -v
```

It checks full decoding, clip order, overlay timing, visible captions above an
overlay, source-to-output caption offsets, audio tone placement, frame rate,
portrait geometry, display rotation and unchanged source bytes. Assets and
outputs are created in a temporary directory and removed after the run.

The fixtures use the draft encoding preset for speed and skip loudness
normalization. These tests do not certify final-quality aesthetics, loudness
targets, OCR spelling, long-edit drift or every codec. The legacy AAC copy-concat
path allows up to 100 ms of total duration/A-V difference on the two-second
fixture; the checks do not claim that its known join padding is fixed.

Missing media tools produce a local skip with an installation hint. CI sets
`VIDEO_USE_REQUIRE_EXPORT_TESTS=1`, making missing capabilities fail the job so
exports cannot silently go untested. Ubuntu installs DejaVu fonts for libass.
On macOS, a standard FFmpeg build may omit libass; put an FFmpeg build with the
subtitles filter first on PATH before running these checks.
