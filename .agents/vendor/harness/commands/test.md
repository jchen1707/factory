---
description: Run this repository's test suite and report results
---

Run the gate report, narrowed to the test kind:

```sh
node ../hooks/gate_report.mjs --kinds test --force
```

The path is relative to **this file**. Layer A ships `commands/` and `hooks/` as siblings in
both delivery adapters, so `../hooks/gate_report.mjs` resolves whether you are reading this
from the plugin or from a vendored tree. `--force` because you were asked: the change filter
exists for the Stop hook, not for a person who typed the command.

An `e2e` or `integration` gate is opt-in, and the report will not run one unless you assert
it. When a gate's `when` clause applies to this change, name it:

```sh
node ../hooks/gate_report.mjs --kinds test,e2e --force --gate <gate-name>
```

Assert them **one at a time, by name**. `--all` asserts every opt-in `when` clause at once,
including clauses that are plainly false for your change, and a gate that should never have
executed can block a run it had no business blocking.

If the manifest or lockfile changed, run the config's `install` command first. The report
never installs.

Report the pass/fail summary and every failure **verbatim**, with `file:line`. Report any gate
the report marked `unavailable` — a missing browser or container is not a failing test, and
handing an agent "the test failed" for a gate that never started sends it to fix code that was
never wrong.

**Do not modify tests or source to make a failing test pass.** Diagnose the root cause and
propose a fix. A test edited until it agrees with the code has stopped being evidence — that
is the whole reason the writer/reviewer split exists, and it is just as true when one agent is
doing both jobs in sequence.

Two failures that look alike and are not:

- The behaviour is wrong → fix the behaviour.
- The test was wrong → say so explicitly, name what it asserted and why that was wrong, and
  change it deliberately. Never quietly.

Per the Definition of Done in `AGENTS.md`, the test gates must be green before a change is
done.
