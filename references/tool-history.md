# Keep a record of the tools used for an edit

Before starting an edit, save a record of the current video-use files alongside the edit:

```sh
python /path/to/video-use/helpers/runtime_snapshot.py record -o /path/to/footage/edit/tool-record.json
```

When returning to the edit, check whether those files still match:

```sh
python /path/to/video-use/helpers/runtime_snapshot.py check /path/to/footage/edit/tool-record.json
```

A match exits with status 0. Changed, added or missing files, invalid records and other errors exit with status 1. Existing record files are never replaced. For a later attempt, save a new record with a different name.

The record contains relative file names and SHA-256 hashes, not source contents. It covers helpers, skills, references, assets, tests and the root skill, agent, package and lock files. It ignores separate apps, generated caches, media folders, `.env` files/directories and symbolic links. Use `--root /path/to/another/video-use` before `record` or `check` to check another copy.

This is an explicit record of the code available for an edit. It does not log which commands ran, record Python/FFmpeg versions, save source footage, restore old tools or guarantee the same output across machines. Keep the repository revision and render command in your project notes as well. Do not modify tool files while recording or checking them.

Run `python -m pytest tests/test_runtime_snapshot.py` for the focused checks.
