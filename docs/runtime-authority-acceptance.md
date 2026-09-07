# Deterministic authority acceptance

Measured 2026-09-07 UTC using production host functions, the installed layer-A Node
policy interpreter, a real scratch Git repository and a fresh schema-5 SQLite store.
All **14 checks passed**. No model, sandbox or tracker was called. No CRUD or production
state was opened or modified.

Factory HEAD was `2f9be58e29dc9a84c336e2b8b5767ee30f47104d`. The report retains working-tree
status and exact copies/hashes of the authority, operator, configuration, store,
runtime-state, harness and shared-interpreter sources used. This is a deterministic
policy/evidence acceptance experiment, not a full factory gate report.

## Observed behavior

| Operation | Actual result |
| --- | --- |
| Snapshot Prototype root and child policy | Child inherits correctness requirement and the explicit engineering deferral, including rationale and revisit condition. |
| Modify candidate root requirement after snapshot | Repeated snapshot returns original revision 1; published configuration remains unchanged. |
| Change run delivery profile through ordinary operator settings | Refused with `explicit-policy-replacement-required`. |
| Replace with conflicting parent/child requirement | Real Node interpreter rejects it; factory records `delivery-policy-invalid`; revision stays 1. |
| Let child defer a parent-required engineering requirement | Refused with `Child policy cannot weaken parent policy`; revision stays 1. |
| Explicitly replace policy in constructed paused context | Hardening becomes revision 2, requires correctness, engineering and production requirements, has no deferrals, and records exactly one `policy-replaced` operator event. |
| Reuse prior verify/review pass for revision 2 | Both refused with `authority-evidence-stale`. |
| Relabel old verify/review request as fresh evidence | Both refused with `authority-evidence-stale`. |
| Record request and evidence for revision 2 | Both verify/review authority checks accept the new revision. This measures provenance recording, not execution of gates or reviewers. |
| Modify a published snapshot file | `authority.current` refuses with `authority-integrity`; restoring the exact original bytes restores valid integrity. |
| Invoke real CLI replacement for an active scratch run | Nonzero exit with `policy-replacement-needs-paused-run`, before construction of a tracker-reading context. |

The experiment constructs an actual `Context` with unused adapter fields set to `None`;
it has no fake adapters or monkeypatches. Node and Git run as real subprocesses.
The fixture starts paused by construction; this is not another suspend/resume measurement.
The successful replacement invokes `authority.snapshot(..., replace=True)` directly.
A successful CLI/console replacement was deliberately excluded: CLI `_context_for`
reads a Linear issue, outside this experiment's no-tracker scope. The CLI active-state
refusal was measured because it occurs before that lookup.

## Retained evidence and limits

Everything is under `artifacts/runtime-authority-acceptance/`:

- `acceptance.py`: non-reusable executable experiment.
- `inputs.json`: original policies and each attempted weakening/conflict.
- `repository/`: actual Git fixture, retained HEAD and final source configuration.
- `home/state/factory.db` and `home/state/authority/`: scratch store and both snapshots.
- `revision1-request-evidence/`, `revision2-request-evidence/`: retained request markers.
- `active-cli-refusal.json`: exact real CLI exit and output.
- `results.json`: all 14 assertions, refusal reasons, source/fixture revisions,
  operator events, check rows and complete snapshot payloads.
- `source/` and `sha256.json`: source copies and artifact digests.

Results SHA-256: `6bf4e44e3bd85fcc4ab4dd48be6a624bc0367b9f0a2a7147fe0c241bf9ae97ec`.

Two setup-only attempts are preserved separately: the first omitted the attempt
protocol directory, and the second used the nonexistent `python -m factory` entrypoint.
The corrected final run created the directory and used the installed `factory` CLI.
Neither setup error was a product defect, and neither is counted as passing evidence.

This closes the deterministic interpretation, immutable-snapshot and stale-evidence
refusal checks. It does not prove actual reviewer adherence to deferrals, successful
CLI replacement, gate execution under each profile, or end-to-end delivery refusal.
No factory source defect was observed, and no source fix was made.
