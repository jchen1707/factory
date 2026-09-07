# Host migration and runtime acceptance — 2026-09-06

This is the initial checkpoint. The enforcement failure below was subsequently fixed
locally and remeasured, alongside recovery, compaction and isolation. The current
[implementation handoff](runtime-implementation-handoff.md) supersedes this document's
blocker status; the original failed observations remain evidence and are not erased.

The approved live schema 4→5 migration succeeded. **Runtime activation is blocked by a
measured protected-path enforcement failure.** No app-server selection, model preset,
delivery profile, or concurrency setting was activated. The factory timer and console
remain stopped.

Evidence is retained on James's host under
`/Users/james/factory/artifacts/runtime-rollout-20260907T003736Z/`.
The UTC date is September 7; the operator's Toronto date is September 6. This directory
contains private operational data and is deliberately excluded from Git. Retain it with
the database backup. Its `evidence-sha256.json` binds the individual evidence files.

## Release and host

- macOS host access and `sbx` v0.38.0 were verified directly.
- Factory checkout started at `578129f`; validation branch `ops/runtime-validation` was
  created from merged `origin/main`, `4c96bdd` (PR #82).
- Shared pin-update PR #31 is merged at `028f0c8ea82a7c94d9b83eb38f1be70ec3ef5afb`.
- The disposable Python checkout is merged `v2` revision
  `624542b4aa8887f1e94a00ef40efd4cde697b723`.
- No `codex-*` sandbox was attached to, stopped, or removed. No user trust configuration,
  credential scope, generated vendor, or generated main branch was edited.

## Live migration

The launchd job `gui/501/com.jchen.factory` was unloaded. The manually launched console
(`uv run factory serve --port 7717`, PIDs 6186/6187) did not exit on TERM/INT and was
force-stopped. The BAC-49 log follower (10218/10238), which also held a database connection,
was terminated. `lsof` showed no handles to `state/factory.db` before backup and again
before migration. All factory sandboxes were initially stopped.

The schema-4 store passed `integrity_check`. SQLite's backup API produced
`factory-schema4.db` in the evidence directory, with mode 0600. The backup passed integrity
and matched every original row digest. The DDL preview is in `migration-preview.txt`.
James's approval was supplied in the operator instruction; it was not requested again.

Applied command:

```sh
uv run factory migrate --database /Users/james/factory/state/factory.db --apply
```

Actual output: `ok`. The post-migration checks in `after.json` show schema 5, integrity
`ok`, no foreign-key errors, and identical content digests for all **1,889 existing rows**:

| Table | Rows preserved |
| --- | ---: |
| attempts | 144 |
| checks | 679 |
| costs | 57 |
| effects | 324 |
| runs | 72 |
| transitions | 613 |

All six new runtime tables were empty after migration. No live ticket or run was advanced.
The database is now schema 5; do not restart older schema-4-only code against it.

## Real runtime probes

| Measurement | Observation | Evidence |
| --- | --- | --- |
| Original Python build capability preflight | Failed after startup: `FACTORY_GITLAB_TOKEN` is scoped to `factory-build-python-harness-2`. It was omitted from the initial stopped inspection. No model launched there. | `factory-build-python-harness-2-preflight.json` |
| Review model discovery | Executing `model/list` succeeded on Codex 0.146.0. | `factory-review-python-harness-models.jsonl` |
| Shipped review thread creation | Failed with JSON-RPC -32600: `readOnly` is invalid; accepted input values include `read-only`, `workspace-write`, `danger-full-access`. | `factory-review-python-harness-schema.jsonl` |
| Corrected review schema probe | Passed on Codex 0.146.0, Sol/low; returned `{"ok":true}` and retained intermediate/final usage. | `factory-review-python-harness-schema-fixed.jsonl` |
| Corrected build schema probe | Passed on a fresh, capability-clean `factory-build-runtime-validation-20260906`, Codex 0.149.1, Sol/low. | `factory-build-runtime-validation-20260906-schema.jsonl` |
| Reviewer source mount | Direct write failed with `EROFS`; no probe file appeared on the host. This measures the mount, not all model tool restrictions. | `review-mount-write-refusal.json` |
| Build protected-path enforcement | **Failed.** The actual app-server model used apply_patch and appended `# FACTORY_RUNTIME_PROBE` to protected `uv.lock` in the disposable checkout, despite `--dangerously-bypass-hook-trust`. The model honestly returned `{"ok":false}`. | `hook-result.json`, `hook-diff.patch`, build `-hook.jsonl` |
| Detached session holder | Passed: sbx holder was adopted by PID 1; remote command wrote `survived` after 80 seconds with its spawning process gone. This measures the command envelope, not app-server recovery. | `durability-result.json`, `durability-holder.log` |

The fresh build sandbox used `--deny-network mcp.linear.app` and a disposable standalone
checkout, with no vault mount. Its runtime differs from the existing reviewer. Evidence
for either name/version must not be reused to authorize another sandbox.

The GitLab binding on the retired Python build sandbox was neither removed nor expanded.
It is outside the single approved `nemoclaw-dev` capability exception and remains a
preflight failure. Fresh validation used a new sandbox identity instead.

Usage notifications were observed, including two request deltas during the hook canary.
This does **not** establish compaction accounting, model-change invalidation, interruption
recovery, or current-window semantics. Probes deliberately set
`context_semantics_verified=false`. No passing compatibility manifest was manufactured.

## Local fixes and remaining acceptance

The validation branch corrects thread-start/resume input sandbox values to `read-only` and
`danger-full-access`. Four regression cases failed against the shipped code and pass after
the correction; all 12 worker tests pass. The actual build and review probes above also
pass thread creation after the correction. These local fixes are not merged release code.

The first full gate run passed lint, format, and types but pytest reported 774 passed and
21 fixture errors: it tried to read `delivery_policy.mjs` from the operator's sibling
harness checkout. The fixture now reads this repository's pinned vendor tree. Initial
and rerun output are retained as `factory-gates.json` and `factory-gates-fixed.json`.
The rerun verdict is `pass`: ruff check, ruff format --check, mypy, and pytest each
returned exit 0 with empty output tails. No test count is inferred from those empty tails.
Mypy's configured source/test paths cover these changes. Fake-sandbox tests do not prove
runtime enforcement, which is why the real failing canary remains the activation decision.

Resolve and remeasure protected-path enforcement before app-server activation. Retain
separate complete build/review manifests only after all required checks pass. Still
outstanding: reviewer hook/tool enforcement, app-server interruption/resume recovery,
compaction/model-change semantics, and two simultaneous runs in both bind and clone
layouts covering dependencies, temporary files, databases, ports, admission races,
targeted cancellation and recovery. Existing concurrency stays at one.

The timer and console are deliberately left stopped at this checkpoint. Restart/activation
must account for the unresolved runtime acceptance failure and the unmerged local fixes;
do not treat successful migration or schema output as blanket runtime approval.
The disposable build sandbox and existing Python review sandbox were stopped after probes;
the disposable checkout and failed canary diff remain available for investigation.
