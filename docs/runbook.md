# Operator runbook — when the factory is stuck

The factory stops at `awaiting_human` with a PR open, and merging is a human's. Everything
below is what to do when it stops *before* that. Every entry came from a real run, not from
imagination; the date and the ticket are named where known.

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

### `blocked: env-gate-failed` — an environment gate the agent cannot fix

The gate report failed only on gates whose `caveat` names an environment condition, not a
code defect — the `lighthouse`/`--all` hazard. Under `--all` every opt-in gate runs, so a
frontend ticket that claims `playwright` (in scope) also forces `lighthouse` (out of
scope), which fails on a missing Chrome and cannot pass even with it (its caveat: a null
score is not a pass). Looping back to `implementing` would spend another ~10 M-token
attempt "fixing" a missing tool, so the run blocks instead.

The PR body still reports the failing gate honestly. Two ways out:

- **Bypass the gate and let the PR open with the failure reported.** This is the FRO-6
  resolution (2026-08-22):
  ```
  factory resume <TICKET> --from reviewing
  ```
  Verify/review is skipped; the gate failure stays in the PR body for a human to see.
- **Install the tool the caveat names and re-run verify.** For lighthouse that is Chrome
  inside the build sandbox (`pnpm exec playwright install chromium` covers the browser
  gates; lighthouse additionally needs a Chrome it can drive). Then:
  ```
  factory resume <TICKET> --from verifying
  ```

`--from verifying` re-runs the gate report against the implement attempt's evidence. For a
`--clone` run, prefer `--from reviewing`: review fetches a fresh worktree, while verify
re-runs against a host mirror that a later run may have repointed (see the Phase 4 handoff's
open question). Clear the block in Linear once you have decided.

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

The ladder (§16.3a) exhausted: three attempts, then a planning rewind, then a fourth
failure. Re-authorising spend is James's explicit act:

```
factory resume <TICKET> --authorise
```

This is deliberately not automatic. A run that failed four times is telling you something a
fifth attempt will not fix — read the evidence first.

### `illegal-transition` — a factory bug, not a stuck run

If a transition raises `illegal-transition`, the state machine and the step disagree about
where the run is. **Fix the code; do not re-run.** Re-running pays for another attempt and
will hit the same bug. The FRO-6 resume (2026-08-22) hit this when `implement.start`
re-recorded `implementing -> implementing` after a verify-fail loop-back; the fix was in
`implement.start`, not in the ticket. File the defect, attach `factory status --evidence`,
and resume only after the fix lands.

## It opened a bad PR

The PR is always a **draft** (through Phase 4). Nothing is merged by the factory — that is
a unit-tested invariant (`tests/unit/test_boundaries.py`).

- **Wrong content / stale evidence:** `factory cancel <TICKET>` closes the run, then
  `factory run <TICKET>` starts fresh. The draft PR on GitHub is left for you to close by
  hand (`gh pr close <n> --delete-branch`) — the factory never deletes a pushed branch.
- **A secret reached the PR body:** the run already failed with `secret-in-artifact`
  before the push. Rotate the credential; the value is compromised regardless of whether
  the push happened.
- **A disputed review finding:** the PR body records both positions under "Disputed
  findings". Decide on GitHub; the factory does not override a finding the implementer
  declined to fix.

## The daemon did nothing / is not running

```
launchctl print gui/$(id -u)/com.jchen.factory   # is it loaded?
tail -50 ~/factory/logs/daemon.err.log           # the last tick's failure
factory doctor                                   # registry, routing, sbx, codex, gh, disk, db
```

Common causes:

- **`tick refused: sqlite says …`** — the DB failed `PRAGMA integrity_check`. Restore from
  the nightly copy (`~/factory/state/factory.db.bak*`); the DB is rebuildable from Linear +
  git + artifacts, so nothing is lost.
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

`factory serve` opens the same five views in a browser at <http://127.0.0.1:7717> — the
board and the run detail refresh without a reload, and the per-run controls (Suspend,
Resume, Resume from planning, Cancel, Retry now) are the same code paths as the commands
above. It binds loopback only and holds no credential of its own. **There is no Merge
button**: merging is James's, on GitHub, and the console links out to the pull request.

The context percentage is shown only when it can be defended — the agent's completed-turn
`input_tokens` over the routed model's window from `~/.codex/models_cache.json`. Where
either is missing the column shows `—` and names the reason on hover; it is never
estimated. A run above ~70% that is still failing gates is one to rewind to `planning`
(§16.3a) rather than retry.

The transition `rule` on each hop is the named reason the factory took it. An unexplained
hop is a bug; a hop with `rule="backoff"` is the daemon waiting §16.4's schedule. When in
doubt, `factory suspend <TICKET>` and read the evidence before resuming — the worktree and
session are kept, so nothing is lost by stopping to look.