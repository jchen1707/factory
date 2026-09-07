# Invocation-local nested-agent restriction and fork calibration

Measured 2026-09-07, Codex 0.146.0, exclusively in
`factory-build-crud-20260907` (template `codex-pnpm:v1`, image
`sha256:8ab3deaa75f9c10fb0e95d866a57280bc1494950c1a90b2cc636c8b1391fd574`).
Evidence is under `artifacts/runtime-nested-fallback/`; its
`evidence-sha256.json` binds the retained scripts, raw protocol, preflight,
scoped rollout records, scratch stores, clean git status, and stopped inspections.

## Supported behavior

Opt-in app-server launches now set `agents.enabled=false` in both `thread/start`
and `thread/resume` configuration. Native nested agents are unavailable so every
supported model invocation remains scheduled, approved and budgeted by the
factory. Independent factory review invocations remain available. Legacy exec is
unchanged. No production configuration, default adapter, user Codex configuration,
ticket, FRO-12 state, or product file was changed by these probes.

This closes child admission for the supported new adapter through a measured
restriction. It does not implement arbitrary nested orchestration or an atomic
child-launch veto. Historical nested usage remains incomplete; no backfill or
invented child accounting is applied.

## Real effect, not accepted flags

Each active probe used a dedicated scratch repository, exact hook discovery and
trust configuration, capability preflight, and an explicit request to spawn one
child only if the tool existed. Otherwise the primary task was to return
`NO_NESTING PRIMARY_OK`. No file or shell work was requested of these models.

| Evidence directory | Invocation configuration | Observed effect |
| --- | --- | --- |
| root | CLI `features.multi_agent=false` | **Insufficient:** parent spawned a child and returned `NESTING_AVAILABLE`, although feature metadata reported false. |
| `combined/` | Both CLI switches plus both thread overrides false | Primary completed, no child identities. |
| `thread-agent-disabled/` | Only thread/start `agents.enabled=false` | Primary completed with `NO_NESTING PRIMARY_OK`, no child identities. |
| `resume-disabled/` | Only thread/resume `agents.enabled=false` | Previously nesting-enabled parent completed with `NO_NESTING PRIMARY_OK`, no new child identities. |

The resumed parent was `01a07a67-1df3-7450-a5e7-90f9774838c7`; its
preceding turn had actually spawned the fork-calibration child below. The
resume override therefore demonstrates removal on an existing capable thread,
not only a new thread. The response's `multiAgentMode: explicitRequestOnly`
survives even when tools are disabled; it is not an effective-tool assertion.
Requested and observed parent model/effort were `gpt-5.6-sol` / `low`.

Preflight retained only declared capability environment names, verified no
nonexcluded capability secrets, and confirmed the inherited GitHub token was
invalid before model calls. Every driver stopped the owned VM in `finally`.
These are controlled availability probes, not a proof against arbitrary shell
attempts to construct a new model client; existing sandbox capability boundaries
remain necessary.

## Fork-safe usage measurement

`fork-baseline/raw.jsonl` records one parent and exactly one full-history child,
with both terminal notifications and scoped runtime rollouts. The parent first
returned `CALIBRATION_ANCHOR_Z7`; the child recalled that marker from inherited
history. Both executed `gpt-5.6-sol` / `low`, confirmed by their own turn IDs in
`turn_context`, not by the unrelated rate-limit bucket label.

Parent: `01a07a67-1df3-7450-a5e7-90f9774838c7`.
Child: `01a07a67-382e-73c2-9276-7c883f99fe78`.

| Counter | Copied child baseline | Child own increment | Parent total, both turns |
| --- | ---: | ---: | ---: |
| Input | 11,804 | 11,977 | 47,873 |
| Cached input | 8,192 | 10,240 | 39,936 |
| Output | 12 | 27 | 125 |
| Reasoning output | 0 | 13 | 0 |

The child's first live cumulative vector includes the earlier completed parent
turn. The scoped child rollout contains that copied token-count record before
its own turn context. Subtracting this exact inherited baseline from the child
total equals its sole own `last` vector in every counter. Parent cumulative
usage equals only its four own increments and does not absorb the child's work.
`fork-baseline/analysis.json` asserts this conservation and records the deduplicated
trace total: **59,850 input / 152 output** (50,176 cached input, 13 reasoning
output). These subset counters are not added again to input/output.

This proves naive parent-plus-child cumulative addition double-counts inherited
history in this trace. It does not establish a general fallback baseline for
missing events, resumed children, retries, or older historical runs. No billing
invoice completeness claim is made.

## Source and verification

`src/factory/agent/app_server_worker.py` now applies the minimal measured thread
override alongside existing invocation hook trust. Worker SHA-256:
`f2b3751989c9810250554ec4b35dfcb9eec07b00d11fc60b0a6f81e9168b93be`.
The four start/resume × read-only regression cases failed before the change;
all 56 worker tests then passed, as did targeted Ruff check and format check.
The real probes establish exact protocol behavior; final worker compatibility
must bind this changed digest. Prior manifests for older worker hashes are not
evidence for this source version.

## Exact paths and handoff

Host evidence root: `/Users/james/factory/artifacts/runtime-nested-fallback`.
Runtime writable protocol mount:
`/Users/james/factory/artifacts/crud-runtime-test/state/clone/factory-crud-verification`.
Dedicated subdirectories beneath that mount were `nested-fallback`,
`nested-fallback-combined`, `nested-fallback-thread-agent-disabled`, and
`nested-fork-baseline`; each used its own `repo` checkout. Resume used only the
last scratch repo and its known parent. Runtime scoped rollouts are under
`/home/agent/.codex/sessions/2026/09/07/`; exact returned paths are retained in
the corresponding `raw.jsonl` rollout envelopes. No other sessions were read.

The sandbox is stopped and released. Worker/tests are released to the root;
no further VM/model calls or worker edits are planned by this task. Remaining
acceptance is the coordinated final-digest compatibility and full repository
gates. Full nested orchestration remains outside the supported adapter scope.
