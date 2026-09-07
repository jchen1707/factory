# Accounting observation correction

The production collector could turn late compaction usage into a fresh context
reading, ignore the worker's explicit context invalidation, or abort on malformed
raw token counts before retaining already priced requests. These defects were
reproduced with the real collector and scratch SQLite stores using fixture events.

The collector now preserves the compaction wait until a valid different normal-turn
measurement arrives. Missing or malformed compaction identities keep context
unavailable for that invocation. Cumulative usage remains separate. Worker context
invalidations also survive a disconnect before compaction completes.

Malformed raw observations invalidate context and mark usage/pricing incomplete.
Valid normalized request estimates remain a known budget lower bound; repeated
collection does not inflate it. This does not invent missing measurements or rewrite
historical stores.

Thirty-five focused tests passed after the original three failures and four
missing/malformed compaction-identity failures were retained. Scoped lint, format
and type checks passed. Evidence, source hashes and intermediate failures are in
`artifacts/runtime-accounting-observation-correction/`. These are deterministic
collector regressions, not new provider measurements; final repository gates and
real adapter compatibility are recorded separately in the current handoff.
