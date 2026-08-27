# Handoff — the architecture review's remaining PRs (2–5)

Written 2026-08-27, after PRs 0 and 1 shipped. Everything below is **settled**: it came
out of a five-round design interrogation and James approved each recommendation, including
one decision `AGENTS.md` reserves for him. This is not a plan to re-open. Where a decision
has a reason, the reason is given, because the reason is the part that stops it being
re-litigated.

`CONTEXT.md` (shipped in PR 0) is the domain vocabulary. The architecture words — module,
interface, depth, seam, adapter, leverage, locality — come from the `codebase-design` skill
and are used deliberately.

## Where this came from

An architecture review of the hot spots (`console/`, `recovery.py`, `steps/verify.py`,
`cli.py` at 2311 lines). Five candidates, six PRs, ordered so the two defects land before
the refactors that would otherwise bury them.

| PR | What | State |
| --- | --- | --- |
| 0 | `reap` signals the process the state actually runs | **merged** (#68) |
| 1 | the ledger cannot record a hop the table forbids | **open, clean** (#69) |
| 2 | the `driver` module | not started |
| 3 | `routing` owns writing `models.toml` | not started |
| 4 | the `doctor` module | not started |
| 5 | delete `--dry-run` from the run pipeline | not started |

PR 2 depends on PR 1's `steps.record_stop`. PRs 3–5 are independent of each other and of
PR 2; they can land in any order once 1 is in. **Branch each off `main` after #69 merges.**

### One process note, learned the hard way

#68 and #69 both inserted a new test at the same anchor in `tests/integration/test_phase4.py`
and the second one conflicted. When two of these PRs touch the same test file, put the new
test at the **end** of the file, or next to the test that shares its subject. Do not insert
at a convenient mid-file anchor.

---

## PR 2 — the `driver` module

The big one. `AGENTS.md` defines layer D as the thing that "decides *what runs, when, in
what order, and what happens when it dies*." That sentence has no module: it is spread over
six private functions in `cli.py`, written twice, and the two copies disagree.

### What exists today

Two execution models for one pipeline:

- **Foreground** — `cli._drive` calls the steps in a fixed chain, and `plan.run`,
  `implement.run`, `verify.run`, `review.run` are each a ~6-line shell around
  `start` → wait-for-exit → `collect`. Three of them duplicate `_await_exit`; `plan`
  inlines its own poll loop.
- **Detached** — `cli._FORWARD` (a hand-written `dict[State, str]`) → `cli._perform`
  (string → step call), driven by `cli._drive_from_here`, with `_start_detached` and
  `_start_next_after_gate_fail` as special cases off `reap`'s verdicts.

`machine.py` is the pure transition table and holds no entry actions, so nothing checks
that `_FORWARD` agrees with `_drive`. `machine.py`'s own comment already names this failure
mode — *"Prefer deriving to enumerating"* — after `_WORKFLOW` missed `resumable` and then
`blocked`. `_FORWARD` is the same hand-written list, one file away.

### The settled design

**`machine.ENTRY: dict[State, Action]`**, with `Action` a `StrEnum` beside `TRANSITIONS`.
`machine.py` stays pure and cannot import `steps` (circular), so it holds the *action name*
and `driver` maps name → callable. This was chosen over a plain `dict[State, Callable]` in
`driver.py` and over per-step `ENTERS` declarations, for one reason: it is the only option
a soundness test can be written against.

`ENTRY` covers **every state the factory acts on**, with `REAP` and `RECOVER` as actions
alongside `CLAIM`, `CONTEXT`, `SANDBOX`, `WORKTREE`, `START_AGENT`, `DELIVER`. Not just the
seven forward states. "Which states are detached" is currently a hand-written tuple in
`reap.py` that must agree with `_FORWARD`'s *absence* of those states, and nothing checks
that either — folding both into one table is the point.

**The soundness test is the deliverable**, and it must be derived, not listed:

> every non-terminal state either has an entry action or is human-held

where *human-held* is computed, not enumerated: a state is human-held iff every exit that
is not a stop (`blocked`, `cancelled`) has a `machine.requires_human_rule`. Verified by
hand against the current table: `BLOCKED`, `AWAITING_HUMAN`, `SUSPENDED` and `FAILED` are
human-held; `APPROVED` and `RESUMABLE` are not. **Do not hand-write a third list of
human-held states — that is the defect this PR exists to remove.**

**`driver.step(ctx) -> Result`** with `Outcome` = `PROGRESSED | WAITING | STOPPED |
NEEDS_HUMAN`, plus a `detail: str`. `reap.Outcome` (6 members) becomes internal to `reap`
and maps into it. The loop condition today is `if ctx.state is before: break`, which
conflates "an agent is running" with "this run is finished" — `WAITING` vs `PROGRESSED` is
that distinction, and it is why returning the new `State` instead was rejected.

`step` **renews the lease whenever it returns `WAITING`**. Under a foreground loop an
`implementing` state returns `WAITING` immediately and the loop sleeps, possibly for hours,
holding a 900 s lease. Renewing in `drive`'s loop was rejected as less precise; dropping
the lease while waiting was rejected because it opens a window for a second process to
claim a run whose agent is live (the daemon gets away with that only because a tick is
short). Invariant: **a `WAITING` run is leased by whoever is watching it.**

**`driver.drive(ctx)`** is the only `try`. It catches `Blocked` and `Resumable` — which stay
the step vocabulary, because `reap.reap`'s contract depends on `collect` raising through it
— and turns them into `Outcome.STOPPED` carrying the reason slug, via PR 1's
`steps.record_stop`. After this, `cmd_run`, `tick_once` and `dispatch_control` differ only
in how they format one outcome. No caller writes its own handler again.

**`reap.act(ctx)` owns the two follow-ons.** This was nearly got wrong: the tempting design
is one action per `step`, returning after the reap and letting the next loop iteration start
the attempt. **It infinite-loops.** `reap`'s `NEXT_STEP` and `START_NEEDED` verdicts are
derived from state only the follow-on changes — `NEXT_STEP` reads a finished attempt row for
`run.attempt`, and nothing increments `runs.attempt` except `start_attempt`, which the
follow-on calls. The daemon gets away with breaking between them because a *tick boundary*
is not a loop iteration. So the follow-on runs inside the `REAP` action, in `reap.py`, next
to the verdict that produces it — not in `driver`, which must hold no domain judgement.
`reap.py` already imports `verify_step` and `review_step` at module level, so `_start_detached`
moves there for free; it needs a function-local `recovery` import, which is the pattern
`recovery` already uses in seven places.

**`steps.start_agent(ctx)` in `steps/__init__.py`** owns the plan-vs-implement choice
(`plan.should_plan`), so `ENTRY[WORKTREE_READY] = START_AGENT` and `driver` never learns that
planning exists. Needs a function-local import of `steps.plan` / `steps.implement`.
A dedicated `steps/agent.py` was rejected (one function forever); putting it in `plan.py`
was rejected (makes `implement` reachable only through `plan`, which reads wrong in a stack
trace).

**`factory run` becomes a foreground loop over `driver.step`, `--follow` on by default.**
It inherits `reap`'s orphan detection, which it never had, and `reap`'s `KILL_GRACE_SECONDS`
in place of each step's own 60 s wait. The runbook already calls foreground `factory run`
orphan-prone with nohup as the mitigation; making it identical to the daemon's path is the
fix, not a regression.

**Schema version 4** — a `force_plan` column on `runs`, matching `full_review`'s shape
exactly (`INTEGER NOT NULL DEFAULT 0`, set at `insert_run`), with an `_upgrade_3_to_4`
walking the same `_migrate` ladder as `_upgrade_1_to_2`. **James approved this explicitly**
on 2026-08-27; `AGENTS.md` reserves schema migrations for him, so do not treat the approval
as extending to any other migration. It fixes a real hole: `--plan` is threaded through
`_drive(ctx, force_plan=...)` today, so a `factory run --plan` that dies before
`worktree_ready` is resumed by the daemon without the flag.

### Deletes

`_drive`, `_FORWARD`, `_perform`, `_drive_from_here`, `_start_detached`,
`_start_next_after_gate_fail`, the four `run()` wrappers, the three `_await_exit` copies,
three of the four `POLL_INTERVAL_SECONDS = 10` declarations, and two of the three 900 s
lease TTLs.

### The blast radius — read this before starting

Deleting the four `run()` wrappers is not a `cli.py` change. Measured on 2026-08-27:

- `verify_step.run(` — ~30 call sites, almost all in `test_pipeline.py`, `test_phase4.py`,
  `test_phase3.py`
- `review_step.run(` — ~13, mostly `test_phase3.py`
- `implement_step.run(` — ~6
- `plan_step.run(` — few

**`claim`, `context`, `sandbox`, `worktree` and `deliver` keep their `run()`** — they are
synchronous steps, not start/wait/collect wrappers. Only the four detached ones go. Do not
mass-rename `_step.run(`.

The recommended migration is a **test-only** helper in `tests/integration/conftest.py` —
something like `advance_state(ctx)`, "loop `driver.step` until the state changes" — and a
mechanical substitution of the ~50 sites. Do **not** add that helper to `driver` itself:
`factory run` needs `drive`, and production code that exists only for tests is the smell
this whole review is about. The fake writes `exit` synchronously inside `exec_detached`, so
one `verify_step.run(ctx)` is generally two `driver.step` calls (reap → `START_NEEDED` →
start; reap → `EXITED` → collect).

### Evidence bar

Green suite, **plus one real unattended run**, recorded in `docs/discovery/` the way
Phases 1–6 were. PR 2 changes how every run executes and is the only one of the four
refactors that earns a real run. Plus the derived soundness test above — if it cannot be
written, the deepening bought nothing.

Ships with **ADR-0001 — one execution model**, in `docs/adr/` (the directory does not exist
yet; create it). Write it *with* the change, not before: an ADR written ahead of the work
records an intention, and this is a repo where four phases of measured evidence say those
differ. It exists because four docstrings currently argue *for* the foreground/detached
split — delete them without recording why and someone re-adds `run()` wrappers.

---

## PR 3 — `routing` owns writing `models.toml`

**Pure move, and it should stay that boring.**

`routing.py` owns reading `models.toml`; `console/app.py` owns editing it —
`_rewrite_models_toml` (782), `_replace_value` (823), `_validate_models_toml` (845). And
`load_routing` takes only a path, so validating a candidate means writing it to a
`NamedTemporaryFile`, parsing it back and deleting it. That tempfile round-trip is the
interface's shape leaking into the caller.

- Move all three into `routing` **verbatim**. Keep the targeted line rewrite; do **not**
  re-serialise through a TOML writer. The docstring's reason is sound: the file carries the
  measured model catalogue and a page of comments explaining why each number is what it is.
- Add `routing.validate(text) -> Routing`; `load_routing` becomes read-then-validate. The
  tempfile dance deletes.
- **No `cmd_config --set`.** It becomes trivial once `routing.apply` exists; land it
  separately if wanted. The value here is the seam.

**Property test:** a `routing.validate` round-trip — the thing that could not be written
while validation required a path on disk.

---

## PR 4 — the `doctor` module

`cmd_doctor` (`cli.py:1728–2100`) is ~100 lines of discovery + execution + formatting in an
argparse handler, over 18 checks with four different return shapes. Three are tested, each
by importing a `cli` private — testing past the interface.

- A `doctor` module. `DoctorContext` (home, registry, store) is passed to every check, so
  they all have one shape. Zero-arg closures were rejected: half the checks need `registry`,
  and `cmd_doctor` threads them by hand with `if registry:` guards — that threading *is* the
  friction, and closures just move it into the list construction.
- **The config checks run first and produce the context.** If they fail, dependent checks
  report a distinct third status, `skipped: registry did not load`. Today those three check
  families *vanish from the output entirely* when `projects.toml` is broken — a doctor that
  reports fewer failures the more broken the machine is. That is the bug worth designing out,
  and it is the same discipline as `gates_run` vs `skipped_unchanged`, which already cost a
  Phase 2 defect.
- **Exit code unchanged**: 1 on failures only. A skip is "I could not check this", not a
  failure of the machine. But the summary must read `3 check(s) failed, 4 skipped` — it must
  be impossible to read the output as a clean bill of health.
- **No console view in this PR.** That is a new page with its own layout decisions, and the
  console has its own plan.

**Property test:** every check has the same shape and is exercised through `doctor.run`
— coverage that the current heterogeneous functions cannot express.

---

## PR 5 — delete `--dry-run` from the run pipeline

`Context.dry_run`, `shadow_state`, `shadow_worktree`, `shadow_branch`, `planned` and
`would()` all go. ~130 sites across 17 modules.

**Why delete rather than rebuild:** it is an untested simulation of a state machine — a
second implementation nobody has ever checked agrees with the first, which is the exact
drift `AGENTS.md` says the three-layer scheme exists to end. Measured: **no test drives the
run pipeline with `dry_run=True`.** README advertises it twice.

**`gc --dry-run` is untouched.** It is a real sweep planner, it *is* tested, and `gc.sweep`
takes `dry_run` as its own parameter and never sees `Context`. Verified — deleting the
`Context` fields does not reach it.

**`factory run <T> --check` replaces the documented promise.** Run `assess`, print the same
condition list, stop before `insert_run`. It is ~15 lines and it is what `cmd_run` already
prints in its first 25 lines before any write. **Exit codes mirror `cmd_run` exactly** —
0 eligible, 1 not-eligible-with-no-reason, 2 blocked — or it is a second opinion rather than
a preview.

Watch for: `cli._block` calls `block_step.announce` even in dry-run today; that branch just
deletes.

**Property test:** a grep that `shadow_` and `dry_run` appear nowhere outside `gc.py` —
plus a unit test for `--check`'s three exit codes.

Ships with **ADR-0002 — `--dry-run` deleted from the run pipeline**. It was a documented
feature; the reason it went needs to outlive the diff.

---

## Two things deliberately not done

- **The plan is not touched.** `SOFTWARE-FACTORY-PLAN.md` is declared complete and
  `AGENTS.md` forbids re-planning. None of this changes what the machine does, so it is not
  a Phase 8. The record lives in `CONTEXT.md` and two ADRs.
- **Only two ADRs.** The other four decisions are legible from the code. An ADR is for a
  decision a future reader would otherwise re-litigate, and both of these have prose in the
  current codebase arguing the opposite side.
