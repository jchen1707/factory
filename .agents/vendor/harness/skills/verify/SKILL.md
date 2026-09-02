---
name: verify
description: Run the Definition of Done gates and report the actual output as evidence. Use before claiming work is complete, before opening a PR, or whenever asked whether the change actually works.
argument-hint: '[--all]'
---

Prove the change works. **Paste real command output — never assert success.**

The gates are declared once, in `harness.config.json`, and one program decides which of them
apply: `gate_report.mjs`. The same gates run automatically in the `Stop` hook, but only when
the turn touched a gated path. Invoke this skill when you want the evidence in the transcript,
or to cover the cases the hook deliberately skips.

## Gates

```sh
node ../../hooks/gate_report.mjs --force
```

The path is relative to **this file**. Layer A ships `skills/` and `hooks/` under one root in
both delivery adapters, so `../../hooks/gate_report.mjs` resolves whether you are reading this
from the plugin or from a vendored tree. `--force` because a person asked: the change filter
belongs to the Stop hook, not to a command someone typed.

With no `--kinds`, the report runs exactly the gates the Stop hook runs — `lint`, `format`,
`types`, `build` and `test`. `build` is in that list and not in `/lint`'s for a reason: it is
usually the only gate that checks the deployment target, so a construct the type checker
accepts and the target does not reaches CI green from every faster gate. It is also slow,
which is why the inner loop skips it and this does not.

**Do not read `harness.config.json` and pick gates yourself.** Gate selection has one
implementation on purpose. A hand-picked list cannot see `enabled: false`, cannot see a
monorepo's per-app dispatch, and cannot tell a gate that failed from one whose environment was
never there — and it goes stale silently every time the config grows a field.

**Read the run in order and stop reporting at the first failure.** A later gate's output is
meaningless once an earlier one failed, and quoting all of it buries the one line that
mattered.

When a gate's `when` clause applies to this change, assert that gate by name — and only that
one:

```sh
node ../../hooks/gate_report.mjs --force --gate <gate-name>
```

`e2e` and `integration` gates need a browser or a container, so they are opt-in rather than
part of the default loop — but "opt-in" is not "optional": each one usually checks a class of
failure the others structurally cannot. With `--all` in `$ARGUMENTS`, pass `--all`, which
asserts every opt-in `when` clause at once. Prefer `--gate`: `--all` asserts clauses you may
not have meant, and a gate that should never have executed can block a run it had no business
blocking.

The report never runs the config's `install`. Run it first yourself if dependencies changed.

## Reporting

For each gate: the command, its exit status, and the tail of its output. Then one of:

- **PASS** — every gate green. State which gates ran and which were skipped.
- **FAIL** — name the first failing gate, quote the failure, and state the root cause if you
  can see it. **Do not attempt the fix inside this skill** — report, and let the caller decide.

### A green gate is not automatically evidence

Three failure modes deserve to be called out rather than glossed over. All three end with a
zero exit status, which is what makes them dangerous.

- **A gate that could not run** — a missing container, a browser not installed, no build
  output to measure, an entry point that does not exist yet. That is not a pass. The report
  says `unavailable` and its verdict becomes `incomplete`; carry both words through to the
  reader rather than rounding them to green.
- **A gate that ran and measured nothing.** Every row carries its `caveat`, and that field
  names exactly how the gate can pass vacuously. **Print the caveat beside the
  green result and say whether it applies to this change.** A rule that fails open on a file
  it does not classify, or a metric the runner returned as null, has told you nothing — and
  the exit code says otherwise.
- **No tests exist for the changed behaviour.** A green test gate proves nothing then. Say so:
  "12 passed, none covering the new code path" is the honest line, and it is the one the
  reader needs.

## Verifying by hand

A green suite is not proof the application runs. When the change is user-visible, exercise it
the way this repository documents — the dev server plus whatever interactive client its docs
name — and report what you observed: the entry point, the state before, the thing you did, the
state after.

**This is evidence, not coverage.** Driving the application by hand proves the change works
now; it cannot fail a build tomorrow. If the change altered behaviour and no test covers it,
say so explicitly — "verified by hand, unguarded by tests" — and name the test that should
exist.

## The machine-readable form

Add `--json` and the same run comes back as one document instead of a summary: one row per
gate with its `status` (`pass`, `fail`, `unavailable`, `not_applicable`, `skipped_unchanged`
or `disabled`), its `app`, `exit`, `durationMs`, `caveat` and `when`, the `requestedKinds` you
asked for, and a single `verdict`. Use it when a caller needs the record rather than prose —
an automated verifier, a CI summary, a run record — and leave it off when a human is reading.

Two properties of that document are worth knowing even when you never pass `--json`, because
they are what the summary is telling you in words:

- A gate that declares a `requires` argv is **probed with it first** and reported
  `unavailable` without running when the probe does not exit zero. "The browser was never
  installed" arrives as a gate that could not run rather than as a gate that failed, which is
  a different instruction to whoever reads it.
- The `verdict` is `incomplete` — **never `pass`** — when a gate could not start or a monorepo
  app had no config of its own, because a green exit code alone does not prove every relevant
  gate ran. The exit codes keep the three apart for a caller that reads only the code: `0`
  pass, `1` fail, `3` incomplete.

> **Image-input consent (hard rule).** A screenshot or a screencast feeds an image into the
> model. **Stop and ask the user for permission first, and do not proceed until they
> confirm.** Prefer a text snapshot of the interface: it needs no consent, and it is a better
> assertion target than a picture because it exposes the roles and names rather than the
> pixels.
