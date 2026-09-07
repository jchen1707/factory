# Workflow certification acceptance

This continuation validates factory behavior using disposable synthetic stores and
sandboxes. No customer ticket delivery, live configuration, migration or activation
is part of these measurements. Artifacts below are relative to the isolated worktree.

## First paid-admission race

Command: `uv run python artifacts/runtime-certification-service/admission-race.py`.
The script refuses an existing case directory. It uses the existing owned
`factory-build-cert-service-20260907` VM and a new isolated schema-6 store.

Two spawned controller processes synchronize after independently ensuring the same
fresh certification job. The winner starts the real source-owned canary, then exits
with code 73 before `AgentLaunches` can acknowledge the spawn. The other controller
exits normally. A separate exclusive launch marker corroborates the database intent.
A new controller attempts the identical launch and receives false, then collects the
actual terminal result repeatedly without buying another probe.

Observed in `artifacts/runtime-certification-service/initial-admission-race/result.json`:

- Job `dc5990e42fe040e6b81fc57dc68d1623`; one invocation and one actual launch.
- Controller exits 0/73, retained effect `intended`, actual canary exit 0.
- Repeated collection retains known API-equivalent estimate USD 0.0108136.
- Zero active leases; owned VM stopped after collection.

Only the named race assertion was required. The job was deliberately closed as failed
with an explicit synthetic-stop reason instead of launching eight unnecessary phases
or publishing a partial certificate. The earlier complete six-check reports remain
separate evidence.

## Exact authority bytes

Command: `uv run python artifacts/runtime-certification-service/workflow-acceptance.py build`.
Unlike the earlier adapter-only fixture, this uses an actual `Context`, production
`build_spec` / `_review_spec`, immutable authority publication and `agent.selection.select`.
All tracker/delivery adapters are absent; the seeded synthetic state starts at worktree-ready.

Initial real observation failed before any paid launch. The snapshot wrote parsed
`harness.config.json` through JSON serialization, while the observer compared the
snapshot digest with the original candidate bytes. The configuration was semantically
unchanged, but hashes differed. `observer-digest-diagnosis.json` identifies only that
file as mismatched. `regression-red.txt` retains the same digest assertion failure at
the integration seam after correcting the test fixture's candidate files.

`authority.snapshot` now copies each approved configuration byte-for-byte, including
app configurations. Parsing, path checks and integrity publication are unchanged.
Existing snapshots remain immutable. Only the isolated fixture was explicitly replaced
(revision 1 to 2); this fix does not silently refresh any live run's governing policy.
An older reformatted snapshot may require an explicit operator replacement before
automatic certification, with the existing evidence invalidation rules.

The strengthened integration observer compares candidate bytes with every supplied
approved hash. Fourteen focused workflow/policy tests pass. Standards and spec review
found no issues with this bounded fix. Real selection subsequently admitted a canary.
Full workflow launch/recovery acceptance must be reported separately from that admission.

The first full workflow job (`9602588764ed48528980db6422dfdc86`) completed all nine
phase processes but correctly failed final isolation publication. The synthetic
repository omitted `.factory/` from `.gitignore`, so its own certification protocol
files appeared as new application files. `isolation-failure-diff.json` records additions
only below that protocol root, no removed/modified existing files, unchanged HEAD and
unchanged tracked diff. The fixture now declares the protocol ignore and has a new
explicit authority revision 3. No production isolation check was relaxed.

Earlier job `028c7ffaed35499bb35e9e66186fb1d5` became stale after its initial canary
(specification digest changed); it remains failed and accounted. The precise transient
input was not captured, so no cause is claimed. Subsequent observations reused one job.

Canonical `artifacts/runtime-workflow-real/gates.json` passes all four gates: Ruff check
exit 0 (82ms), format exit 0 (41ms), mypy exit 0 (442ms), pytest exit 0 (135430ms).
No skips; every output tail is empty. Both changed files are within configured coverage.
The fake-sandbox suite does not establish real compatibility; those measurements above
remain separate. No production source changed after this report.

## Production builder and recovery paths

Final build job `66103cc8e21547c0aa15e9b6ec643b04` published all six checks through
production selection. `workflow-refusals.py` changed one host evidence byte and invoked
`implement.start`: refusal retained attempt 0 and zero application invocations. Restoring
exact evidence allowed `workflow-builder.py start` to launch the actual production step.
The builder exited 0 with `no_change_needed` and no changed files; a separate collector
retained its session/accounting and released capacity.

`workflow-recovery.py` deliberately injected a resumable state after that normal terminal
result, with an explicit synthetic reason. This tests the real `recovery.resume_run` path,
not an additional crash. Tampered certification refused before attempt 2. Exact restoration
allowed the ladder's `session-intact` branch to launch; the resulting worker exited 0,
reported no changes, and returned the same session identity. Accounting and lease cleanup
were performed by a new controller. The actual process interruption and lost-acknowledgement
measurements remain separate evidence above and in the previous six-check checkpoint.

`workflow-stop.py` recollects all retained jobs/application evidence, checks estimate replay
and zero leases, asserts a clean target, and stops only the two owned workflow VMs.
The independent reviewer **workflow** matrix remains unfinished even though the prior
adapter/service reviewer six-check fixture passed. Child execution/integration also remains
unfinished. The result is a builder/recovery checkpoint, not full feature completion.
