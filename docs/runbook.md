# Operator runbook — when the factory is stuck

Successful delivery stops at `awaiting_human` with a PR open; merging belongs to James.
The same state can also hold a review escalation before a PR exists. Inspect the recorded
reason before choosing an action. This is current operator guidance; dated examples
describe historical runs and do not override current policy or evidence.

The first command in every case is `factory status <TICKET> --evidence` — the transition
timeline, the recorded checks and the effects ledger tell you which state below you are in.
`factory status --all` shows every run on one screen.

## I want it to stop right now

| You want | Type | What it does |
| --- | --- | --- |
| Park a run, keep its work and session | `factory suspend <TICKET> --reason "…"` | Signals the agent, waits for a real `exit`, stops the build sandbox if nothing else uses it, records `suspended`. Resume with `factory resume <TICKET>`. |
| Abandon a run and clean up | `factory cancel <TICKET>` | Removes the worktree, deletes the **unpushed** branch, releases the lease, stops the sandbox, moves Linear back to `Todo` and removes `needs-info`. A pushed branch is never deleted. |
| Stop the daemon | `launchctl bootout gui/$(id -u)/com.jchen.factory` | The daemon is stateless between ticks, so stopping it is safe at any instant. In-flight leases expire on their own. |

`cancel` is irreversible for the worktree; `suspend` is not. When in doubt, suspend.

## It is stuck

### `blocked: env-gate-failed` — inspect the environment evidence

Read the retained gate report and its caveats. Resolve the named environment requirement
in the target's declared runtime, then use `factory resume <TICKET> --from verifying`
when the preserved source and authority still match. Do not copy an old run's gate bypass
or install commands into a new run: gate selection belongs to the target harness.
Historical FRO-6 bypass advice is superseded by this evidence-first procedure.

### `blocked` with any other reason — a judgement call

Every other `blocked` reason is a human judgement: `evidence-mismatch`, `gates-incomplete`,
`vault-write-outside-allowlist`, `schema-invalid`, `host-execution-deny`, `no-parent-spec`,
`state-not-todo`, `budget-exceeded`. Read the reason and the evidence path, fix the
underlying condition in Linear or the environment, then:

```
factory resume <TICKET>              # re-enter the state that blocked
factory resume <TICKET> --from planning   # rewind to a fresh plan instead
```

`blocked` never retries on its own — a human is the only path out. That is the design.

### `resumable` — an attempt died

