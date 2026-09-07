# Reliability implementation handoff

Updated 2026-09-06. The approved four-repository plan remains the specification.
Implementation and local verification are complete; real runtime acceptance and rollout
remain outstanding. See [runtime rollout](runtime-rollout.md) for controls and evidence formats.
This replaces the earlier pre-compaction checkpoint; do not restart its completed tasks.

## Boundaries and environment

No tickets, live runs, production databases, or deployments were changed. After the initial
implementation checkpoint, James authorized continuing release work; feature branches and
draft PRs are now published (see release status below).
Historical BAC-53/54/49/22 supply curated evaluation fixtures, not captured runtime transcripts.
No app-server activation or concurrency increase was performed.

`sbx` is absent. Codex CLI 0.153.4 is available, but schema/protocol tests cannot establish
sandbox hook enforcement, isolation, durability, recovery, or usage semantics.
Keep activation gated on actual digest-bound evidence from the executing runtime.
Do not touch `codex-*`, user trust configuration, generated `main`, or `~/.factory/`.
Never edit generated vendors; use the shared sync process.

The workspace `.venv` belongs to macOS. On Linux use `node scripts/python_env.mjs uv run ...`.
The declared factory gates already use this adapter and preserve explicit sandbox environments.
Do not rebuild or delete the workspace environment.

## Repository state

| Repository | Branch | Implementation revision |
| --- | --- | --- |
| factory | `feat/reliability-observability-profiles` | Commit containing this handoff; baseline `c74866fbc084ff273f2c988d13ee1aebf85dc180` |
| harness | `feat/delivery-contracts-and-presets` | `a024205423b60152a56011b617c97bbf360d03ee` |
| python-harness | `feat/composable-stack-presets` | `6e69ce3f8f011c511ab2cfaa70f1c30bd749404f` |
| frontend-harness | `feat/composable-stack-presets` | `e0601d03c6d894922bf6a41fa1f9c158ae0085c5` |

All three consumers were regenerated through `vendor_sync.py` against `a02420542`
(the release formatting fix on top of `d87bfcb`). Consumer table revisions above are the
initial implementation commits; each PR also contains the final vendor-pin update.
Shared changes must reach `harness@v2` before remote freshness can pass. Do not move refs
to simulate publication. Stack changes likewise target their respective `v2` branches.

## Implemented

- Normalized invocation/thread telemetry, context freshness, compaction/model invalidation,
  idempotent accounting, dated API-equivalent estimates, incomplete-history labels and budgets.
- Compatibility-gated app-server selection across implementation, diagnosis and review;
  retained legacy exec; metadata-only executing-runtime model validation for explicit presets.
  Actual selected model, effort and preset are retained per invocation. Reviewer selection
  occurs once so accounting cannot race a settings change into recording another selection.
- Trusted immutable policy snapshots with inventory/hash/path checks, nested app inheritance,
  explicit deferrals, policy replacement invalidation and shared Stop/reviewer interpretation.
  Target-supplied JavaScript is never executed on the host as the policy interpreter.
- Shared ticket readiness, optional planning, vertical test design, fresh diagnosis and concise
  handoff contracts. Reproducible evidence authorizes at most two repairs per failure episode;
  environment, authority, scope and disputed findings retain their separate dispositions.
- Project/run approval controls, per-axis reviewer accounting and budget/approval boundaries,
  invocation cards, effective profiles/deferrals and concurrency/waiting controls.
- Atomic admission, measured per-run isolation, recorded cleanup identities, clone/writable
  mount separation, serialized shared Git operations and current-integration-base checks.
- Reviewable schema 4-to-5 migration, historical evaluation fixtures and outcome/cost metrics.
- Shared scaffold composer validation and atomic publication; minimal Python/TypeScript and
  existing FastAPI/React-Vite presets, optional components, all stack reviewer inputs and
  generated gate/protection declarations. Existing project dependency choices are preserved.

## Verification

Latest factory command:

