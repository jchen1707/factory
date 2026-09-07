# Integration-base freshness acceptance

Status: **PASS**, measured in `factory-build-crud-20260907`; the sandbox was stopped
and released after execution. Dedicated fixture run: `781618cee2754bec`.

Both original and refreshed gate reports show `verdict: pass`: the API and web acceptance
gates each executed with exit 0, no skips, and no declared caveats. The observed refusals were:

- Missing verification base: `integration-evidence-stale`.
- Integration advanced after successful gates: `integration-evidence-stale` at delivery.
- Preserved private branch missing that commit: `integration-base-refresh-required`.
- After explicit merge and fresh gates: the base guard accepted, but absent independent
  review evidence remained `authority-evidence-stale`.

Original integration base: `ece7eb835d0e3d6166f607999a30f70a265f6009`.
Advanced integration base: `a07c4f360c5daf38e4a2bb4de6d27429200afdc8`.
The original candidate commit remained an ancestor after the merge, the feature branch stayed
selected, and the uncommitted operator-work file remained byte-for-byte unchanged. Frozen
authority integrity passed. No actual review or delivery occurred.

This deterministic acceptance uses production `integration_base.before_verification`,
`integration_base.before_delivery`, `clone.refresh_base`, authority snapshots, and the
shared gate reporter. It uses an independent private VM clone, a host-only bare Git remote,
and a dedicated SQLite factory home. It makes no model, tracker, or forge calls and does
not change FRO-12, its authority, or production state.

The executed sequence checked:

1. Delivery without a recorded verification base is refused.
2. Both declared candidate gates execute successfully against the original base.
3. An independent host commit advances the integration remote after verification.
4. The old verification is refused at delivery; a preserved private branch missing the
   new integration commit is refused before verification.
5. Refusals preserve the exact candidate commit and uncommitted work.
6. Production clone transfer imports the current integration objects. An explicit fixture
   merge incorporates that base; both declared gates execute again, and the base guard accepts.
7. Missing independent-review evidence still blocks the authority guard. No review receipt
   is fabricated, and this probe never claims a review or delivery occurred.

Driver: `artifacts/runtime-integration-base-acceptance/run.py`.
Retained evidence: `report.json`, `sandbox-commands.jsonl`, and the original
and refreshed gate reports in that directory. Its host-only `integration.git` and
`integration-writer` retain the real remote advancement. The dedicated factory database and
frozen authority live under the separate `integration-base-acceptance/isolated-home-retry` subtree
of the existing CRUD protocol mount. The fixture uses
`/tmp/factory-integration-base-acceptance-20260907` independently on host and VM.

The two-app gate fixture is reused from monorepo replay acceptance. Its VM-only helper module
is synthetic; this probe does not establish product dependency installation, concurrent
admission, an actual independent review, or deployment readiness.

The first attempt exposed a fixture setup omission: the driver had not created its attempt
directory before calling `authority.record_request`. `setup-failure.stderr` retains that
failure. Only the fixture driver was corrected; a fresh dedicated database was used for the
successful run. No factory source change was needed.
