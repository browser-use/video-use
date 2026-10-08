# Reuse unchanged clips

Add `--reuse` to the normal render command:

```sh
python helpers/render.py /path/to/footage/edit/edl.json \
  -o /path/to/footage/edit/final-02.mp4 --reuse
```

The first run saves completed clips in `edit/render-cache/`. Later runs compare
source contents, cut ranges, picture settings, helper code, media-tool versions
and relevant Python-library versions before reusing a clip. Every copied cache
hit is checked against its saved content checksum. Incomplete or corrupted
entries are rendered again. Failed renders never publish a cache entry.

The cache covers EDL v1/v2 segment extraction and EDL v3 picture shots and filmed
layers. A caption or audio revision can reuse the underlying picture shots.
Changing one shot's source range or grade rebuilds that shot. Changing a source
file rebuilds every cached shot that reads it. Masks are tracked as input files.
Helpers and tools are checked conservatively: changing any helper invalidates
old entries. Files must stay unchanged while the render is in progress.

Legacy raw FFmpeg grade filters are deliberately rendered without reuse because
they can read files that are not declared in the EDL. Built-in grades and
auto-grade are supported. Auto-grade analysis still runs before the cache lookup.

Final assembly, audio mixing, overlays, captions and the existing output checks
still execute. This feature preserves the renderer's existing verification
coverage and output restrictions; it does not add verification to legacy paths.
For compositions, continue choosing a new versioned output name for each run.
Reuse counts are printed by the command and included in composition verification
reports. A cached clip is copied into the build, never hard-linked to it.

The cache uses disk space and reads source contents to validate reuse. It does
not promise a speedup for tiny inputs or expensive auto-grade analysis. It has
no automatic eviction: remove the project's `render-cache` folder when its
saved intermediates are no longer needed, with no render using it. Keep source
files outside that generated folder. Concurrent cache publication is checked
by content; concurrent legacy renders in one edit directory remain unsupported.

Use the command without `--reuse` for the existing uncached behavior. No new
dependencies, network service or paid model call are introduced.
