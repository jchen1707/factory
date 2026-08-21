---
description: Run this repository's test suite and report results
---

Read `harness.config.json` at the repository root. Run every gate whose `kind` is `test`.

Run an `e2e` or `integration` gate as well when its `when` clause applies to this change —
those tiers need a browser or a container, so they are opt-in rather than part of the default
loop. If the manifest or lockfile changed, run `install` first.

Report the pass/fail summary and every failure **verbatim**, with `file:line`.

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
