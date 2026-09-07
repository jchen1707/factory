# Host diagnosis admission and handoff acceptance

Measured 2026-09-07 UTC in `artifacts/runtime-diagnosis-acceptance/`, with a fresh
SQLite store and local Git repositories. This is production-function acceptance
against actual command output; it is not a model diagnosis or a sandbox workflow.
No production or CRUD state, tickets, sandboxes, or credentials were changed.

The scratch repository declares one Python acceptance command in its own
`harness.config.json`. The existing vendored `gate_report.mjs --force --json` ran
that command six times. Each run exited 1 and produced a schema-valid failed report.
The observable failure required four and actually returned one, two, or three.
Raw stdout, stderr, command arguments, times, and reports remain under each scratch
repository's `.factory/run/` directory.

Production `handoffs.record_failure`, `handoffs.authorize_repair`, and
`execution.guard` consumed the persisted evidence and store:

- First repair admitted; recollecting the same authorization was idempotent.
- A second real command run with unchanged failing behavior was refused as
  `failure-without-new-evidence` despite a new report and duration.
- Changed failing behavior admitted the second repair. Another changed failure
  was refused as `repair-limit`; SQLite retained exactly two authorizations in
  one failure episode.
- Missing reproduction and modified verifier bytes were refused. The configured
  lifetime ceiling of five and the subsequent-attempt spend ceiling of $50 were
  enforced. The spend record was controlled probe input, not measured model cost.
- Operator-supplied environment, stale-authority, requirements, and review-dispute
  classifications produced retained human handoffs and refused automatic repair.
  Classification accuracy itself was not tested.

`summary.json` records outcomes and source digests. `state/factory.db` contains
the checks, episode, and controlled cost record; invocation and effects tables
are empty. `initial-admission-order/` preserves an earlier probe configuration
that correctly queued at the project slot check before reaching the spend check.
The completed experiment assigns independent project identities to its scenarios.

## Handoff fidelity defect and correction

The physical preservation check retained two commits, branch, HEAD, a tracked
diff, and an untracked file. Its exact porcelain check exposed a defect:
` M value.py` became `M value.py` in the handoff, losing the distinction between
the index and working-tree columns. Whitespace stripping caused this in both
host and clone collection.

`handoffs.write` now preserves raw Git document output and strips only the commit
SHA. Both real-Git integration regressions failed before this three-line correction
and pass afterward; the clone test uses the sandbox adapter fake to route real Git
commands into a real local clone. All 13 inventory/fingerprint tests, targeted Ruff,
and mypy pass. Independent bounded review found no concrete defect. All four factory gates pass
for corrected source `746dfdd` in
`artifacts/crud-runtime-test/factory-gates-handoff-inventory.json`. Mypy checks declared
paths; fixture-based suite success does not replace the separately retained real-host
command measurements.

The original observed failure and payload remain in `porcelain-before-fix/`.
`capture_final_handoff.py` passed against the corrected source, retaining exact
status columns and identical before/after work inventory in
`final-handoff-preservation.json`. `corrected-provenance.json` identifies its source
and evidence digests separately from the earlier admission experiment.

## Remaining acceptance

These experiments prove deterministic routing, evidence provenance, repair admission,
and host handoff fidelity. Repair authorizations were exercised; no agent repaired
code. A fresh model must still diagnose a real preserved failure, consume its
handoff, perform bounded repair through factory orchestration, and return through
verification. Human dispute decisions, sandbox recovery parity, and full delivery
are not established by this host experiment.
