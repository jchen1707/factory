---
description: Run this repository's lint, format and type gates, and report results
---

Run the gate report, narrowed to the three fast kinds:

```sh
node ../hooks/gate_report.mjs --kinds lint,format,types --force
```

The path is relative to **this file**. Layer A ships `commands/` and `hooks/` as siblings in
both delivery adapters, so `../hooks/gate_report.mjs` resolves whether you are reading this
from the plugin (`plugins/harness/commands/`) or from a vendored tree
(`.agents/vendor/harness/commands/`). If you read this file, that file is beside it.

`--force` because you were asked. Without it the report runs a gate only where git says a
gated path changed, which is right for the Stop hook that fires at the end of every turn and
wrong for a person who typed the command.

The report decides which gates those kinds resolve to, in which directories, and whether each
one is switched off, opt-in, or unavailable. **Do not read `harness.config.json` and pick
gates yourself.** That is a second implementation of gate selection, it goes stale the moment
a field is added, and it cannot see a monorepo's per-app dispatch — it would run every app's
gates from the root, which is the wrong answer wearing a green tick.

If the dependency manifest or lockfile changed in this session, run the config's `install`
command first. The report never installs.

Report results:

- Every violation **verbatim**, with `file:line`. A summarised violation is one the reader
  has to go and look up anyway.
- Which gates passed, by their `name`.
- Any gate the report marked `unavailable` or `disabled`, and say which. A verdict of
  `incomplete` is not a pass — it means a gate could not start.

Then stop. Two rules about fixing:

- **Fix only what your current change introduced.** Do not reformat, re-lint or rewrite files
  the change did not touch — a diff full of unrelated reflow is unreviewable, and it buries
  the thing that actually needs review.
- If a gate fails for a pre-existing reason, say so and leave it. Report it as a finding; do
  not fold someone else's cleanup into this change.

Per the Definition of Done in `AGENTS.md`, every one of these gates must be clean before a
change is done. `harness.config.json` is where that list lives — if a gate is missing from it,
the fix is to add it there, not to run something extra by hand.
