# Check an edit before rendering

The normal `render.py` command now checks its inputs before extracting clips.
To run just the checks:

```sh
python helpers/render.py edit/edl.json -o edit/final-02.mp4 --check
python helpers/check_edit.py edit/edl.json -o edit/final-02.mp4 --json
```

Pass the same `--build-subtitles`, `--no-subtitles` and `--no-loudnorm` flags
you intend to use for rendering. Checks cover EDL v1 range structure, declared
media and transcript files, video/audio presence, known duration bounds, output
directory access, the required FFmpeg filters and encoders, and a usable caption
font. Caption checks render one disposable synthetic frame; they do not inspect
source imagery or call a service. Existing outputs are refused so a rerun needs
a new output name. A failed check creates no edit artifacts.

When using `--build-subtitles`, missing transcripts produce a warning and those
segments remain without captions, matching the renderer. Existing transcripts
must be readable and contain valid word timing. Explicit subtitle files must be
readable even when they already exist.

This builds on `check_env.py` from the environment checks PR: that command still
handles initial setup and optional animation programs. These checks select the
requirements for a specific legacy edit. EDL v2/v3 integration is a follow-up
once those renderer contracts land; unsupported versions fail explicitly.

Success is a starting check, not final approval. Metadata may be incomplete or
incorrect, source files can change later, raw grade filters can read additional
files, and container duration is not proof of decodability. Review notes report
these gaps. Fonts can be substituted by the installed platform, and this probe
does not prove coverage for every language or ASS style. Final playback, caption
legibility, sync, color and output checks remain necessary. No tools are installed
automatically and no API credentials are required.
