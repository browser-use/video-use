# Motion references and asset discovery

Use the compact catalog to find a relevant observed relationship, resource, or pinned asset. Author the composition from the current brief. Catalog metadata does not choose a scene, download references automatically, or establish the quality of a new film.

## Discover and inspect

Run these commands from the repository root:

```sh
python3 helpers/motion_library.py list
python3 helpers/motion_library.py search assembly --json
python3 helpers/motion_library.py show mediawork-pinterest-objects --json
python3 helpers/motion_library.py check
python3 helpers/motion_library.py fetch polyhaven-studio-small-09-1k --out /path/to/project/assets
```

Search uses compact titles, summaries, IDs and tags without loading detailed source. Start with one behavior term when a broad combined query returns nothing. A custom catalog uses `--catalog PATH` before the command. The default catalog resolves relative to the helper file, so an isolated framework copy keeps working.

Fetching is explicit and available only for `asset` entries with redistribution permission. It verifies HTTPS downloads against exact byte counts and SHA-256 hashes, stages all missing files before publishing them without overwriting existing files, and writes a receipt. A valid cache is reused. A mismatched existing file or receipt stops the fetch. Files and combined entry downloads are limited to 64 MiB. No archives are extracted and no provider login or crawling occurs.

Inspect downloaded assets for their intended role. Keep receipts and required license files with the editable delivery. If upstream bytes change, verify the new contents and terms before updating the pin.

## Catalog contracts

The bundled [catalog](../library/catalog.json) contains reusable resource metadata and observed references. Third-party binaries belong in the authored project.

| Kind | Purpose | Permitted use |
|---|---|---|
| `reference` | Observations about composition or motion with evidence and limits | Study the stated relationship and author a new result |
| `resource` | A provider or library with item-level discovery guidance | Select an item and verify its exact terms and suitability |
| `asset` | Specific licensed files with pinned sizes and hashes | Fetch explicitly and inspect the actual files |
| `recipe` | An optional original mechanism in a supplied custom catalog | Load its declared local implementation and controls |

Every entry declares `id`, `kind`, `title`, `tags`, `summary` and `source_url`. An original recipe with a valid local `implementation` may omit `source_url`. Its `detail` and `implementation` paths must remain inside the catalog directory. `related` names existing entry IDs. References require an `evidence` record and `reuse: reference-only`.

Assets require a license name and URL, explicit redistribution permission, an attribution string, and nonempty downloads with URL, plain filename, SHA-256 digest and byte count. Only asset entries may declare downloads. Provider-level permission does not establish permission for an individual item.

## Extend at the existing boundary

Add a resource when its role is clear. Promote an asset only after decoding it, verifying item-level reuse terms, measuring files, and proving its intended role. Document what reference evidence was actually observed and preserve uncertainty about uninspected motion, audio, source projects, and production tools.

For a custom original recipe, describe its meaningful inputs and ownership of geometry or materials. Prove a representative render, a useful control variation, and backward or repeated seeks. Keep the composition's staging and time in its project. Keep asset readiness, rendering and editable delivery in the consuming project.

Keep discovery independent of rendering and EDL validation. A renderer consumes prepared inputs and does not search the library. Retrieve selected entries instead of loading the entire catalog into each prompt.

Custom catalogs are trusted inputs: HTTPS and digest checks verify transport and bytes, not whether a destination belongs to a public network. Publication is atomic per file, not across a whole asset; a late publication failure may leave verified files for a retry. License metadata records the catalog author’s assessment and must be checked against the source terms.

Run `python -m pytest tests/test_motion_library.py` for catalog and simulated download checks. No additional Python packages are required by this helper.
