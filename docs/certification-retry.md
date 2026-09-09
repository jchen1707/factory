# Explicit retry after failed certification

A transient hook failure left FRO-12 blocked even after a separate diagnostic
canary passed. Ordinary certification requests deduplicate by full fingerprint,
including failed jobs. Polling must continue to return that failure; it must not
silently spend on repeated probes. The diagnostic is not a production certificate.

The explicit `factory retry-certification --ticket FRO-12 --role review --job
<failed-id> --reason <operator-reason>` command requests one successor after fresh
identity observation and deterministic preflight. It reconciles the old job first,
requires its owning run to be paused with no active agent reservation, and retains
the original identity, failure, evidence, invocation records and accounting.
Wrong owners and changed identities are refused. The command holds the run lease.
It does not resume the workflow, approve probes, launch models or write tickets.
Use normal `factory resume FRO-12 --from reviewing` afterward; each invocation still
passes approval, budget and capacity admission.

Requests naming the same failed predecessor return the same successor, including
under concurrent connections and controller restart. If that successor fails,
further retry needs another explicit request naming the new failure. Replaying an
older request never creates another attempt. Normal selection uses the latest
sequence for the exact fingerprint; old failed attestations remain invalid.

## Schema 6 to 7 (approval required before production application)

The migration rebuilds `runtime_certifications`, retaining every existing column
value and adding `sequence` (existing rows receive zero) and `retry_of` (existing
rows receive NULL). The former fingerprint-only unique constraint becomes
`UNIQUE(fingerprint,sequence)`. The unique predecessor and self foreign key prevent
forked retry chains. No invocation, effects, run, accounting or settings rows are
rewritten. This migration runs transactionally and existing databases refuse to
open under new code until migration is explicitly applied.

Preview exact DDL with `factory migrate --database <database>`. James must approve
this new migration; earlier schema approvals do not cover version 7. Before applying,
disable/stop writers and console, verify absence, take an integrity-checked SQLite
backup, then deploy reviewed source and apply the migration. Restore services only
with code that understands schema 7. Rollback after new activity requires preserving
that activity; never overwrite new evidence with an old backup.

## Acceptance and remaining live work

Regression tests cover immutable old records, ordinary request deduplication,
concurrent explicit requests, restart/replay after a successor failure, stale and
wrong-owner refusals, active reservations, unchanged admission and schema preservation.
The implementation was preceded by a failing test of the missing retry operation.

After merge/deployment and approved migration, observe the current reviewer identity.
If it matches failed job `64df02bca42c45bb85bf1a50131a311b`, request its explicit retry.
If identity changed naturally, ordinary certification must validate the new identity;
do not fabricate a fingerprint change to escape a failure. Approve only exact new
probe invocations. Stop for evidence if hooks fail again. Only a complete six-check
production certificate allows independent review of the preserved FRO-12 attempt 2.
The intermittent hook/file-read cause is still unknown; this fixes retry availability,
not that underlying transient failure.
