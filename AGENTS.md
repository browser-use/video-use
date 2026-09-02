# video-use repository context

video-use is a public, conversation-driven video production framework. Changes
made here may be packaged into the skill and reused by people with different
machines, media, workflows, providers, brands, and output goals. Treat the
repository as a general product, never as one person's customized clone.

## Product principles

- The delivered video is the product. Prioritize visual and audio quality,
  editorial judgment, factual correctness, synchronization, pacing, and
  production reliability.
- Design reusable contracts and capabilities. Do not hardcode personal paths,
  credentials, account ids, prompts, brands, preferences, or assumptions
  about one project.
- Keep provider-specific behavior behind narrow boundaries. Core EDL validation,
  rendering, reframing, and QC must remain usable from the command line without
  any optional client or remote runner.
- Preserve backwards compatibility when practical. If a format must change,
  provide a clear migration path and reject unsupported input with an actionable
  error.
- Never silently downgrade a requested feature. A missing source, track, model,
  codec, or dependency should fail before expensive work begins and explain what
  is required.
- Defaults should be safe and broadly useful, while explicit project or user
  requirements always win.
- Keep credentials out of source, logs, fixtures, prompts, and generated
  artifacts. Configuration belongs in environment variables or provider secret
  stores.

## Architecture boundaries

- `SKILL.md` defines the agent workflow and public editing contract.
- `helpers/` contains provider-independent production tools and validation.
- `skills/` contains focused companion skills and reusable production assets.
- `tests/` protects public behavior. Optional clients or remote runners may
  orchestrate core features but must never become the only place a feature
  exists.

Keep decision data explicit in portable project files such as `edit/edl.json`.
Renderers should consume declared inputs deterministically. UI state, agent
history, and cloud runtime state must not be required to reproduce an output.

## Change workflow

1. Identify whether a change belongs to the public editing contract, a reusable
   helper, a focused skill, or an optional adapter.
2. Implement the smallest complete general capability at the lowest reusable
   layer. Wire adapters to that capability instead of duplicating it.
3. Validate inputs locally before uploads or paid compute. Validate again at
   remote execution boundaries.
4. Add tests for successful use, invalid input, backwards compatibility, and
   provider-boundary behavior where relevant.
5. For render changes, create representative media and inspect the encoded
   dimensions, duration, frame rate, visual framing, and audible output.
6. Update the public EDL example or usage documentation whenever users or agents
   need to author a new field.

Cost and latency are secondary unless the user sets a budget or deadline. Improve
them only when output quality and reliability remain equal or improve.

## Communication and commits

After code changes, summarize the affected files, the functions or contracts
added, and what each does in plain language. Keep this technical context compact
so someone can learn an unfamiliar codebase without reading every diff.

Write simple, readable commit messages. Prefer short lowercase wording without
punctuation.

## Branch discipline

Several agents work on this repository at once. To keep one agent's progress
from being overwritten by another:

- One feature per branch, one agent per branch. Never edit a worktree that
  belongs to another branch; take files from a commit or tag instead.
- Commit early. Uncommitted work in a worktree has no merge base and no
  history, so a later sync silently discards it.
- Hard rules in `SKILL.md` are append-only. New rules get the next number.
  Removing or renumbering a rule requires an explicit reason in the commit.
- Procedure prose belongs in `references/<feature>.md` at the repository root;
  create that folder with the first reference file. Edits to `SKILL.md`
  are limited to rules, helper-index bullets, directory-tree lines, the EDL
  example, and one-line pointers to the reference files.
- `tests/test_skill_contract.py` checks that the rules and every referenced
  path still exist. Run it before committing a `SKILL.md` change.