```sh
node .agents/vendor/harness/hooks/gate_report.mjs --force --json
```

Retained output: `/tmp/factory-completed-gates.json`. Actual results:

| Gate | Status | Exit | Output tail |
| --- | --- | --- | --- |
| ruff check | pass | 0 | empty |
| ruff format --check | pass | 0 | empty |
| mypy | pass | 0 | empty |
| pytest | pass | 0 | empty |

Verdict: `pass`; all four declared gates ran. Mypy only checks configured paths (`src`,
`tests`), which include the new code. Pytest uses fake sandboxes and fixture transcripts;
it proves control-plane behavior, not runtime compatibility. No test count is inferred from
empty gate output. Console behavior is covered through integration requests; no live console
or sandbox was exercised. Final preset expectations explicitly assert `volume`, and a
regression covers settings changing after role selection.

Other retained verification from the implementation session:

- Shared layer-A contract/generation checks: `/tmp/harness-last-check.txt`, all checks passed.
  Its submodule-dependent checks were skipped; sibling consumer source and generated trees
  were validated separately below.
- Consumer source: `/tmp/python-harness-final-consumer-gates.json` and
  `/tmp/frontend-harness-final-consumer-gates.json`.
- Generated consumer trees: `/tmp/python-harness-final-generated-gates.json` and
  `/tmp/frontend-harness-final-generated-gates.json`.
- Installed presets with actual composed review inputs: `/tmp/python-preset-final/` and
  `/tmp/frontend-preset-final/`.
- Installed optional component combinations: `/tmp/optional-compositions-gates.json`.
  Python FastAPI/RAG/agents/OpenAI, TypeScript/server providers, and React/Vite/REST/GraphQL.

These reports passed their applicable gates. Stack integration/E2E gates were not applicable;
Lighthouse remains disabled. Temporary evidence paths are local and may expire; retain any
needed raw reports before cleaning `/tmp`.

## Remaining acceptance and release work

1. Review and publish shared/consumer changes in dependency order; regenerate using the
   normal process when necessary and verify remote freshness after publication.
2. On a machine with `sbx`, capture actual intermediate usage, compaction resets, model changes,
   hook/schema enforcement, read-only review, detached survival and interruption recovery.
   Validate both build and review manifests. Fixtures are not substitutes.
3. Measure two simultaneous runs in both bind and clone layouts, including dependency/temp/
   database/port isolation, admission races and targeted cancellation/recovery.
4. James reviews the schema migration before application. Keep existing adapter, routing and
   concurrency until the operator explicitly selects validated replacements.
5. Collect real outcomes and compare interventions, repeated failures, completion and estimated
   cost per accepted change. Historical missing usage remains incomplete.

The implementation does not establish real sandbox acceptance or measured performance gains.

## Published release status

Draft PRs: [shared harness #30](https://github.com/jchen1707/harness/pull/30),
[Python #76](https://github.com/jchen1707/python-harness/pull/76),
[frontend #54](https://github.com/jchen1707/frontend-harness/pull/54), and
[factory #82](https://github.com/jchen1707/factory/pull/82).

Factory was merged with current `origin/main` without source conflicts and its four gates
passed again (`/tmp/factory-release-gates.json`). Its PR explicitly includes the earlier local
`c74866f` human-review acceptance commit. Shared checks were rerun after initializing pinned
submodules: `/tmp/harness-release-check.txt` passed including mounted-stack config and shared
generator checks, with 149 shared hook tests.

Initial GitHub checks additionally passed Python Linux/Windows verification and database
integration, plus frontend Linux verification, generation, E2E and Lighthouse. The configured
local Lighthouse gate being disabled does not mean the independent CI job is disabled.
Consumer freshness remains dependent on shared PR #30 merging into `v2`; do not weaken it.
Shared generated-tree CI exposed formatting in two new source files; these are fixed in source
and consumed through a fresh vendor sync. Consult current PR checks for the latest commit status.
James retains merge, production migration and activation decisions.
