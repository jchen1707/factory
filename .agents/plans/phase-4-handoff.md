# Phase 4 handoff — begun 2026-08-22

Phase 3 is closed and **Phase 4's core path is done** on branch
`feat/phase-4-tick-and-recovery` (17 commits, pushed, open as
[factory#19](https://github.com/jchen1707/factory/pull/19) against `main`).
`SOFTWARE-FACTORY-PLAN.md` §19 "Phase 4 — polling, idempotency, recovery, resume,
cleanup" (line 2188) is the specification; this file records only what a new session
cannot read out of the plan or the code.

Read `.agents/plans/phase-3-handoff.md` for the Phase 2/3 archaeology.

**Start here:** "Where Phase 4 stands" below, then "Next session" (the only thing left
is the §18.5 console, plus James's decision to load the daemon under the timer).

## Where Phase 4 stands

| §19 item | State |
| --- | --- |
| `cli.py: tick` | **built** — one pass: reap, recover, advance, claim, in that order |
| `steps/reap.py` | **built** — §16.1's orphan detection, read entirely off disk |
| `recovery.py` | **built** — §16.3 resume-vs-restart and §16.3a's ladder, `decide` is pure |
| `gc.py` + `cli.py: gc` | **built** — §16.5, `--dry-run` is the same code path |
| `steps/plan.py` rewind | **built** — rung 3 enters `planning`; `plan` records an attempt row now |
| `cli.py: suspend` / `resume` | **built + validated end-to-end** — `recovery.suspend`/`recovery.resume` + thin CLI shells; drove FRO-6 `blocked → reviewing → pr_ready → awaiting_human` (PR #41). Two bugs found+fixed: the verify-fail loop-back `illegal-transition` (defect 4) and the forced-Tier-2 PR-body misreporting (defect 5). Committed `5a3e400`, `822c11c`. |
| `cli.py: daemon` + `ops/com.jchen.factory.plist` | **built** — `cmd_daemon` loops a factored `_tick_pass`; the plist runs `factory tick --once` every 60 s via an absolute `/opt/homebrew/bin/uv` path (launchd has no PATH). Not loaded under the timer until James says so (§19). |
| `src/factory/console/` | **built** — PR #21. §18.5's five views: CLI forms (`status`, `status --evidence`, `logs --follow`, `runtimes`, `config models`) and `factory serve` (FastAPI, loopback-only, SSE). No Merge button; controls route through `policy` + `actor="human"`. |
| `docs/runbook.md` | **built** — `docs/runbook.md`, written from the real defects (env-gate-failed, illegal-transition, lighthouse/`--all`, disk floor, Linear outage, bad PR, stop-now). |

Plus three things §19 does not list, all forced by real runs: `factory run --full-review`
(§15.2 override), intake **condition 11** (blocking relations), and the poller evaluating
intake at all — see "Defects this session".

**The property the whole phase turns on** is §4.2's, and it is now real in code: *the tick
that asks is never the tick that started the run.* The agent steps are two-phase —
`implement.start`/`implement.collect`, `plan.start`/`plan.collect`,
`verify.start`/`verify.collect`, `review.start`/`review.collect` — and `collect` reads
nothing that `start` held in memory. The vault snapshot moved out of a local variable into
`vault-before.json` in the attempt directory for exactly that reason, and its absence
blocks rather than defaulting to an empty `before`. `factory run` still calls
start + wait + collect; only the tick uses the halves.

**One gap in the tick, known and deliberate.** `deliver` is still synchronous inside a pass
(host-side push + PR, seconds; `exec_detached` is sandbox-only, so splitting it would need a
new host-detach mechanism for no gain). `verify` and `review` are two-phase now, so a tick
no longer blocks on the gate suite or the review fan-out — only the final push+PR is
in-band, and `launchd` does not overlap `StartInterval` instances anyway. The daemon is safe
to load under the timer once James decides to (§19); the env-gate fix and the F23 ladder
bound the two loops that would otherwise burn the attempt budget unattended.

## Build `resume` next

> **Status (2026-08-22): built.** `recovery.suspend`/`recovery.resume` drive the logic;
> `cmd_suspend`/`cmd_resume` are thin shells that own the lease and the real adapters.
> The three notes below were each honoured, and a fourth the spec does not name was found
> while reading the code: `implement.start`/`plan.start` call `advance` with the default
> automatic actor, but `SUSPENDED → implementing` is `resume-is-james` and `BLOCKED →
> implementing` is `unblock-is-a-judgement`, so both `start` functions take an `actor` param
> and `resume` passes `"human"` or the advance refuses. `resume` re-enters
> `verifying`/`reviewing` **without incrementing** (note 2 — `verify`/`deliver` address
> `factory_dir / "run" / str(attempt)`), then `cmd_resume` drives forward via the tick's
> `_drive_from_here`, so the FRO-6 path runs verify→review→deliver in one command; an agent
> target starts the agent and hands the rest to the tick. `state_that_died` is generalised
> to `state_before(ctx, target)`. All four cases (suspend, resume-into-verify, blocked-at-
> verify, session-by-id, `--from planning`, `--authorise`) are tested in
> `tests/integration/test_phase4.py` and each key test was mutation-checked to fail without
> its fix. **Run end-to-end against FRO-6 on 2026-08-22** — but via `--from reviewing`,
> not `--from verifying`, because lighthouse cannot pass (see "CLOSED — Tier 2 executed"
> below) and the verify-fail loop-back crashed (defect 4). It is the run that closes Tier 2.

§16.3b is the spec. Three notes it does not contain, and each will cost an hour to
rediscover.

**1. The state machine has no edge for the case you actually have.** `machine.py`'s
`_WORKFLOW` gives `BLOCKED` exactly two exits — `IMPLEMENTING` and `PLANNING`, both
reserved by `unblock-is-a-judgement`. There is no `BLOCKED -> VERIFYING` and no
`BLOCKED -> REVIEWING`. But the live subject (FRO-6, below) is blocked *at* `verifying`
with a complete implementation and a passing gate report: the cheap repair is to re-enter
the state that blocked, not to spend another 10 M-token implement. Add both edges to
`_WORKFLOW` and both pairs to `HUMAN_ONLY` under the existing rule name. Note that
`assert_table_is_sound` and the §21.1 table-driven test will both need to still pass.

**2. The attempt counter must not increment on a non-agent resume.** §16.3b says "either
way the attempt counter increments, so a suspended-and-resumed run cannot escape the
budget by cycling", and that is right for `implementing` and `planning` — they start a
fresh agent turn. It is wrong for `verifying` and `reviewing`: no model call happens on
re-entry into verify, so incrementing would spend budget for nothing **and** break the
lookup, because `verify.py` and `deliver.py` both address `factory_dir / "run" / str(attempt)`
and would look in a directory that does not exist. Increment when the resume starts an
agent, not otherwise.

**3. Which state to resume into is already recorded, so do not parse it.**
`record_transition(from_state=..., to_state=SUSPENDED)` stores the origin in the
`transitions` row. `recovery.state_that_died` is the same lookup for `RESUMABLE`;
generalise it to `state_before(ctx, target)` rather than writing a second one — this file
records one defect today that was two functions disagreeing about the same input.

The rest is mechanical and belongs in `recovery.py` beside `resume_run`, with thin
commands in `cli.py`:

- `factory suspend <TICKET> [--reason]` — `kill_agent`, **wait for the `exit` file** so the
  attempt ends with a real terminal record rather than a truncated one (the same thing
  `reap` does on a state timeout), `sbx stop` only when no other run is using the sandbox,
  keep the worktree/branch/attempt directory/session id, then `advance(..., SUSPENDED,
  actor="human")` and one Linear comment through the effects ledger.
- `factory resume <TICKET> [--from <state>] [--authorise]` — `--authorise` is §16.4's
  `FAILED -> RESUMABLE` re-authorisation and is already `HUMAN_ONLY: reauthorise-spend`.
  Everything else routes through the existing `recovery.resume_run` machinery.

**Do it before re-running anything.** FRO-6's blocked run holds a finished implementation
that cost 10 582 685 input tokens; resuming it into `verifying` re-runs the gate report
(no model call) and then the review, which is the only part still unproven. Re-running from
scratch pays for that implement a second time, and BAC-4's four re-runs at 7-16 M each are
what exhausted the Codex credit once already.

One practical detail: the build sandbox is `stopped`, and the branch lives inside its
clone. `sbx stop` does not destroy it — only `sbx rm` does — but a resume that reaches
`reviewing` will need `clone.fetch_back`, which needs the sandbox running and the git
daemon port re-read. `steps/clone.py:_fetch_with_the_sandbox_running` already knows that
dance; do not reimplement it.

## What remains in Phase 4

`resume` and Tier 2 are **closed** (commits `5a3e400`, `822c11c`, pushed to
`origin/feat/phase-4-tick-and-recovery`). In the order I would do them:

1. **Make `verify`/`review`/`deliver` two-phase.** — **DONE.** `verify` and `review`
   are now `start`/`collect` (detached, reaped), mirroring `implement`; `deliver` stays
   synchronous (host-side push+PR, seconds; `exec_detached` is sandbox-only, so splitting
   it would need a new host-detach mechanism for no gain). `reap.DETACHED_STATES` gained
   `verifying`/`reviewing` plus a `START_NEEDED` outcome (no attempt row → the tick calls
   the state's `start`, the way `NEXT_STEP` calls `implement.start`); the `NEXT_STEP`
   guard was tightened to `state is IMPLEMENTING and entered_from is VERIFYING` so a
   finished review attempt isn't misclassified. Orphaned and timed-out verify/review go
   `resumable` (uniform with implement); `recovery.resume_run` re-runs them via
   `verify.start`/`review.start` directly, bounded by a new `store.resumable_reentries`
   ceiling (the `attempts_in_state` ceiling is decorative for verify/review —
   `start_attempt` is `INSERT OR REPLACE` on a fixed attempt number, so re-runs don't
   grow it). `review.start` runs `fetch_back`+`redphase`+`weakening` synchronously (the
   replay needs the build sandbox; the codex axes must stay in the read-only review
   sandbox, so they can't share a detached script) then spawns one detached fan-out;
   one behaviour-change ticket blocks the tick for one test-run's duration before the
   fan-out detaches. One trigger rule narrowed: `_decide_tier2`'s `tier1_has_human` rule
   can't fire from `start` (Tier-1 hasn't run yet), so a small-diff Tier-1 critical/high
   no longer triggers the extra fan-out — it still routes to `awaiting_human` via the
   transition, and `--full-review` covers it on demand. The rule stays in `_decide_tier2`
   for its table-driven test. New tests in `test_phase4.py` (each mutation-checked): the
   core two-phase property for verify, `START_NEEDED`, the orphan→resumable→rerun, and
   the reentry ceiling→`failed`. All integration+unit+mypy green. Plan:
   `.agents/plans/drifting-sprouting-lantern.md`. Next: item 2 (the `--all`/lighthouse
   hazard) is now the blocker for the daemon — a daemon resuming such a ticket into
   `verifying` will still loop back to a fresh implement on a lighthouse fail.
2. **Fix the `--all`/lighthouse daemon hazard before loading the timer.** — **DONE.**
   `gate_report.mjs` runs *every* opt-in gate under `--all` and does not re-evaluate
   `when`, so a frontend ticket whose agent claims any e2e/integration gate also forces
   lighthouse, which fails on missing Chrome and can't pass even with it (its caveat:
   performance scores null). The chosen fix is factory-only: in `verify.collect`, when the
   verdict is `fail` and *every* failing gate carries a non-null `caveat` (the config
   author's flag that the gate is environmental — lighthouse, a browser gate without the
   browser), the run blocks with `env-gate-failed` instead of looping back to
   `implementing`. A real code gate (`ruff`/`pytest`/`mypy`, `caveat: null`) still loops
   back. `blocked` is a human state: install the tool, or `factory resume <TICKET> --from
   reviewing` to bypass and report the failure honestly in the PR — the FRO-6 resolution,
   now the documented unblock rather than a one-off. Tested + mutation-checked in
   `test_phase4.py`. (See [[opt-in-gates-all-is-all-or-nothing]] in memory.)
3. **`daemon` + `ops/com.jchen.factory.plist`** — **built.** `cmd_daemon` loops a factored
   `_tick_pass` (the same body `cmd_tick` uses), reloading `projects.toml`/`models.toml`
   hot each pass; one bad tick is logged and skipped, never fatal. The plist runs
   `factory tick --once` with `StartInterval 60` / `RunAtLoad`, stdout+stderr to
   `~/factory/logs/daemon.{out,err}.log`, via `/opt/homebrew/bin/uv run --project
   /Users/james/factory factory tick --once` (launchd has no PATH). §19's rollback is
   `launchctl bootout`; the daemon is stateless between ticks so stopping it is safe at any
   instant. **Not loaded under the timer until James says so.**
4. **`docs/runbook.md`** — **built.** Written from the real defects: `env-gate-failed`
   (install the tool or `--from reviewing`), `illegal-transition` (fix the code, don't
   re-run), the lighthouse/`--all` block, disk floor, Linear outage, a bad PR, and
   stop-now (`suspend`/`cancel`/`launchctl bootout`).
5. **The console** (§18.5, five views, loopback only) — **deferred** by the 2026-08-22
   scope decision. It is the largest item and the least load-bearing: every view has a CLI
   form and §18.5 says the CLI is built first. It also adds FastAPI/uvicorn deps to the
   deliberately stdlib-only control plane that holds the keychain credential, which
   `pyproject.toml` flags as a decision to review — so it is its own PR, not folded into
   this one. The context percentage needs `~/.codex/models_cache.json` for the denominator
   (P0-7 proved the event stream carries neither the window nor a percentage); the
   `routing.ModelFacts.usable_context` property already exists for it.
6. **The §22 rows Phase 4 owns.** Covered now: F3, F4, F12, F13, F24, F22, and this slice
   adds **F1/F2** (the `FACTORY_CRASH_AT` env hook in `record_effect` — a real crash
   between the `intended` INSERT and the write, and between the write and `confirmed`; the
   ledger reconciles rather than retrying, mutation-checked), **F14** (disk floor — a run
   below `disk_min_free_gb` blocks with `disk-below-floor` on the next `advance`,
   mutation-checked), **F17** (Linear unreachable — the tick catches the adapter error and
   leaves the run in place, no state advance), **F23** (a third consecutive gate failure
   climbs the ladder to `planning` — wired into `verify.collect` where the
   `verifying -> planning` edge lives; rung 4 parks at `resumable`; both mutation-checked),
   **F25** (a bad `models.toml` refuses the tick and names the rule, no fallback). **F26**
   (a console control aimed at a `codex-*` sandbox) is **deferred with the console**: the
   namespace assertion it rests on (`policy.assert_factory_sandbox`) is already covered by
   `tests/unit/test_sbx.py`, so the safety property holds; the console-control variant
   belongs in the console PR.

## Next session

**Phase 4's build is done; what remains is James's.** Both open questions are resolved
(PR #20, merged) and the console is built (PR #21).

Two things the first console commit claimed prematurely, both since fixed and worth
recording because the same shortcut is easy to repeat:

- **§19 names `console/templates/*.html` as files.** The first cut inlined every page in
  `app.py`. Now extracted — `page.html`, `board.html`, `run_detail.html`, `runtimes.html`,
  `config.html`, `controls.html`, `console.css` — as `string.Template` files, and they ship
  in the wheel.
- **"Tests: the whole of §22" means all thirty rows.** An audit found **F27, F28 and F30**
  (the test-honesty rows) with no test at any level: `test_redphase.py` covered `_classify`,
  which is a different claim from "`replay` raises". `tests/integration/test_redphase_gates.py`
  adds seven, including §23's requirement that the two blocking cases be unreachable from
  any config file. Nine further rows had the behaviour covered but no `F<n>` label, so §22
  could not be audited at all; they are labelled now, and **all thirty are traceable**.
  F5 also gained a real assertion — it wrote 60 stderr lines and never checked the 40-line
  bound, and an unbounded tail in a Linear comment is how a secret reaches the tracker.

Writing §19's "resume-from-planning works from the browser" test then found a third:
`dispatch_control` hardcoded the real `sbx`/`codex` adapters, so no console control could
be tested without a Docker login (§21.3 says the whole machine runs against fakes). It
takes `tick_once`'s `context_factory` seam now.

1. ~~**The §18.5 console**~~ — **built, PR #21.** CLI forms first as §18.5 requires, then
   `factory serve`: FastAPI, loopback-only (a non-loopback `--host` is refused), five views,
   SSE for the board and the event tail. The context percentage is
   `turn.completed.usage.input_tokens / ModelFacts.usable_context` — P0-7 refuted the
   plan's reading of the stream, so the denominator comes from the model cache — and it is
   **hidden with the reason named** whenever either half is missing, never estimated. F26
   lands via `policy.assert_factory_sandbox` on every control. There is no Merge button.
   Config edits validate through `load_routing` *before* the write, so a
   reviewer-on-the-builder's-model is refused in the form and `models.toml` is never
   half-written. 27 tests; the three load-bearing ones mutation-checked.

   Three defects found while building it, each fixed against measured evidence:
   `sbx ls --json` is an **object** with a `sandboxes` array and `{host_port,
   sandbox_port}` port objects (a list-shaped reader shows an empty runtimes view on a
   healthy machine); SQLite connections are thread-bound and the SSE readers run on a
   worker thread; Starlette's form parser would have cost a fifth dependency to read
   `a=1&b=2`.

2. **Load the daemon under the timer — James's decision (§19). The only thing left.**
   The daemon and the plist are built; the env-gate fix, the F23 ladder and the clone-branch
   fix (below) bound the loops that would otherwise burn the attempt budget unattended, and
   the §22 crash/idempotency tests pass. When James is ready:
   `launchctl bootstrap gui/$(id -u) ops/com.jchen.factory.plist`. Rollback is
   `launchctl bootout`; the daemon is stateless between ticks, so stopping it is safe at
   any instant.

3. ~~**Two open questions**~~ — **both resolved, PR #20.**
   - **Does `verify` re-run the gates on a `--from verifying` resume?** **Yes, and that is
     the decision.** A resume only advances into `verifying`; the tick's `START_NEEDED`
     branch calls `verify.start` again, and `collect` reads a freshly written
     `gates.stdout.txt` only after the new `exit` lands. A stale `gates.json` is never
     read. Re-running is what surfaced FRO-6's hidden lighthouse failure, and
     `env-gate-failed` makes it safe by blocking instead of looping. Documented in
     `verify.collect`; pinned by a test that fails if `collect` is pointed at the stale file.
   - **The clone worktree on a `--from verifying` resume.** **Fixed.** A `--clone` build
     sandbox is shared across a project's runs, so a second run leaves the clone on *its*
     branch and a later resume would run the gates against the wrong ticket's code.
     `verify.start` now calls `clone.ensure_on_branch` for clone runs — idempotent on the
     forward path, and it blocks `clone-branch-missing` rather than cutting a fresh empty
     branch if the agent's commits are gone. Two tests, mutation-checked. This was the
     "worth a test before loading the daemon" hazard, and it is closed.

**Still open, and deliberately not Phase 4's:** the two carry-over defects under "Two
defects worth knowing about" — `_SENSITIVE_DIRS["frontend"]` matching nothing, and
`deliver._archive` returning silently on a missing attempt directory. Both are recorded
defects rather than blockers.

**The board, for context:** FRO-6 is `awaiting_human` at draft
[frontend-harness#41](https://github.com/jchen1707/frontend-harness/pull/41) (the run that
closed Tier 2); a human merging #41 should clear its `needs-info` label. FRO-7 is held at
intake by condition 11 (blocked by FRO-6). No BAC work pending. See "State of the board"
below for the full list.

## Where Phase 3 ended

**Both stacks reach a draft PR through the whole pipeline.**

| | evidence |
| --- | --- |
| python, bind-mounted | `factory run BAC-4` → [python-harness#66](https://github.com/jchen1707/python-harness/pull/66), merged |
| frontend, `--clone` | `factory run FRO-10` → [frontend-harness#39](https://github.com/jchen1707/frontend-harness/pull/39), merged |

FRO-10 drove `approved → awaiting_human` in **6 m 15 s**:

```
23:42:47  approved -> claimed                 23:47:52  implementing -> verifying
23:42:47  claimed -> context_loaded           23:48:00  verifying -> reviewing
23:42:47  context_loaded -> sandbox_creating  23:48:58  reviewing -> pr_ready
23:42:50  sandbox_creating -> sandbox_ready   23:49:02  pr_ready -> awaiting_human
23:42:51  sandbox_ready -> worktree_ready
23:42:51  worktree_ready -> implementing
```

Every preflight check passed, the effects ledger shows exactly one Linear write per state
all confirmed, and Linear's GitHub integration moved FRO-10 to Done on merge — so the
`Fixes <TEAM-NUM>` line in the body is doing its job and the factory does not need to.

**The red-phase replay is real, not theatre.** FRO-10's replay ran vitest inside the
clone's scratch worktree and returned a genuine red: `× rejects an invalid health response
→ expected ZodError … to be an instance of ValidationError`. That is §15.3's property
holding on a live run.

## The one thing Phase 3 did not prove

**Tier 2 has never executed to completion on any run.** Not once, across every run of this
project. It has fired its trigger correctly in both directions and it has been skipped
correctly, but the fan-out itself is unproven code. Do not describe the review as fully
validated, and do not be reassured by a PR body that says `Tier 2 skipped — rule:
no-trigger`: that is the trigger working, not Tier 2 working.

FRO-10 skipped it correctly — 2 files, 36 lines, no protected path, no sensitive dir, no
Tier-1 finding, not a Bug. Nothing about that run was wrong.

**How to actually prove it.** The six rules are in `review._decide_tier2`. Only two are
reachable in practice:

| rule | reachable? |
| --- | --- |
| ≥ 10 files / ≥ 400 lines | **yes** — needs a genuinely large ticket |
| protected path | no — `protect_paths.mjs` refuses the agent by design, so the diff can never contain one |
| sensitive dir | no — frontend's is `src/**/routes/**` and this repo has no routes directory; see the config bug below |
| Tier-1 critical/high | not controllable on demand |
| Bug label without test | only for a Bug whose fix honestly adds no test **and** reports `behaviour_changed: false` — otherwise redphase blocks first with `behaviour-change-without-test` |

So it needs a large diff. **FRO-5 has since landed** ([frontend-harness#40](https://github.com/jchen1707/frontend-harness/pull/40), merged 2026-08-22), which changes the picture:

- **FRO-7** (`Filter your Projects by status, including archived`) is `Todo`, passes intake
  today, and needs no fix to run. It is the obvious next candidate.
- **FRO-6** (`Search your Projects by name`) is unblocked too, but is parked at
  `In Progress` with a `needs-info` label from the run that blocked on FRO-5. **A human has
  to clear that label** — holding a blocked ticket at intake until someone does is the
  designed behaviour, not a bug.

Neither is guaranteed to fire Tier 2 on its own: they are single-slice tickets and may land
under both the 10-file and 400-line thresholds.

**Do not go hunting for a big enough ticket.** `factory run <TICKET> --full-review` now
exists and forces the fan-out for one run, so the fan-out no longer depends on getting
lucky with a diff size. It is checked first in `_decide_tier2` because it is an override
rather than a seventh rule, it lives on the **run row** rather than on the `Context` —
under `tick` the review happens in a later process than the one that took the flag — and
`review-summary.json` records `ran:forced`, which the PR body reports as "forced with
`--full-review`; no trigger rule fired". A forced fan-out reported as a triggered one
would misstate what the rules concluded about the diff.

**`factory run FRO-7 --full-review` was run on 2026-08-22 and did not get there.** It
passed all ten intake conditions, reached `implementing`, and the agent stopped itself:

> FRO-7 is explicitly blocked by FRO-6 and must reuse its URL-parameter and
> result-announcement seams. Those seams do not exist on origin/v2 or any fetched FRO-6
> ref, so implementing them here would violate the ticket boundary.

That is the agent behaving correctly — it read the slice boundary and refused to invent a
sibling's seams rather than quietly widening its scope. Cost 351 540 in / 5 182 out, and
the factory wrote `needs-info` to FRO-7, so **FRO-6 and FRO-7 are now both held at intake
and both need a human to clear the label.**

The dependency chain is FRO-5 → FRO-6 → FRO-7, and FRO-5 landing did not unblock FRO-7.
So the run that proves Tier 2 is **`factory run FRO-6 --full-review`**, once someone
removes FRO-6's `needs-info`. FRO-6 is a single-slice ticket and will almost certainly not
trigger Tier 2 on its own, which is exactly what the override is for.

**Tier 2 has still never executed to completion.** Three runs on 2026-08-22 tried and none
reached `reviewing`:

| run | outcome | what it proved |
| --- | --- | --- |
| `factory run FRO-7 --full-review` | `agent-blocked` — FRO-7 needs FRO-6's seams | the two-phase implement step works against the real wrapper; `vault-before.json` lands on the clone mount. Argued condition 11 into existence |
| `factory run FRO-6 --full-review` #1 | invalid — read the previous run's evidence | defect 1 |
| `factory run FRO-6 --full-review` #2 | `evidence-mismatch` at `verifying`, gate verdict `pass` | defect 3, and a finished implementation now waiting on `resume` |

This section stays open, and the next thing that closes it is `resume`, not another run.

### CLOSED 2026-08-22 — Tier 2 executed to completion

`factory resume FRO-6 --from reviewing` (after resetting the crash artifacts the first
resume left behind — see the two defects below) drove `blocked -> reviewing -> pr_ready ->
awaiting_human [delivered]` and opened draft [frontend-harness#41](https://github.com/jchen1707/frontend-harness/pull/41).
`review-summary.json` records `tier2: "ran:forced"` with four real, `medium` findings:
one from the Tier-1 Standards axis (`review-standards.json`, the `q` URL param reaching
state without Zod) and three from the Tier-2 `review-full.json` axis (search-test coverage
of case-insensitive matching, focus loss on Clear, and `useDeferredValue` not deferring
because the select is recreated each render). **This is the first time Tier 2 has run
against real adapters.** No critical/high finding, so review advanced to `pr_ready`.

Two things made the resume take the `--from reviewing` path rather than the `verify ->
review` path the spec implies:

1. **Lighthouse cannot pass for this run, and `--all` forces it to run.** The agent
   claimed `playwright` (opt-in, in scope) and `lighthouse` (opt-in, out of scope for a
   search-by-name feature). `gate_report.mjs`'s `--all` is all-or-nothing — it runs *every*
   opt-in gate and does not re-evaluate `when` — so lighthouse runs whenever any opt-in
   gate is claimed. Lighthouse then fails on `Chrome installation not found`, and its own
   caveat says performance scores null even with Chrome ("a category with no score is not a
   pass"), so the verdict is `fail` for a reason no amount of implementing fixes. Verify
   looping back to `implementing` on that would spend another ~10 M tokens to re-fix an
   environment gate. The honest unblock is `--from reviewing`: the PR body still reports
   `lighthouse = fail` (and `Verdict: fail`) for a human to see; it just does not let an
   out-of-scope, unreliable gate block the unproven review code.
2. **`--from verifying` crashed on the verify-fail loop-back** (defect 4 below), so even
   the `verify -> review` path was not open until that was fixed.

The state DB was reset before the resume: the first resume had incremented the attempt to
2 and written an incomplete `run/2` before crashing, so the run was restored to its
pre-resume state (`blocked`, attempt 1, the three crash transitions dropped) with a
backup at `state/factory.db.bak-pre-reviewing-resume`. `--from reviewing` re-enters at
attempt 1, so review reads `run/1`'s evidence (the real gate report + claim).

### What the FRO-7 run did prove

Two things Phase 4's refactor had only ever run against fakes, both confirmed on a real
`--clone` project:

- **The two-phase implement step works across the real wrapper.** `start` spawned the
  detached run and `collect` read it back — `check implement_result_schema: pass` and a
  schema-valid `blocked` result. The `FakeSandbox` writes `exit` synchronously and the
  real wrapper does not, so this was the live regression risk in splitting the step.
- **`vault-before.json` lands on the clone mount**, not in the worktree —
  `state/clone/frontend-harness/FRO-7/.factory/run/1/`, 17 KB, written at `start` and read
  at `collect`. `check vault_snapshot: pass`. That is the path PR #16 had to fix once
  already.

### An intake condition this run argues for

FRO-7's description carries a `## Blocked by` section naming FRO-6 in prose. Intake reads
that description already and does not look at the section, so it spent a model run to
learn something a string match would have answered in zero seconds. A condition 11 —
*every ticket named under "Blocked by" is Done* — is cheap, and its failure mode is the
good one: a false block is a ticket a human re-reads, where the current behaviour is a
paid run that ends in `needs-info`. Worth building before the daemon is loaded, because
under a timer this run would have happened unattended.

## Two defects worth knowing about, not yet fixed

- **`_SENSITIVE_DIRS["frontend"] = ("src/**/routes/**",)` matches nothing.** The repo puts
  routing in `App.tsx` and features under `src/features/**`. That Tier-2 trigger is dead
  config rather than a live rule, and the python entry (`src/app/ai/**`) should be checked
  the same way before anyone relies on it.
- **`deliver._archive` returns silently when the attempt directory is missing.** Combined
  with `_read_json` returning `{}`, a run whose evidence cannot be found produces a PR body
  with an empty gate table and a printed artifact path that does not exist — and says
  nothing. The *cause* is fixed (below), but the silent failure mode is still there and
  will hide the next one.

## Carry-over footguns

Everything in `.agents/plans/phase-3-handoff.md` still applies — run in the background,
tag before cancelling, there is no resume, probe before you spend, fakes should model the
constraint that broke. Four to add:

1. **`git checkout -- <file>` to undo a mutation test destroys uncommitted work.** It cost
   me six files of unstaged edits in one session, twice. Commit first, then mutate, then
   `git checkout` is safe because it restores the commit.
2. **A commit written after a PR is merged does not reach `main`.** `2dc1e7b` was pushed to
   an already-merged branch and silently stayed there; `main` shipped the bug it fixed
   until PR #16. After merging, `git log --oneline main..<branch>` before deleting anything.
3. **`uv run mypy 2>&1 | tail -2 && git commit`** commits even when mypy fails — the pipe
   makes the exit status `tail`'s. Gate on the bare command.
4. **`sbx` owns the clone's git remote and does not maintain it.** See
   `steps/clone.py:_fetch_with_the_sandbox_running`; the remote is registered at `create`,
   withdrawn on stop, never restored, and the daemon port is reassigned on every start.
   `sbx ls --json` is the only durable source. This is the kind of thing that looks like a
   factory bug for an hour before it turns out to be a platform lifecycle.
5. **Footgun 1 is not theoretical and I walked into it anyway.** Having read it that same
   session, I ran `git checkout -- src/factory/repo.py` to undo a mutation and destroyed
   two uncommitted fixes with it. Commit *first*, then mutate, then `git checkout` is
   safe because it restores the commit. Budget the extra commit; it is cheaper than
   re-deriving the fix.
6. **An identical token count between two runs is not a coincidence.** It is the tell that
   the second run read the first one's evidence. Any two numbers matching to the digit
   across runs — tokens, exit codes, durations — should be treated as a reused artifact
   until proved otherwise.
7. **`ls` and other bare commands can return empty under the harness's hooks.** Twice a
   `ls` produced no output where files plainly existed; `ls -a <absolute path>` worked.
   Do not conclude a directory is empty from one silent listing.

## Defects this session found, in order

All three came out of two real runs, and all three are the same shape: **the factory
producing a confident answer that was not about the run in front of it**, or punishing the
agent for being more thorough than the minimum. Each fix has a regression test that was
mutated to prove it fails without the fix.

1. **A second run of one ticket read the first run's verdict.** `factory run FRO-6` recorded
   `implementing -> blocked` in the same second it spawned its agent, reporting a
   four-hour-old block reason and the previous run's token counts *to the digit*, while its
   own agent kept working in the sandbox for another ninety seconds. `Context.factory_dir`
   keyed the clone evidence tree by **ticket**; a bind-mounted worktree is deleted by
   `cancel` and gets a fresh tree free, but the clone mount survives every run, so the
   second run landed on the first one's `run/1`, found its `exit` file, and
   `implement._await_exit` returned instantly — it returns the moment that file appears and
   cannot tell stale from fresh. `sbx.poll` trusts it identically, so **`reap` would have
   done the same thing unattended under the daemon.** Fixed at two layers: `factory_dir`
   carries the run id, and `AttemptDir.create` clears the terminal markers of any previous
   invocation (bare names only — `plan-exit` and siblings belong to a rewind's planning
   half).
2. **`factory cancel` tried to remove the repository.** For a `--clone` run `runs.worktree`
   is the project path — correctly, the branch is cut inside the VM — and `git worktree
   list` includes the main working tree, so `worktree_exists` said yes and the rollback
   ran `git worktree remove --force /Users/james/frontend-harness`. Git declined and **the
   whole cancel aborted with it**, leaving the tracker un-restored. `_worktree_paths` no
   longer offers it and `repo.remove_worktree` refuses the repository by name.
3. **The opt-in gate lookup read a claim differently from the mismatch check.** FRO-6
   blocked on `evidence-mismatch` naming `playwright, lighthouse` — gates the agent had
   genuinely run and passed. `gates_run` entries are free-form (`"playwright — pnpm
   test:e2e passed (4 tests)"`); `_evidence_mismatch` knew that and matched by longest-name
   containment, while `_claims_opt_in` ten lines above looked the claim up in a dict **by
   equality**. So `--all` was never passed, and layer A's contract is that without it the
   e2e/integration gates are `not_applicable` — *"opt-in is not optional: the caller
   asserts a gate's `when` clause by passing this"*. The report could not corroborate a
   claim the factory had declined to have checked, and the mismatch rule called that
   disagreement. **The repair tightens rather than relaxes**: one matcher, `_gate_named_in`,
   used by both, so with `--all` the report actually runs those gates and the claim is
   verified instead of unfalsifiable. `skipped_unchanged` still blocks and a test pins it.
4. **The verify-fail loop-back crashed `implement.start` on `illegal-transition`.** When a
   gate verdict is `fail`, `verify` advances `verifying -> implementing` and the tick's
   `_drive_from_here` then calls `implement.start` to begin the next attempt. `start`
   unconditionally called `advance(ctx, IMPLEMENTING)`, so already in `implementing` it
   recorded `implementing -> implementing`, which is not in the table, and the run blocked.
   This is a tick bug, not a resume bug — any gate failure would crash the daemon here. FRO-6
   hit it because lighthouse (defect-free code, but an env gate) failed the verdict. Fixed:
   `start` advances into `implementing` only when it is not already there (the hop was
   recorded by whoever put the run there — `verify` on a loop-back, `start` otherwise). Test
   mutated to fail without the fix.
5. **The PR body reported a forced Tier 2 as "skipped", and labelled Tier-2 findings
   "Tier-1".** `delivery/github.render_pr_body` handled `tier2 == "ran"` and a fallback
   "skipped" but had no branch for `ran:forced`, so the first completed Tier 2 (FRO-6) said
   "Tier 2 skipped — rule: `ran:forced`" and listed the fan-out's three findings under
   "Tier-1 findings". This is exactly the misstatement §13.2 warns against, and it survived
   because the forced path was never exercised — Tier 2 had never completed. `deliver`
   its own `_review_summary` (the awaiting-human comment) already handled `ran:forced`
   correctly; the two disagreed. Fixed: one `ran:forced` branch in `render_pr_body`
   ("Tier 2 ran (forced with `--full-review`; no trigger rule fired)") and a `Findings` /
   `Tier-1 findings` label that follows whether Tier 2 ran. PR #41's body was regenerated
   and re-posted. Test mutated to fail without the fix.

Two more worth recording that were not code defects:

- **The poller evaluated no intake condition at all.** `claim_step` performs the claim and
  judges nothing; the ten conditions lived in `cmd_run`. An unattended tick would have
  started a run on any ticket carrying `ready-for-agent` and discovered the rest by spending
  a model run. `assess` is the one evaluator now.
- **A regression test that could not fail.** The first clone re-run test used a *different
  ticket* for the second run, so the paths differed whatever `factory_dir` did — reverting
  the fix left it green. Only the mutation pass caught it. A test written from the fix
  rather than from the failure is the trap; write it against the real sequence
  (cancel, then re-run the same ticket).

## Defects Phase 3 found and fixed, in order

Kept because the pattern is the useful part: **five of the six were found by probing the
real sandbox before spending a model run, and each would have cost a ~20-minute run to
find otherwise.**

1. `pnpm test` in a scratch with no `node_modules` exits 1 saying "ELIFECYCLE Test failed",
   and `_ASSERTION_FAILURE_SIGNS` matched the bare word `failed` — so a replay in which
   **no test executed** was classified as a real red phase. The worst verdict the module
   can produce and the hardest to notice, because it looks like success. Fixed twice over:
   `_RUNNER_MISSING_SIGNS` dominates both existing lists, and `clone.scratch_add` links the
   clone's dependencies so the gate actually runs.
2. `ensure` would silently attach to a bind-mounted sandbox of the same name — the
   workspace set is identical either way, because the clone sits at the project's own path.
   New preflight `clone-isolates-the-workspace` proves isolation by producing the
   observable, §9.3-style.
3. The `sandbox-<name>` remote is not durable (see footgun 4).
4. The git daemon's host port is reassigned on every start.
5. `deliver` and `review` rebuilt the attempt path from `ctx.worktree`, which `fetch_back`
   repoints at the host mirror while the evidence stays on the mount. Every clone run would
   have shipped an empty gate table and collected no artifact. No test caught it because
   every other test runs bind-mounted, where the two paths are the same string.

## State of the board

- **BAC** — done and archived. Nothing pending.
- **FRO-10 / FRO-9** — FRO-10 Done. FRO-9 is the parent spec I filed for it; it is a spec,
  not a work item, and carries no `ready-for-agent` label. Move it to Backlog if it clutters.
- **FRO-6** — **`awaiting_human`, draft PR [frontend-harness#41](https://github.com/jchen1707/frontend-harness/pull/41).**
  Resumed 2026-08-22 via `--from reviewing` (verify bypassed — lighthouse can't pass, see
  "CLOSED — Tier 2 executed"). The run (`ab00d69c38844d2c`, 2026-08-22 01:28-01:51) is real
  work: 22 minutes, 10 582 685 in / 34 692 out, four files and four tests written. The gate
  report's five stop gates + playwright all pass; `lighthouse = fail` (Chrome missing, out
  of scope, can't pass per its caveat) is reported honestly in the PR body. Tier 2 ran
  forced and produced four `medium` findings. The `needs-info` label from the original
  block is still on the Linear ticket — resume bypasses intake, so it never mattered for
  the resume, but a human merging #41 should clear it.
- **FRO-5** — Done. Landed by frontend-harness#40, a merge-forward of the long-closed
  `feat/FRO-5-projects-list` across 64 commits of drift, with three conflicts resolved and
  all seven CI checks green. Linear moved it to Done off the `Fixes FRO-5` line. Verified
  on `origin/v2` on 2026-08-22: `src/features/projects/**` is present.
- **FRO-7** — `In Progress` with `needs-info`, written by its own blocked run. It is
  blocked by FRO-6 (a formal Linear relation) and **intake condition 11 now refuses it for
  free**, so it needs no babysitting: clear the label whenever FRO-6 lands and the poller
  will take it.
- **FRO-8** — Todo, refused by intake for having no parent spec. It is the same defect
  FRO-10 fixed; it should probably be closed as duplicate.
- **FRO-1** — the parent of FRO-2..7. Intake refuses it correctly; it is a spec.

## Two facts Phase 3 learned that the rest of Phase 4 still depends on

- **`BLOCKED` has only two exits and neither is the one `resume` needs.** See "Build
  `resume` next" — this is the same observation the Phase 3 handoff made as "there is no
  resume", now with the specific edge named.
- **The host process is out of the run's TCB but the machine is not** (§4.2, and the long
  comment in `sbx.py:exec_detached`). A daemon that ticks every 60 s must read liveness
  from `<attempt>/sbx-exec.pid` and the heartbeat, never from its own memory. `reap.py`
  does exactly that and nothing else; keep it that way.
