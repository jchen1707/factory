# Cancellation respects persisted sandbox identities

A real concurrent clone verification experiment found that cancellation selected the
registry's shared build sandbox instead of the run's recorded per-run identity.
`cli._cancel_run` passed that unresolved project to both `clone.release_branch` and
`SbxAdapter.stop`. This could inspect/release a branch in the wrong VM and stop the
shared sandbox while leaving the intended per-run worker alive. The experiment
paused before executing that unsafe cancellation.

The fix resolves the registry project through `isolation.project_for_run` at the
start of `_cancel_run`, before any cleanup. Both CLI and console cancellation use
this core. The helper changes only the persisted build/review sandbox names; local
paths, orphan cleanup, branch-preservation rules, credential boundaries and legacy
fallback remain unchanged. No new sandbox or tracker action was added.

## Verification

Four new regression cases cover bind/clone layouts with and without persisted
per-run identities. They invoke the complete cancellation core with real scratch
Git/store state and replace only the external sandbox boundary. They assert exact
clone-query and stop targets, untouched base/sibling identities, retained sibling
state/dirty work, and the intended cancelled transition.

Before the fix, both per-run cases failed because the stopped name was the registry
base; both legacy cases passed. After the fix, all four passed, alongside the existing
pipeline and clone suites: **104 tests passed** in the retained combined run. Ruff
check, formatting check, and targeted mypy also passed. Independent bounded review
found no concrete issue.

The new clone case exercises a missing branch, a normal nonfatal cleanup path.
Existing clone tests cover preserving branch commits under a rescue ref before
releasing its name. These deterministic regressions establish routing; the
[concurrent workflow report](runtime-concurrent-workflows-acceptance.md) records
actual live cancellation validation separately. This report does not infer that
live result from the unit/integration suite.

## Evidence and limits

`artifacts/runtime-cancellation-gates/` retains `cancel-identity-red.txt`,
`cancel-identity-green.txt`, `cancel-identity-verification.json`,
`scoped-provenance.json`, and raw `scoped-checks.json`. Root owns the independent
full four-gate `factory-gates.json` in that directory; the scoped checks do not
replace it.

Exact source hashes at this checkpoint:

- `src/factory/cli.py`: `3a1b454f7781c0deeb7a3cac9c59070dbb4f806abb907b9f8de9456bad29a85b`
- `tests/integration/test_cancel_isolation.py`: `f02b9f29df29d0a74bdedc7895d52fb6d2f123618d122e584df4f45358d52b0d`

The patch does not change cancellation of legacy runs that genuinely share one VM
with an active sibling, nor does it add active-reviewer shutdown. Those are separate
behaviors, not proved safe by the new per-run targeting tests. No live cancellation
was executed by the agent implementing this patch; the coordinated acceptance
experiment owns that evidence. No other factory source file was changed by this fix.

Correction `f2a7640` passes all four final factory gates in the linked report. The
real corrected clone cancellation stopped only its recorded VM; the sibling verifier
continued. The concurrent report retains candidate rescue-ref and dirty-work evidence.
