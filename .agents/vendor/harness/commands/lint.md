---
description: Run this repository's lint, format and type gates, and report results
---

Read `harness.config.json` at the repository root. Run every gate whose `kind` is `lint`,
`format` or `types`, in the order they are listed, using the `run` argv exactly as written.

If the dependency manifest or lockfile changed in this session, run the `install` command
first.

Report results:

- Every violation **verbatim**, with `file:line`. A summarised violation is one the reader
  has to go and look up anyway.
- Which gates passed, by their `name`.

Then stop. Two rules about fixing:

- **Fix only what your current change introduced.** Do not reformat, re-lint or rewrite files
  the change did not touch — a diff full of unrelated reflow is unreviewable, and it buries
  the thing that actually needs review.
- If a gate fails for a pre-existing reason, say so and leave it. Report it as a finding; do
  not fold someone else's cleanup into this change.

Per the Definition of Done in `AGENTS.md`, every one of these gates must be clean before a
change is done. `harness.config.json` is where that list lives — if a gate is missing from it,
the fix is to add it there, not to run something extra by hand.
