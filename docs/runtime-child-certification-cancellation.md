# Child certification cancellation found during staged rollout

On 2026-09-08, production-spec synthetic run `aa65e9816f434c57` exposed a lifecycle gap.
Its parent first requested child `05f7e7bd27604a86a9ce9c28c273414d`, then accidentally
polled malformed handle `05f7e6f15c774da18`. The broker correctly refused that unknown
handle. The parent interpreted it as refusal of the original task and ended. Its pending
child was cancelled before application launch, but the child's paid interrupt probe
remained active because certification leases have no child `parent_id` and the child
advance loop skips cancelled requests. The original parent result, calls and all costs
are retained; this is a failed writable rollout attempt, not passing acceptance.

Evidence: `/Users/james/factory/state/runtime-rollout-merged-20260908/isolated-write/`.
`handle-replay.json` independently confirms valid-handle lookup and unknown-handle refusal.
`drain-signal.json` records operator containment against the exact VM generation and owned
process group. `failed-checkpoint.json` records accounting replay and zero remaining leases.
Both cohort VMs were stopped, preserving source and native threads. The project was returned
to read-only delegation; the writer remains unloaded and disabled. No target files changed.

The correction links child certifications through the existing host-owned child preparation
effect and certification identity. Cancellation retires pending/checking certificates and
signals only their recorded active probes, after checking VM generation and process ownership.
The effect is persisted before signalling; an uncertain acknowledgement is never resent.
Terminal observation retains accounting before capacity release. Parent finalization also
waits for these probes, and paid admission rechecks job/request ownership in its transaction.
No schema, worker, routing, model, shared contract or target dependency change is required.

Eight regression cases exercise explicit cancellation and parent-exit draining with valid,
replaced-generation, changed-process and lost-acknowledgement evidence. They also assert that
another prepared probe cannot launch after cancellation, a sibling request is preserved,
restart does not repeat a signal, and terminal usage is retained. The initial regression
failed because reconciliation issued no cancellation signal; it passes with the correction.
The focused cancellation/runner/admission suite passes 44 tests.

A real zero-model check at
`/Users/james/factory-child-certification-cancel/artifacts/child-certification-cancel/real-1/`
ran detached parent and probe processes in two disposable VMs. Parent exit automatically
cancelled the pending child, retired its certificate, signalled the exact probe once, observed
exit143, and released all leases. Three replays added no signal. Both recorded generations
were removed and verified absent. This measures process lifecycle, not model compatibility;
the isolated fixture's identity is not a compatibility attestation.

After James merges the fix, deploy the merged host source and repeat the bounded writable
cohort. Keep the exact returned child handle; an unknown-handle refusal does not mean the
original request was refused. Finish child/result/mount/accounting checks, refresh intake
eligibility, then enable/bootstrap the writer and observe normal ticks. Earlier read-only
acceptance is retained; no product ticket completion or hardening is required.
