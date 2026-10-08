# Deliverables, reframe tracks, and loudness targets

Declare every delivery format in the EDL instead of running ad hoc ffmpeg
commands. `helpers/edl.py` validates the declaration and `helpers/render.py`
renders each deliverable from one staged edit.

## Declaration

```json
{
  "reframe": {
    "mode": "track",
    "track_file": "reframe_tracks.json"
  },
  "deliverables": [
    {
      "id": "broadcast_16x9",
      "file": "deliverables/broadcast.mp4",
      "width": 1920,
      "height": 1080,
      "fps": 30,
      "reframe": "cover",
      "loudness": {"integrated_lufs": -24, "true_peak_dbtp": -2, "lra": 11}
    },
    {
      "id": "social_9x16",
      "file": "deliverables/social_vertical.mp4",
      "width": 1080,
      "height": 1920,
      "fps": 30,
      "reframe_track": "social_9x16",
      "loudness": {"integrated_lufs": -16, "true_peak_dbtp": -1, "lra": 11}
    }
  ]
}
```

- `id` is a stable name used by `--deliverable <id>` and by any tool that reads the outputs.
- `file` is relative to the EDL directory unless absolute.
- `width`/`height` may also be written as `resolution: "1080x1920"`.
- `fps` accepts integers, decimals, or rationals such as `30000/1001`.
- `loudness` sets the two-pass loudnorm target per deliverable.

## Reframe modes

- `cover` is an explicit center crop to the deliverable's aspect.
- `contain` letterboxes or pillarboxes.
- `track` follows normalized subject-center keyframes. A tracked request never
  falls back to a center crop silently; a missing track is an error.

The track file uses output-timeline seconds and normalized source coordinates:

```json
{
  "tracks": {
    "social_9x16": [
      {"time": 0.0, "center_x": 0.32, "center_y": 0.48},
      {"time": 4.5, "center_x": 0.68, "center_y": 0.47}
    ]
  }
}
```

Keyframes are interpolated linearly between times. Each deliverable names its
track with `reframe_track`; the root `reframe` object supplies the shared mode
and track file.

## Rendering

```bash
python helpers/render.py edit/edl.json --all-deliverables
python helpers/render.py edit/edl.json --deliverable social_9x16
python helpers/render.py edit/edl.json --all-deliverables --output-dir <dir>
```

Use `--output-dir` to keep every rendered deliverable inside one directory. Blocked EDLs, empty ranges, missing sources, missing tracks,
invalid dimensions, and invalid loudness targets are rejected before any
extraction starts.
