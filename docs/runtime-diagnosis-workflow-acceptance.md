# Actual diagnosis and repair workflow acceptance

This bounded experiment uses the production planning, implementation, verification,
approval, and invocation-accounting paths against an operator-approved local fixture.
It has no tracker or delivery adapter. Its purpose is to measure one fresh model
diagnosis followed by one bounded code repair and independent verification, stopping
at `reviewing` without starting reviewers or delivery.

**The bounded workflow passed.** Corrected diagnosis attempt 3 authorized one
separately approved Terra/medium repair. Production independent verification ran
the declared gate and reached `reviewing`; no reviewers or delivery were started.
The reused build sandbox is stopped. This closes this fixture's diagnosis/repair
path, not every runtime or delivery acceptance requirement.

## Scope and setup

Evidence is under `artifacts/runtime-diagnosis-workflow-acceptance/`. Its separate
SQLite store holds synthetic local run `DIAG-1`; this is not a Linear ticket.
`accept.py` is the reproducible controller. The scratch repository and its factory
home occupy a dedicated subdirectory of the existing build protocol mount:
`diagnosis-workflow-acceptance/`. No FRO-12 source, store, branch, dependency
environment, or production settings are used as the fixture.

The repository has a standard-library Python function and one declared unittest
gate. The approved seam is `service.double(int)`, with positive, negative, and zero
inputs returning twice their values. Two committed operator fixture versions remain
in history: returning the input, then adding one. Both are intentionally unsuccessful
fixture edits, not invented model attempts. The first command's real failure is
retained separately; the current failure was reproduced in the actual build VM using
the declared gate runner and bound by production `handoffs.record_failure`.

Merged harness source `f7917ce3a66f916109c5c2d621d4b621bc6fca87` was generated into
the scratch repository with `vendor_sync.py`. An actual Core delivery authority
snapshot, revision 1, freezes the gate configuration and shared workflow contracts.
The snapshot is in the allowed protocol mount so the executing runtime can read it;
host-retained digests and integrity validation govern it. This does not establish
a new operating-system read-only authority mount.

The synthetic run starts at `verifying`, attempt 1, with explicitly identified
deterministic fixture failure evidence. No implementer result or prior model usage
is fabricated. This initialization does not exercise intake or initial provisioning.
Subsequent diagnosis, repair, and verification use the production step APIs.

The existing exact build app-server compatibility manifest applies to
`factory-build-crud-20260907`; capability and hook preflight were repeated before
launch. The inherited template authentication was again observed invalid.
The Volume preset selects Sol/high for fresh diagnosis and Terra/medium for repair.
Only the isolated run's next invocation is approved at each boundary.

The committed history, an unrelated tracked dirty note, and an untracked operator
note must survive diagnosis and repair. The approved fixture explicitly excludes
other repositories, dependency installation, and subagent spawning.

## First measured failure and corrected retry

The first diagnosis exited 0 and correctly reproduced the code defect, identified
the actual prior attempts, and preserved implementation, commits, and both notes.
It wrote the two required markdown handoffs and returned schema-valid `repair`.
Its `reproduction_evidence` field contained explanatory prose citing both a fresh
reproduction log and the host's verifier artifact. The shared contract and schema
did not state that the field must contain only the host's exact relative identity.
Production authorization correctly refused it as `diagnosis-not-reproduced`.
No repair episode or builder invocation was created.

The correction supplies `reproduction_evidence` and its recorded digest in the
handoff and immutable invocation request. Only diagnosis's generated schema permits
the exact identity or an empty string; empty supports human/environment routing
and cannot authorize unproved code repair. New requests also bind authorization to
the snapshotted path and hash, refusing a newer host record at the same path.
This last case is a tested provenance invariant, not a reproduced live race.
Readiness/test-design schemas and legacy admission behavior remain supported.

The matching shared instructions are in CI-green, **unmerged** harness PR33 revision
`36bb716`. Only this scratch repository was regenerated against that source and its
authority explicitly replaced with revision 2. Its delivery requirements and gates
did not change. This is fixture validation of the proposed correction, not activation
of unmerged shared code. Production `recovery.resume` created attempt 3 after the
new `3:plan:1` approval held; attempt 2 and its original evidence remain preserved.

`diagnosis-collection-failure.json`, `retained-failed-collection/`, and
`retained-first-plans/` retain the original failure. `authority-refresh.json` records
both policy revisions. The standalone store recorded its own local block without
contacting a tracker. No result field was manually repaired or relabeled as accepted.

