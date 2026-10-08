# Motion export quality checks

`helpers/motion_qa.py` decodes an encoded H264 MP4, compares its delivery properties, checks faststart and audio decode, and writes a contact sheet, poster and JSON report. It uses existing NumPy/Pillow and FFmpeg/ffprobe dependencies and works independently of the browser Capture renderer.

```sh
python helpers/motion_qa.py final.mp4 --expect-width 1920 --expect-height 1080 --expect-fps 30 --expect-duration 12 --expect-audio
python helpers/motion_qa.py final.mp4 --manifest /path/to/render.json
```

Pass the actual manifest path explicitly for Capture exports, whose proof directories have unique names. For older layouts the helper also checks `final.render/render.json`. Without a manifest or explicit expectations, a pass checks basic format and decoding rather than conformity to a particular brief.

Each run uses a fresh `final.qa-*` directory. `--output-dir` selects a new directory that must not already exist. Reports and source media are never overwritten. Exit status is zero for a technical pass and one for a delivery mismatch or processing error. Invalid command arguments exit with status two.

The report checks video dimensions, average FPS, frame count, video duration, H264/yuv420p, optional declared color space, MP4 faststart and required audio presence. Audio is decoded for errors; it is not assessed for loudness, synchronization or artistic suitability.

Flat black/white frames, nearly identical frames and large luma changes are review cues, not automatic failures. Holds and cuts may be intentional. Inspect the actual moving output for clipping, readability, transitions and finish.

Frame-index timestamps and sample positions assume constant frame rate. Variable-frame-rate cue times are approximate; this helper does not prove per-frame PTS uniformity. It fully decodes the movie and separately extracts review frames, so long videos take time. A failed attempt may leave its own partial review directory.

Run `python -m pytest tests/test_motion_qa.py`; integration tests need FFmpeg and ffprobe.