The agent, the gate report, or a review orphaned or timed out. The tick picks it up
automatically on the next pass (after §16.4's 0/60/300 s backoff), so usually you do
nothing. To drive it immediately:

```
factory resume <TICKET>
```

If the run keeps returning to `resumable`, check `factory status <TICKET> --evidence` for
the `rule` on each `-> resumable` transition — a repeating `agent-failed` is a stuck agent,
and `factory resume <TICKET> --from planning` rewinds to a fresh plan rather than a third
identical attempt.

### `failed` — the attempt budget is spent

The recorded recovery or spend limit is exhausted. Inspect the run's frozen policy,
failure episodes and lifetime accounting rather than assuming a historical fixed
attempt count. Re-authorising spend is James's explicit act:

```
factory resume <TICKET> --authorise
```

This is deliberately not automatic. Read the evidence and resolve the cause before
authorizing another attempt.

### `illegal-transition` — a factory bug, not a stuck run

If a transition raises `illegal-transition`, the state machine and the step disagree about
where the run is. **Fix the code; do not re-run.** Re-running pays for another attempt and
will hit the same bug. The FRO-6 resume (2026-08-22) hit this when `implement.start`
re-recorded `implementing -> implementing` after a verify-fail loop-back; the fix was in
`implement.start`, not in the ticket. File the defect, attach `factory status --evidence`,
and resume only after the fix lands.

## It opened a bad PR

The PR opens **ready for review** (§24.8) — the gates and the review already ran, so
"draft" was the wrong word for it. It is not a trigger: `agent-review.yml` is label-gated
in both harnesses (add the `agent-review` label), and `ci.yml` ran on drafts too. Nothing
is merged by the factory, and that is a unit-tested invariant
(`tests/unit/test_boundaries.py`).

- **Wrong content / stale evidence:** `factory cancel <TICKET>` closes the run, then
  `factory run <TICKET>` starts fresh. The PR on GitHub is left for you to close by
  hand (`gh pr close <n> --delete-branch`) — the factory never deletes a pushed branch.
- **A secret reached the PR body:** the run already failed with `secret-in-artifact`
  before the push. Rotate the credential; the value is compromised regardless of whether
  the push happened.
- **A disputed review finding:** after reading the recorded critical/high findings, James
  may run `factory accept <TICKET> --review-finding --note "<reason>"` on a run blocked by
  `review-finding`. The command requires passing gates and a recorded blocking finding,
  writes James's judgement to the checks ledger, and enters delivery. The PR body keeps the
  findings and records James's position separately under "Disputed review findings".

## The daemon did nothing / is not running

```
launchctl print gui/$(id -u)/com.jchen.factory   # is it loaded?
tail -50 ~/factory/logs/daemon.err.log           # the last tick's failure
factory doctor                                   # registry, routing, sbx, codex, gh, disk, db
```

Common causes:

- **`tick refused: sqlite says …`** — the DB failed `PRAGMA integrity_check`. Restore from
  the nightly copy (`~/factory/state/factory.db.bak*`); the database is **not** reconstructible from Linear and Git. Preserve the damaged
  database and artifacts before recovery, and follow the
  [backup and rollback procedure](runtime-certification-rollout.md).
- **A bad `models.toml`** — the tick refuses and names the rule (F25): the reviewer sharing
  the builder's model, an unknown model, `ultra` on the builder, or a bad budget. Fix
  `config/models.toml`; the next tick succeeds. The daemon never falls back to a default.
- **Disk below the floor** — the tick prints `disk below the floor`, claims no new work,
  and keeps advancing existing runs. Run `factory gc --dry-run` to see what it would
  reclaim, then `factory gc`.
- **Linear unreachable** — the tick catches the adapter error, leaves every run in place,
  and retries next interval. No state changes on a failed write; nothing to do but wait.

## It is stuck and I am not sure why

```
factory status                          # the board: every non-terminal run, live
factory status <TICKET> --evidence      # transitions, gate table, review findings, artifacts
factory logs <TICKET> --follow          # tail the agent's own event stream
factory runtimes                        # sandboxes joined to the runs using them
uv run factory doctor --deep            # includes a live codex hook canary (costs a model call)

tail -f ~/factory/logs/factory-$(date +%Y-%m-%d).jsonl   # the structured control-plane log
```

`factory serve` opens the seven views (runs, projects, run details, timeline, run settings, runtimes and configuration) in a browser at <http://127.0.0.1:7717> — the
board and the run detail refresh without a reload, and the per-run controls (Suspend,
Resume, Resume from planning, Cancel, Retry now) are the same code paths as the commands
above. It binds loopback only and holds no credential of its own. **There is no Merge
button**: merging is James's, on GitHub, and the console links out to the pull request.

Context occupancy, cumulative tokens and estimated costs are separate measurements.
The console labels unavailable or stale context observations; cumulative billed usage
must never stand in for occupancy. Cost is an API-equivalent USD estimate, not an account
charge. Missing usage or prices leaves evidence incomplete and known cost a lower bound.
See [telemetry and costs](operator-reference.md#telemetry-and-costs).

The transition `rule` on each hop is the named reason the factory took it. An unexplained
hop is a bug; a hop with `rule="backoff"` is the daemon waiting §16.4's schedule. When in
doubt, `factory suspend <TICKET>` and read the evidence before resuming — the worktree and
session are kept, so nothing is lost by stopping to look.