The factory correction is committed at `ed64ab7`; its four gates passed in
`artifacts/runtime-diagnosis-contract-evidence/factory-gates.json`. Independent
bounded review found no concrete defect. The corrected real diagnosis returned
the exact required identity, and production `plan.collect` admitted one repair
through `authorize_repair`. Both required handoffs were retained with the attempt.
Original implementation, commits, and operator-note contents were unchanged.
The next `3:implement:1` approval held before the builder was explicitly approved;
production `implement.start` then launched Terra/medium under the same authority.

## Actual repair and independent verification

The builder read the collected diagnosis and test scenarios, added the zero-case
regression, and ran it before changing implementation. Captured command output
shows exit 1 with `1 != 0`. It then repaired only `service.py`, reran the focused
test successfully, and ran all three unittest cases successfully. Its commit is
`ddcba1ca161c419f13cf2af49dcc12b18925492a`. `red-green-commands.json` retains those
actual commands, exit codes, and outputs from the app-server stream. No builder
collaboration events were observed.

The first independent verification correctly refused `evidence-mismatch`: this
minimal fixture declared gated extensions but omitted dispatch paths/files, so
the runner marked the claimed gate `skipped_unchanged`. This was a fixture setup
omission, not another factory source defect. The operator retained that report,
added explicit `tests` and `service.py` dispatch paths, committed the configuration
correction, and explicitly replaced only the scratch authority with revision 3.
Gate argv and delivery requirements were unchanged. Supported
`recovery.resume(..., from_state="verifying")` launched deterministic verification
again without another model invocation.

The final independent `integer acceptance` gate actually ran and passed, exit 0.
Production `verify.collect` accepted the builder claim, recorded revision-3
verification evidence, closed the failure episode, and transitioned to `reviewing`
at attempt 3. Authority integrity passed afterward. The final fixture HEAD is
`65a6baf923bee6fdb77e0dc24bed3c2d8dd04f64`; the original commits remain ancestors.
Both operator-note contents are unchanged, the tracked note remains unstaged,
and the untracked note remains untracked. The effects ledger is empty.

The fixture controller then stopped `factory-build-crud-20260907`. It did not
run a reviewer, create a ticket or PR, alter FRO-12, or activate production.

## Invocation accounting and limits

The store retains three actual model invocations, including the refused diagnosis:

| Invocation | Model / effort | Input / cached input / output | API-equivalent estimate |
| --- | --- | --- | --- |
| Initial diagnosis | Sol / high | 396,494 / 351,872 / 12,372 | $0.5666768, complete |
| Corrected diagnosis | Sol / high | 575,196 / 493,824 / 13,259 | $0.7881976, complete |
| Repair | Terra / medium | 292,674 / 256,256 / 4,217 | Incomplete |

The known subtotal is **$1.3548744**, not the complete workflow cost or Codex
account charges. Builder usage is complete, but all twelve request estimates
report missing service-tier or context-band evidence, despite the invocation's
`app-server` / `standard` metadata. This remains an accounting follow-up; no price
was fabricated. Earlier host acceptance independently measured the two-repair
ceiling and unchanged-evidence refusal. This real fixture exercised one admitted
model repair, not two model repairs or exhausted live repair limits.

The setup uses a synthetic local run, an existing VM/protocol mount, and direct
production step APIs with a disabled tracker adapter. It does not establish intake,
initial sandbox provisioning, operating-system read-only authority isolation,
review/delivery, or recovery for every runtime failure. Its controller stops before
those steps. The shared output-contract correction remains unmerged PR33 source
within this explicitly isolated fixture.

## Final retained evidence

All paths below are within `artifacts/runtime-diagnosis-workflow-acceptance/`:

- `result.json`: final report, transitions, checks, authority, inventory, and all
  invocation accounting; `state/factory.db` is the preserved isolated store.
- `diagnosis-collected.json`, `repair-collected.json`, and approval/launch records:
  production step results and the separate invocation boundaries.
- `retained-protocol/`: original failed reproduction and all three model streams,
  result files, collected markdown, and final gate evidence.
- `verification-dispatch-failure.json`, `retained-dispatch-failure/`, and
  `dispatch-authority-refresh.json`: refused skipped gate and explicit correction.
- `stopped-inspect.json`: terminal sandbox observation.
- `evidence-sha256.json`: digest inventory of retained reports, scripts, store, and
  raw evidence, excluding the independently pinned shared-source checkout.

Raw transcripts remain local operator evidence; publication of this report does
not publish the private artifact directory.
