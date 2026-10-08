# Source inspection

Use these helpers when an editing decision needs source evidence. Keep generated
reports and working copies under the footage directory's `edit/` folder. A full
catalog decodes frame metadata for the entire video, so prefer inspecting only
sources relevant to the current decision.

## Install

FFmpeg and ffprobe must be on PATH. Install the optional screenshot-matching
backend with `uv sync --extra editing` or `pip install -e '.[editing]'`.
OpenCV is needed only by `find_shot.py`; the other helpers use the base Python
dependencies. Tests additionally require pytest.

## Catalog and match

```sh
python helpers/source_scan.py /footage/source.mp4 --out /footage/edit/source.json
python helpers/source_scan.py /footage/source.mp4 --out /footage/edit/scenes.json --scenes
python helpers/find_shot.py /footage/query.png /footage/source.mp4 --every 1 --out /footage/edit/matches.json
```

The catalog records zero-based decoded frame indices, original presentation
timestamps (PTS), stream metadata and a SHA-256 fingerprint of the source bytes.
PTS can start above zero and need not advance at a fixed rate. Selected frames
are decoded sequentially, without approximate keyframe seeking.

Scene changes are suggestions based on differences between adjacent thumbnails.
Screenshot matching compares local image features and ranks geometric agreement;
it is not semantic search or face identification. Sampling every second can miss
a brief shot. Inspect candidate frames and moving footage before accepting a match.

## Prepare a working copy

```sh
python helpers/prepare_source.py /footage/source.mp4 -o /footage/edit/prepared.mkv --crop 0 0 1280 720
```

Crop values are `x y width height`, even pixels within the encoded frame dimensions.
Tagged HDR input requires an explicit `--tonemap` decision, which converts to SDR.
This command rejects an existing output rather than replacing it. It writes an
FFV1 Matroska video, the first audio stream as PCM if present, a command-error log,
and a JSON record with input/output fingerprints, metadata and applied filters.

FFV1 is lossless encoding of the prepared pixels, not a promise that the preparation
preserves every source pixel. Cropping, conversion to 10-bit YUV 4:4:4, and HDR
conversion can change the image. Other audio tracks, subtitles and data tracks are
not copied. Working files can be large. Inspect color and crop before editing from
the derivative; this helper cannot restore detail missing in the source.

## Track evidence and dependencies

An entry file describes one artifact:

```json
{
  "phase": "sources",
  "path": "source.json",
  "summary": "Inspected source frame catalog",
  "status": "measured",
  "depends_on": []
}
```

```sh
python helpers/project_state.py record /footage/edit/context.json source /footage/edit/entry.json
python helpers/project_state.py show /footage/edit/context.json --phase sources
```

Artifact paths are relative to the context file. Record dependencies before the
artifacts that use them. Changed or missing bytes make evidence stale, including
its dependents; recording the changed source does not approve old downstream
results. Regenerate and record affected artifacts after review. Status labels are
explicit declarations, not automatic visual approval. The context is a small
single-writer JSON file; it is not a concurrent project database.

## Shared foundations

`edit_clock.py` maps seconds, frames and audio samples using rational arithmetic,
including fractional frame rates, and checks contiguous half-open shot intervals:
start is included, end is excluded. These utilities do not change the existing
renderer. `edit_io.py` shares JSON, probing, fingerprinting and command errors.

`source_path` requires a provenance declaration and rejects files marked
`study_only` or aliased to `study_media`. This is a caller-invoked policy check,
not proof of rights or origin and not a way to detect an independently copied file.

## Validation

```sh
python -m pytest tests/test_sources.py
```

Tests use generated media and temporary project files. FFmpeg-dependent tests
skip explicitly when its tools are unavailable; matching tests require OpenCV.
No later Sources-dependent PR is needed to collect or run this suite.
