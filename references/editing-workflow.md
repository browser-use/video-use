# From raw footage to a finished edit

Use this guide for ordinary edits: choosing takes, trimming speech, adding captions and checking the result. [SKILL.md](../SKILL.md) remains the source of production rules; [install.md](../install.md) covers setup.

Run the examples from the video-use repository with its Python environment active. Replace `/path/to/footage` with your source folder. Keep source videos unchanged and put generated files in that folder’s `edit/` directory.

## 1. Pick up the project

Read any existing `edit/project.md` before starting. Check the source files, their duration, frame rate, orientation and audio tracks with `ffprobe`. Agree on the audience, length and destination before choosing cuts.

## 2. Read the footage

Transcribe only after the user has supplied footage for editing. This command uses the configured ElevenLabs service and can incur API charges. It reuses existing transcript files, so inspect the cache if footage has changed under the same name.

```sh
python helpers/transcribe_batch.py /path/to/footage --edit-dir /path/to/footage/edit
python helpers/pack_transcripts.py --edit-dir /path/to/footage/edit
```

Read `edit/takes_packed.md`. For uncertain cuts, use `timeline_view.py` on the relevant source and time range. Listen to the audio as well as inspecting frames.

## 3. Agree on the edit

Propose the story order, cuts, caption style and output format. Wait for approval before executing the cut. Save the agreed plan in `edit/project.md`.

## 4. Build a preview

Create `edit/edl.json`, the edit decision list: the source ranges, their order and the settings the renderer should use. Follow the format in [SKILL.md](../SKILL.md), and keep every range within its source duration.

For an edit with captions:

```sh
python helpers/render.py /path/to/footage/edit/edl.json -o /path/to/footage/edit/preview-01.mp4 --preview --build-subtitles
```

Use `--no-subtitles` instead of `--build-subtitles` when captions are deliberately omitted. `--preview` uses the 1080p quality preset. `--draft` uses a faster 720p preset for checking cuts; it is not enough for final visual review. Output shape also follows the edit settings.

Use a new output name for each attempt you want to keep: rendering can replace existing output files. Intermediate clips are reused or replaced in the edit directory, so do not run two renders there at once.

## 5. Check the rendered result

Inspect the actual preview at every cut, including roughly 1.5 seconds on each side where possible. For example, inspect a cut at 12 seconds:

```sh
python helpers/timeline_view.py /path/to/footage/edit/preview-01.mp4 10.5 13.5 -o /path/to/footage/edit/cut-12.png
```

Listen for clipped words, abrupt audio and uneven levels. Check captions for timing, spelling and safe placement. Check duration, orientation and frame rate with `ffprobe`. If something fails, update the edit, render again and repeat the checks. Follow the skill’s three-attempt limit and report remaining problems.

## 6. Finish and leave useful notes

After reviewing the preview with the user, render without `--preview` or `--draft` for final quality:

```sh
python helpers/render.py /path/to/footage/edit/edl.json -o /path/to/footage/edit/final-01.mp4 --build-subtitles
```

Check the final file again before delivering it. Update `edit/project.md` with the approved choices, source files, edit file, final output, render command, repository revision and anything still unresolved. The next session should be able to continue from those notes without guessing.
