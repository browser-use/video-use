# Load the instructions needed for this edit

`SKILL.md` is always required. It retains the production rules, approval process,
output contract and final checks. Technique details live in small reference files.

At the start of an edit, infer the needed capabilities from the user's request
and footage, then call the selector. For an interview trim with captions:

```sh
python helpers/context_router.py --need cuts --need speech --need captions
```

Read only the returned files, resolving their paths relative to `SKILL.md`.
For an agent runner that accepts one text input, `--format markdown` prints the
same core plus selected references. The caller supplies that text through its
normal instruction channel; the helper does not start or configure an agent.

| Capability | When to request it |
| --- | --- |
| `cuts` | Select or trim source ranges |
| `speech` | Transcribe speech or compare spoken takes |
| `captions` | Add or change subtitles |
| `color` | Correct exposure/color or apply a grade |
| `animation` | Author or place animated overlays |
| `sound` | Design music or sound effects |

The agent makes the initial selection, so requests in any language work without
an English keyword classifier. The empty selection returns just the core.
Add capabilities as the task changes; this list does not limit artistic choices.

After writing an EDL v1, include `--edl` to automatically add instructions for its
ranges, subtitles, grade and overlays. EDL facts add to explicit `--need` values.
They never remove instructions already requested. Speech and sound design are
explicit because their presence cannot be reliably inferred from an EDL v1.
This selects guidance; it does not validate whether an edit can be rendered.

```sh
python helpers/context_router.py --need speech --edl /path/to/footage/edit/edl.json \
  --receipt /path/to/footage/edit/context-02.json
```

The receipt records paths, content checksums, byte counts and selection reasons.
It stores no task text, source metadata or credentials. Existing receipts are
never replaced. Use a new numbered receipt when revising the selection.
`--max-bytes` fails rather than silently removing instructions. Byte counts cover
instruction contents, not the small Markdown separators, and are not token counts.

Technique text was moved from `SKILL.md` without changing the hard rules.
Optional engine skills still load on demand as directed by the animation guide.
