# ADR-0002 — `--dry-run` deleted from the run pipeline

- **Status** — accepted, 2026-08-27
- **Replaced by** — `factory run <TICKET> --check`
- **Does not touch** — `factory gc --dry-run`

## The decision

`Context.dry_run`, `shadow_state`, `shadow_worktree`, `shadow_branch`, `planned` and
`would()` are deleted, along with every `if ctx.dry_run:` branch behind them — ~130 sites
across 17 modules. `factory run --dry-run` is gone from the parser.

`factory run <TICKET> --check` replaces the documented promise: run `assess`, print the
same condition list, stop before `insert_run`.

## Why deleted rather than rebuilt

It was **an untested simulation of a state machine**.

Every step carried a second body under `if ctx.dry_run:` that described what the first
body would have done. `advance` had a shadow copy of the transition it would have
recorded. `Context.worktree`, `Context.branch`, `Context.state` and `Context.factory_dir`
each answered from the shadow when the real answer was absent. That is a second
implementation of the pipeline, and nobody had ever checked it agreed with the first —
which is precisely the drift `AGENTS.md` says the three-layer scheme exists to end, living
inside the layer that exists to end it.

**Measured on 2026-08-27: no test drove the run pipeline with `dry_run=True`.** Three
tests set the flag; all three asserted on `ctx.planned` — the *preview strings*, not the
simulated pipeline. Nothing anywhere asserted the shadow states matched the real ones.

Meanwhile the README advertised it twice, so a human had a documented reason to trust it.

The shape of the rot was visible where the two implementations had already diverged:
`cli._block` called `block_step.announce` even in a dry run, so `factory run --dry-run`
on an ineligible ticket wrote a comment and a `needs-info` label to Linear. The preview
that promised to execute nothing was the only path in the file that did not check the flag
before an external write.

## What `--check` is, and what it is not

It is the question a human actually asked `--dry-run`: *would this ticket run?*

`cmd_run` already answers it, in its first 25 lines, before any write — the assessment is
a pure read of Linear and git. `--check` prints that and returns. ~15 lines.

**Its exit codes mirror `cmd_run` exactly** — 0 eligible, 1 not-eligible-with-no-reason,
2 blocked — or it is a second opinion rather than a preview, and a script that trusted it
would learn the difference at the wrong moment. That is the property under test.

It is *not* a preview of the pipeline, and deliberately so. Everything after `insert_run`
depends on what an agent writes; a preview of it is a guess, and a guess printed in the
shape of a fact is worse than no preview.

## `gc --dry-run` is untouched

It looks like the same flag and is not. `gc --dry-run` is a real sweep planner: it
enumerates the worktrees, branches, sandboxes and artifacts a sweep would reclaim, from
the same code that would reclaim them. It is tested. And `gc.sweep` takes `dry_run` as
**its own parameter** and never sees a `Context`, so deleting the `Context` fields does
not reach it — verified.

## Consequences

- `cli._open_store` loses its `dry_run` parameter, and with it the throwaway
  `state/dry-run.db`. There is one state file.
- Three tests are deleted: two asserted on the preview strings, one on the clone chain's
  preview. Their subjects went with the flag. The one property worth keeping —
  `--draft` is not in the real `gh pr create` argv — was already asserted against the
  real argv in `test_phase3.py`.
- `test_no_run_pipeline_module_simulates_itself` in `tests/unit/test_boundaries.py` is a
  grep, deliberately: one `if ctx.dry_run:` added to one step reads as a two-line
  convenience and is the whole thing again.
