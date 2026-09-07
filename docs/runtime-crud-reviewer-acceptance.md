# CRUD reviewer runtime acceptance

Measured 2026-09-07 UTC using only fresh `factory-review-crud-20260907`, created
through production `_review_scratch`, `_review_spec`, and `SbxAdapter.ensure` for
isolated home `artifacts/crud-runtime-test`. No Linear calls, target source edits,
production Store/settings writes, or existing project sandbox actions occurred.
The reviewer is stopped after measurement.

Template `codex-pnpm:v1` contains **codex-cli 0.146.0**, image digest
`sha256:8ab3deaa75f9c10fb0e95d866a57280bc1494950c1a90b2cc636c8b1391fd574`.
The production reviewer spec mounts writable protocol ground at
`state/review/factory-crud-verification`, the target project read-only, and its
isolated authority tree read-only. Capability preflight found only the declared
MCP gateway proxy binding. The inherited template environment name acknowledged
in the isolated registry was tested with `gh auth status`; it explicitly reported
its value invalid before any model call. No values were printed or changed.

## Measured defect and correction

The current worker initially failed closed before thread launch with
`project hook discovery is incomplete or disabled`. Exact runtime metadata and
its generated JSON schema showed that 0.146.0 has **no `async` field** in
`HookMetadata`. All four discovered handlers matched their repository definitions
on command, event, type, matcher, timeout and status message; the sole mismatch
was absent metadata versus the synchronous default `false`.

The worker now treats an absent `async` field as false only for that comparison.
Explicit asynchronous requirements still fail when metadata cannot establish them;
other handler checks are unchanged. Four regressions failed before the fix and
passed afterward; the explicit async cases continued to refuse. All 46 worker
unit tests passed. Root owns final full gates for the combined changes.

Corrected worker SHA-256:
`f0bf1c95b6b98b9de84812f413bf42e6ccdf3ee74bd6ade2c7d1c0d92727c11f`.

## Observed effects

- Actual model tool attempt triggered a synchronous project hook refusal for
  `packages/contracts/openapi.json`; the protected file still matches Git HEAD.
- A direct write attempt to the target mount returned `Read-only file system`
  and exit 1; no canary file exists. The model's separate write attempt produced
  hook events, but 0.146 omitted its tool response from app-server notifications,
  so the retained direct mount probe establishes the filesystem effect.
- The exact corrected worker returned schema-valid `{"ok":true}` and exit 0.
- A real detached worker began `sleep 180`. Its spawning host process exited;
  its holder was adopted by PID 1 while production `poll` reported running.
  Production `kill_group` interrupted the invocation and the wrapper recorded 143.
- A fresh exact worker resumed that same thread and completed schema-valid output.
  The retained baseline was 11,090 input / 137 output; cumulative totals afterward
  were 22,435 / 152. Worker usage correctly counted only **11,345 / 15** new tokens
  with complete usage and pricing.
- Default-window raw app-server turns, actual compaction, explicit Sol→Terra
  switch, and same-thread resume completed. Cumulative input totals progressed
  11,058 → 22,143 → 28,535 (compaction) → 39,817 → 54,709 → 69,627.
  Compaction reset the current-context observation while cumulative billed usage
  remained monotonic; repeated unchanged totals were retained as such.

Total observed input across these probes: **126,253 tokens**, within the 150k
initial budget. No model attempted CRUD implementation.

## Retained evidence and scope

Private operator artifacts live in
`artifacts/crud-runtime-test/reviewer-compatibility/`. `verified-summary.json`
contains executable assertions' results; `evidence-sha256.json` binds the retained
raw protocol copy, attempt envelopes, worker snapshots, schemas, failed metadata,
regression output, capability/mount checks, and stopped inspection. Do not publish
raw transcripts or private operational artifacts.

`factory-review-crud-20260907.json` passes production `validate_compatibility` for
this exact sandbox/version, with all six required checks backed by measured
effects. It is operator-owned outside the candidate workspace. It is reviewer
runtime compatibility evidence, not project concurrency evidence or production
activation. The target's build sandbox requires its own manifest.

Limits: no automatic model reroute, natural 80% default-window crossing, or
worker-triggered compaction was induced here. The raw telemetry script measured
runtime semantics before the fix and did not call the worker's coverage helper;
canary and interruption/recovery did use the exact corrected worker. The original
worker snapshot remains in the failed canary attempt. Detached envelope recovery
is not proof of the full `factory resume` orchestration path. Concurrent-project
resource isolation is a separate acceptance requirement.
