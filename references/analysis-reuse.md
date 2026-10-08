# Reuse earlier video checks

```sh
python helpers/analyze.py footage.mp4 --cache edit/analysis \
  --need scenes --need motion --need silence --start 10 --end 30 --limit 20
```

With no `--need`, only source metadata is requested. The helper records source
streams, duration, dimensions, rotation and color tags. Scene and motion requests
share a sequential low-resolution FFmpeg pass; silence uses the first audio
stream. No model downloads, paid calls or additional dependencies are needed.

The report shows which providers reused a saved result and where each complete
record lives. Queries return at most 20 rows by default; complete data stays in
the cache. Timestamps are source presentation timestamps, including a nonzero
stream start time. Inspect the returned start times before translating them to a
timeline that starts at zero. Motion windows report the strongest picture change
in each time bucket. They are not optical flow or semantic action recognition;
scene cuts, camera motion and lighting changes can all produce large scores.
These observations are candidates for review, never automatic editing decisions.

Records match source bytes, analysis settings, provider code and installed media
tool builds. Metadata, picture measurements and silence have separate records.
Changing the silence threshold preserves picture results; changing only the query
window reruns no analyzer. Damaged or incomplete JSON records are rebuilt. Failed
analysis and sources modified during a check are not published. A failed later
provider can leave completed earlier providers ready for retry.

Source checks still read all input bytes. The first full analysis can be slow;
`--timeout` defaults to 600 seconds per media operation and can be raised for long
recordings. The cache uses disk and has no automatic cleanup. Keep original files
outside it; delete the cache only while no analysis uses it. Records above 64 MiB
fail explicitly. This adds a general analysis entry point alongside #164's exact
frame inspection; it does not alter existing analyzer or renderer commands.

Render reuse in #201 saves completed video clips. This helper saves the evidence
used to plan edits. Final render verification remains separate.
