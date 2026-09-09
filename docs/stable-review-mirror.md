# Preserve the clone mirror while reviewers hold it open

FRO-12's production recertification retry reproduced hook exit1 and missing authority
files. Its workflow repeatedly calls `review.start`, which calls `clone.fetch_back`
before certification/reviewer admission. Fetch-back previously removed and recreated
the host worktree on every call, including while detached certification was active.
An identical commit does not make that operation harmless: processes holding the old
directory retain an unlinked directory, not the replacement path.

A regression using real Git holds a directory descriptor across a repeated fetch-back.
The original code fails with FileNotFoundError opening an unchanged tracked file.
A zero-model comparison in the actual reviewer VM reproduced the same failure for
MANIFEST.json (errno2) with unchanged Git HEAD. The corrected source preserves the
open directory and the read succeeds. No invocation was added by either measurement.
Evidence: live factory artifacts/runtime-pr96-deploy-20260909/mirror-repro.py,
mirror-original.json, mirror-fixed.json. This establishes the directory invalidation
mechanism; successful full app-server hook recertification is still required after
deployment before claiming the production hook failure is resolved.

Fetch-back still fetches the latest VM branch under the project Git lock. If the
registered host mirror is clean, has no in-progress Git operation, and already matches
FETCH_HEAD, retain it and refresh the run's recorded worktree. Dirty mirrors block
without deleting work. A changed mirror cannot be replaced while any run agent has an
active reservation. Clean, changed, unused mirrors retain the existing rebuild and
resulting-HEAD verification behavior. The factory still does not reset, merge, or
implement application code on the host.

Tests cover an open reader across unchanged refresh, tracked/untracked dirty evidence,
unchanged refresh with an active reviewer, refusal to replace changed code under that
reviewer, and the existing update-to-new-commit behavior. The full clone tests use real
Git with a fake sandbox boundary; the retained VM measurement covers the Linux seam.

No schema change. PR96 is already deployed at d7e2b51 and schema7 approved/applied.
FRO-12 is suspended from reviewing on attempt2; its work, native thread, failed
certification records and accounting are preserved. The retry canary failed and the
remaining eight probes were not approved. The new failed job is
b92549e2fb40429b891f2c0579a3eb00; original64df... is unchanged. Writer/console restored.

After merge/deployment, obtain fresh identity and explicitly retry the latest failed
job if it still matches, then resume the preserved run. Approve only exact certification
probe invocations. All six compatibility assertions must pass before independent
review. Stop on a recurring hook failure; do not loop paid retries or promote a
zero-model directory test into a compatibility certificate.
