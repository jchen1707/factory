---
name: verify
description: Run the Definition of Done gates and report the actual output as evidence. Use before claiming work is complete, before opening a PR, or whenever asked whether the change actually works.
argument-hint: '[--all]'
---

Prove the change works. **Paste real command output — never assert success.**

The gates are declared once, in `harness.config.json` at the repository root. Read it. The
same gates run automatically in the `Stop` hook, but only when the turn touched a gated path.
Invoke this skill when you want the evidence in the transcript, or to cover the cases the hook
deliberately skips.

## Gates

Run every gate whose `kind` is `lint`, `format`, `types`, `build` or `test`, in the order
they are listed, using the `run` argv exactly as written. Run `install` first if dependencies
changed.

`build` is in that list and not in `/lint`'s for a reason: it is usually the only gate that
checks the deployment target, so a construct the type checker accepts and the target does not
reaches CI green from every faster gate. It is also slow, which is why the inner loop skips it
and this does not.

**Stop at the first failure.** A later gate's output is meaningless once an earlier one
failed, and running on produces a wall of noise that buries the one line that mattered.

With `--all` in `$ARGUMENTS`, or when a gate's `when` clause applies to this change, also run
the `e2e` and `integration` gates. Those need a browser or a container, so they are opt-in
rather than part of the default loop — but "opt-in" is not "optional": each one usually checks
a class of failure the others structurally cannot.

## Reporting

For each gate: the command, its exit status, and the tail of its output. Then one of:

- **PASS** — every gate green. State which gates ran and which were skipped.
- **FAIL** — name the first failing gate, quote the failure, and state the root cause if you
  can see it. **Do not attempt the fix inside this skill** — report, and let the caller decide.

### A green gate is not automatically evidence

Three failure modes deserve to be called out rather than glossed over. All three end with a
zero exit status, which is what makes them dangerous.

- **A gate that could not run** — a missing container, a browser not installed, no build
  output to measure, an entry point that does not exist yet. That is not a pass. Report it as
  skipped, with the reason.
- **A gate that ran and measured nothing.** Where a gate in `harness.config.json` carries a
  `caveat`, that field names exactly how it can pass vacuously. **Print the caveat beside the
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

> **Image-input consent (hard rule).** A screenshot or a screencast feeds an image into the
> model. **Stop and ask the user for permission first, and do not proceed until they
> confirm.** Prefer a text snapshot of the interface: it needs no consent, and it is a better
> assertion target than a picture because it exposes the roles and names rather than the
> pixels.
