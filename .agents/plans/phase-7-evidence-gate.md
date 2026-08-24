# Phase 7 — evidence gate, first check (2026-08-24)

Written 2026-08-24, the session after Phase 6 closed. Phase 6 is **complete and on
`main`**: the close-out landed in PR #60, and PR #61 (`chore/vendor-phase-6-doctrine`)
bumped the factory's *own* `.agents/vendor/harness` pin to `3442cb6` — the "not done"
hygiene item the close-out listed. `main` is at `23db3d3`.

`SOFTWARE-FACTORY-PLAN.md` is the authority and the only plan file you may edit. It is a
**protected path** (`harness.config.json` `hooks.protected`): the `protect_paths.mjs` hook
refuses agent edits to it, because a change to it is a re-plan, which is James's decision.
The Phase 6 plan edits were authored by James (PRs #59, #60), not by an agent. This file is
the agent's evidence record; the proposed plan insertion for James is at the end.

---

## What Phase 7 is

`SOFTWARE-FACTORY-PLAN.md` §19 Phase 7 is the authority. It is **trigger-gated**: the plan
says *"Do not start this phase without the trigger"* and *"Until a trigger fires, this
phase is a paragraph, not code."* The three triggers:

| Trigger | Threshold | Then consider |
| --- | --- | --- |
| A lost sandbox costs a ticket | ≥ 2 occurrences in 30 days that the §16 recovery did **not** save | Temporal or Restate for journal replay |
| Concurrency exceeds the laptop | ≥ 3 tickets queued behind a busy machine for > 4 h, twice | Self-hosted environments (Anthropic, Team/Enterprise) — read the docs before building a queue |
| Wall-clock dominated by passthrough I/O | P0-10's ratio > 3× and it does not improve | `sbx create --clone` (in-container clone, wired back via the `sandbox-<name>` git remote) |

"Taking care of Phase 7" therefore means **running the evidence gate** and recording the
verdict — the same move Phase 6 made ("the evidence gate did the work"). It does **not**
mean building a durable-execution engine, a remote runner, or the `--clone` path. Until a
trigger fires, that would violate the plan.

---

## Evidence gate — 2026-08-24

Data sources: `state/factory.db` (tables `runs`, `attempts`, `transitions`), the
`logs/daemon.out.log` transcript, and `docs/discovery/p0-10-gate-timing.md`. Counts queried
directly, not asserted.

### Trigger 1 — a lost sandbox costs a ticket — **not fired**

The relevant events are sandbox losses mid-run (the `orphaned` outcome) and whether §16
recovery saved them.

- **2 `orphaned` attempts**, both FRO-11 (`9bf53c80`): `implementing` att 1 and `planning`
  att 3. The daemon log shows both recovered by §16 resume-by-session —
  `implementing:orphaned → resumable:resume(session-intact)`. FRO-11 reached `completed`
  at attempt 4. **§16 recovery saved both.**
- **3 transitions to `failed`** are on record, and none is a sandbox loss:
  - FRO-7 (`b1aa978`): `max-reruns-reviewing` — 3 reviewing re-runs against a 3 ceiling.
    This is attempt-budget exhaustion, the designed terminal-for-human state. FRO-7 was
    re-run to `completed` (`40d1d56`) on a separate row, then this row was `cancelled`. Not
    lost.
  - FRO-11 (`9bf53c80`): `ladder-exhausted` ×2 (rung 4). Re-authorised via the
    human-bound `failed → resumable` transition (§5.3) and re-run to `completed` at
    att 4. Not lost.
- **No run is in state `failed` today.** The `runs` table groups to `cancelled` 34,
  `blocked` 11, `completed` 6. Every `failed` was recovered — by §16 (orphans) or by
  James's re-authorisation (budget exhaustion) — and no ticket was lost.

**Count of sandbox losses that §16 recovery did not save: 0.** Threshold ≥ 2. **Not fired.**

This is the highest-value trigger (R4, the §23 tradeoff that named this phase): the recovery
machinery Phases 4 and 4.5 built has absorbed every sandbox loss the real runs have
produced.

### Trigger 2 — concurrency exceeds the laptop — **not fired**

The factory runs serially: one writer per project (`concurrency.per_project = 1`), 60 s
poll. The 11 `blocked` and 34 `cancelled` rows are **not a queue backlog** — they are
re-appearing intake rejects (BAC-4 and FRO-7 in `Done`, still labelled `ready-for-agent`,
re-intaken every tick as `blocked: state-not-todo`) and cancelled reruns of the same
tickets. No event of ≥ 3 tickets queued > 4 h behind a busy machine. **Not fired.**

To quiet the re-appearing rejects (orthogonal to Phase 7): remove `ready-for-agent` from
the `Done` tickets, or `launchctl unload ops/com.jchen.factory.plist`.

### Trigger 3 — wall-clock dominated by passthrough I/O — **not fired**

P0-10 (`docs/discovery/p0-10-gate-timing.md`) measured the sandbox **~2× faster** than the
host on every Python gate:

> *"The sandbox is faster than the host, roughly 2×, on every gate. The virtiofs passthrough
> is not the bottleneck at this repository size; macOS process spawn is. The whole Python
> gate suite is under three seconds either way."*

The ratio is ~2× in the **favourable** direction, not > 3×, and it is not a wall-clock
problem. (The real frontend finding in P0-10 — a bind-mounted `node_modules` is not loadable
in the Linux VM — is a stack-adapter matter already handled by `requires` probes and the
`codex-pnpm:v1` template, not a Phase 7 trigger.) **Not fired.**

---

## Verdict

**No trigger fired. Phase 7 is deferred, not abandoned.** The §23 tradeoff stands as
written: SQLite + poller + effects ledger, no durable engine. Re-run this gate when any of
these is true, or at the next phase boundary — whichever comes first:

1. A run ends in `failed` with **no** human retry — a ticket actually lost.
2. The poller reports ≥ 3 tickets queued > 4 h behind a busy machine, twice.
3. A re-measured passthrough-I/O ratio exceeds 3× and does not improve.

Until then, Phase 7 is a paragraph, not code.

---

## Proposed plan insertion for James

`SOFTWARE-FACTORY-PLAN.md` is protected, so this is for James to paste. Insert immediately
after the line *"You do not need this on day one. You need it when a lost sandbox costs you
a ticket."* (currently line ~2539) and before the `---` that closes §19:

```markdown
**Evidence gate — 2026-08-24 (first check, at Phase 6 close).** All three triggers audited
against the live `state/factory.db`, the `logs/daemon.out.log` transcript, and the Phase 0–5
discovery record. **None fired.** Phase 7 stays a paragraph; no code is written.

| Trigger | Threshold | Observed | Fired? |
| --- | --- | --- | --- |
| A lost sandbox costs a ticket | ≥ 2 occurrences in 30 d that §16 recovery did **not** save | **0 tickets lost.** Two `orphaned` attempts — both FRO-11 (`implementing` att 1, `planning` att 3) — were saved by §16 resume-by-session (`resumable:resume(session-intact)`); FRO-11 reached `completed`. The three `failed` transitions on record (FRO-7 `max-reruns-reviewing`; FRO-11 `ladder-exhausted` ×2) are attempt-budget exhaustion, not sandbox losses, and all were recovered by the human-authorised `failed → resumable` retry (§5.3). No run is in state `failed` today. | No |
| Concurrency exceeds the laptop | ≥ 3 tickets queued > 4 h, twice | Serial execution (one writer per project, `concurrency.per_project = 1`, 60 s poll). The 11 `blocked` and 34 `cancelled` rows are re-appearing intake rejects (`state-not-todo` on `Done` tickets still labelled `ready-for-agent`) and cancelled reruns, not a queue backlog. No 4-h starvation event. | No |
| Wall-clock dominated by passthrough I/O | P0-10's ratio > 3× and not improving | P0-10 measured the sandbox **~2× faster** than the host on every Python gate — *"the virtiofs passthrough is not the bottleneck at this repository size; macOS process spawn is."* The ratio is ~2× in the favourable direction, not > 3×, and the whole gate suite is under three seconds either way. | No |

**Verdict.** The recovery machinery that Phases 4 and 4.5 built has absorbed every sandbox
loss the real runs have produced: two orphans, both resumed by session id, neither costing a
ticket. That is the single highest-value trigger (R4 / the §23 tradeoff that named this
phase), and it has not fired. The other two are capacity questions for a load the laptop has
not seen. **Phase 7 is deferred, not abandoned**: re-run this gate when a run ends in
`failed` with no human retry, when the poller reports tickets queued > 4 h behind a busy
machine, or at the next phase boundary — whichever comes first. Until then the §23 tradeoff
stands as written: SQLite + poller + effects ledger, no durable engine.
```

After pasting, regenerate the generated copy so `scripts/sync_plan_copy.py --check` stays
green:

```sh
python3 scripts/sync_plan_copy.py
```
```

---

## Method

Nothing here was found by reading the code first. The trigger counts come from SQL against
the live DB (`transitions`, `attempts`, `runs`), the recovery disposition from the daemon
log, and the I/O ratio from the dated P0-10 record. The plan file was not edited by the
agent — the `protect_paths.mjs` hook refused it, which is the guardrail working.