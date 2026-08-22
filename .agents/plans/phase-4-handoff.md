# Phase 4 handoff — 2026-08-22

Phase 3 is closed. `SOFTWARE-FACTORY-PLAN.md` §19 "Phase 4 — polling, idempotency,
recovery, resume, cleanup" (line 2188) is the specification for what comes next; this file
records only what a new session cannot read out of the plan or the code.

Read `.agents/plans/phase-3-handoff.md` for the Phase 2/3 archaeology. Everything it lists
as open is now closed except one item, named below.

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

**Tier 2 has still never executed to completion.** This section stays open.

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

## Defects found and fixed this session, in order

The pattern is the useful part: **five of the six were found by probing the real sandbox
before spending a model run, and each would have cost a ~20-minute run to find otherwise.**

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
- **FRO-6** — `In Progress` with `needs-info`. Its blocker (FRO-5) is gone, so the ticket is
  ready, but the label still holds it at intake by design and a human must remove it. Its
  `blocked` run row is also the natural first test subject for Phase 4's `resume`.
- **FRO-5** — Done. Landed by frontend-harness#40, a merge-forward of the long-closed
  `feat/FRO-5-projects-list` across 64 commits of drift, with three conflicts resolved and
  all seven CI checks green. Linear moved it to Done off the `Fixes FRO-5` line.
- **FRO-7** — Todo, unblocked, passes intake, nothing in its way. The Tier-2 candidate.
- **FRO-8** — Todo, refused by intake for having no parent spec. It is the same defect
  FRO-10 fixed; it should probably be closed as duplicate.
- **FRO-1** — the parent of FRO-2..7. Intake refuses it correctly; it is a spec.

## Phase 4

`SOFTWARE-FACTORY-PLAN.md` line 2188 has the file table and the validation commands. The
headline is that Phase 4 is where the factory stops needing a human to type `factory run`:
`tick`/`daemon` under launchd, `steps/reap.py` for orphan detection, `recovery.py` for
§16.3 resume-vs-restart, `gc.py` for §16.5, the §16.3a rewind ladder in `plan.py`, and the
loopback console in `src/factory/console/`.

Two things Phase 3 learned that Phase 4 depends on:

- **There is still no resume.** `machine.py:104` makes `BLOCKED → REVIEWING` illegal and
  unblocking is human-only. Phase 4's `resume` is the first time a blocked run can move
  without a fresh implement, and the FRO-6 run sitting at `In Progress` is a live test
  subject for it.
- **The host process is out of the run's TCB but the machine is not** (§4.2, and the long
  comment in `sbx.py:exec_detached`). A daemon that ticks every 60 s must read liveness
  from `<attempt>/sbx-exec.pid` and the heartbeat, never from its own memory — the tick
  that asks is never the tick that started the run.
