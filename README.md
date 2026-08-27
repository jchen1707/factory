# factory

Layer D — the control plane.

The factory takes one Linear ticket a human approved, drives it through a sandboxed agent
under this system's own gates, and stops at the boundary a human owns. It is not clever. It
is a machine that survives a crash, resumes the ticket it was on, never repeats a side
effect, and produces evidence you can audit without re-running anything.

`SOFTWARE-FACTORY-PLAN.md` is the specification and it is complete. `AGENTS.md` is the
instruction file for anyone — human or agent — working in this repo. `docs/discovery/` is
Phase 0's measured evidence, and where a measurement contradicts the plan, the discovery
file wins. `docs/runbook.md` is the operator's "it is stuck, what do I type" reference.

## Where this sits

| Layer | Repository | What it owns |
| --- | --- | --- |
| A | [`harness`](https://github.com/jchen1707/harness) | stack-neutral gates, review frames, hooks |
| B | `python-harness`, `frontend-harness` | one stack's config and checklists |
| C | a product repository | the product |
| **D** | **this repository** | **what runs, when, in what order, and what happens when it dies** |

This repository consumes all three and is consumed by none of them. It holds **no gate
command and no review prompt**: it reads them from the target repo's `harness.config.json`
and the vendored layer-A tree at `.agents/vendor/harness/`. A gate name appearing anywhere in
`src/` is a review failure.

## Status

Phases 1–6 are complete. The factory runs the full lifecycle unattended — from a
`ready-for-agent` Linear ticket to an open pull request that waits for James to merge — and
survives crashes, reboots, and killed processes along the way. Phase 7 (durable/remote
execution) is **trigger-gated and has not fired**: it stays a paragraph until a lost sandbox
costs a ticket, concurrency outgrows the laptop, or passthrough I/O dominates wall-clock. See
§19 Phase 7 of the plan for the verdict and the re-check conditions.

```sh
uv sync
uv run factory doctor                    # is this machine able to run the factory
uv run factory status --all              # every run, its state, attempt, lease, cost
uv run factory run BAC-6 --check          # evaluate the intake conditions, write nothing
uv run factory run BAC-6                 # drive one ticket in the foreground
```

## The lifecycle of one ticket

Each ticket is a row in `state/factory.db` and a fixed workflow — a state machine, not a
negotiation. Python decides control flow; the model decides only what to write inside one
step. The states:

| State | Who moves it | What happens |
| --- | --- | --- |
| `approved` | intake | a `ready-for-agent` ticket that passed the eligibility contract gets a row |
| `claimed` | factory | lease acquired; Linear moved to In Progress; one comment, through the effects ledger |
| `context_loaded` | factory | ticket + parent spec + comments written to `.factory/context/`; project resolved |
| `sandbox_creating` | factory | `sbx inspect` → create the `factory-build-<project>` sandbox if absent |
| `sandbox_ready` | factory | preflight: toolchain, `HARNESS_SKIP_VERIFY` unset, vendored tree intact, **no secret in the VM**, enforcement canary attached |
| `worktree_ready` | factory | `git worktree add` on the host, on `origin/<base>`, branch `<type>/<TEAM>-<n>-<slug>` |
| `planning` | factory | only when the ticket is large/ambiguous, or `--plan`, or attempt 3 of the rewind ladder |
| `implementing` | factory | detached `codex exec` with the implement prompt + `--output-schema`; runs as a session the factory does not hold |
| `verifying` | factory | `gate_report.mjs --json` in the build sandbox; advances on the report, cross-checked against the agent's claim |
| `reviewing` | factory | Tier 1 (Standards + Spec) read-only in `factory-review-<project>`; Tier 2 full fan-out when the change warrants it |
| `pr_ready` | factory | host-execution guard; push branch; open/update the PR with the evidence body |
| `awaiting_human` | **James** | PR open, Linear In Review, evidence attached — the factory stops here |
| `completed` | factory (observed) | James merged the PR; `factory complete` records it; `gc` reclaims the run |
| `blocked` | **James** | a judgement call (missing spec, env-gate, schema touch, budget). PR does not open. Carries `needs-info` |
| `resumable` | factory | a step died or the sandbox stopped; resume by session id, or restart with backoff. `failed` after the attempt budget |
| `suspended` | **James** | `factory suspend` parks a run; keeps worktree, branch, session |
| `failed` | **James** | attempt budget exhausted; `factory resume --authorise` re-authorises spend |
| `cancelled` | **James** | `factory cancel` abandons a run and cleans up after it |

Terminal states are `completed` and `cancelled`. The factory physically stops at every
transition `policy.requires_human()` names: `awaiting_human → completed` (merge), `blocked →
implementing` (unblock), `failed → resumable` (re-authorise spend), and any `→ cancelled` /
`→ suspended`. Everything else is automatic.

## Commands

All commands are `uv run factory <command>`. `run` drives one ticket in the foreground and is
typed by a human; `tick` is the same pipeline without the human — one pass that reaps,
recovers, advances, and only then claims new work. They share every step; what differs is
who waits.

### `run <ticket>` — drive one approved ticket

```sh
uv run factory run BAC-6
uv run factory run BAC-6 --check          # evaluate the intake conditions, write nothing
uv run factory run BAC-6 --plan          # force the planning step first
uv run factory run BAC-6 --full-review   # force Tier 2's full fan-out regardless of trigger
```

Stops at `awaiting_human` (PR open) or at a `blocked`/`failed` state. For a long run, invoke
it detached (`nohup`/`&`) — a foreground run killed at the terminal leaves an orphan the
next tick reaps.

### `tick` — one unattended pass

```sh
uv run factory tick --once               # one pass and exit (what the daemon calls)
uv run factory tick --once --verbose    # also report what it skipped
uv run factory tick --once --no-claim   # advance existing runs, start no new ones
```

A tick reaps whatever a previous pass started, recovers whatever died, moves every run that
can move, and only then looks for new `ready-for-agent` work. It never blocks on a model run
— a tick that blocked could not reap anything else while it did.

### `daemon` — tick in a loop

```sh
uv run factory daemon                   # default 60 s between passes
uv run factory daemon --interval 30
```

The launchd unit `ops/com.jchen.factory.plist` runs `tick --once` every 60 s instead of
holding a long-lived `daemon` process. Load it with `launchctl load
ops/com.jchen.factory.plist`; unload to stop the poller.

### `status` — what the factory is doing

```sh
uv run factory status                    # the active run, or the ticket you name
uv run factory status BAC-6
uv run factory status --all              # every run (the default with no ticket)
uv run factory status BAC-6 --evidence   # transitions, gates, review, artifacts
```

One line per run shows state, attempt, liveness, tokens, and spend against the $20 ceiling;
`--evidence` expands to the full audit trail.

### `logs` — tail a run's event stream

```sh
uv run factory logs BAC-6
uv run factory logs BAC-6 --follow        # keep tailing as the stream grows
```

### `runtimes` — sandboxes joined to runs

```sh
uv run factory runtimes
```

Lists every `sbx` sandbox on the machine and the run using it — so you can see which
`factory-*` sandboxes are live and confirm none is a `codex-*` (James's interactive `csbx`).

### `config` — render routing

```sh
uv run factory config models             # roles, efforts, budget, projects
```

Read-only. Model routing lives in `config/models.toml`, hot-reloaded on every tick and
validated on every read — a failing validation refuses to start rather than falling back to
a default.

### `serve` — the operator console

```sh
uv run factory serve
```

The §18.5 console: loopback-only, read-mostly, holds no credential of its own. Renders every
non-terminal run with state, attempt, ladder rung, tokens, spend, and liveness, and refreshes
without a reload. Model/effort edits are validated in the form before they are written (a
reviewer on the builder's model is rejected). It has no Merge button; every control writes an
`actor = "human"` transition.

### `doctor` — is this machine able to run the factory

```sh
uv run factory doctor
uv run factory doctor --deep             # also a live codex canary against a protected path (costs a model call)
```

Checks the registry, routing table, state machine, model cache freshness, git/gh/codex/sbx,
the sbx-stored OpenAI credential, the global gitignore, the Linear keychain credential, the
mattpocock execution set, the database, disk, vendored layer A in both consumers, sensitive
paths, and the plan copy. Reports green on a clean machine and names the exact missing thing
on a broken one.

### `cancel` — abandon a run

```sh
uv run factory cancel BAC-6
uv run factory cancel BAC-6 --reason "duplicate"
```

Kills the run, removes its worktree (only when it holds nothing but `.factory/`), deletes
its branch only when unpushed and carrying no commits beyond the base ref, and releases the
lease. Whatever it refuses to remove, it names.

### `complete` — the PR was merged

```sh
uv run factory complete BAC-6
```

Records that James merged the PR; lets `gc` reclaim the run's worktree, branch and sandbox.
`factory status` surfaces "PR merged — ready to complete" for runs whose PR has merged but
whose row is not yet completed.

### `accept` — clear a review escalation

```sh
uv run factory accept BAC-6
uv run factory accept BAC-6 --note "assertion weakened intentionally; see thread"
```

Clears a §15.3 escalation (a test-weakening finding that routed the run to `awaiting_human`
for a human decision) and lets the interrupted review run. The note is carried into the PR
body's cleared-escalations section.

### `suspend` / `resume` — park and resume a run

```sh
uv run factory suspend BAC-6             # stop the sandbox; keep worktree, branch, session
uv run factory resume BAC-6              # resume into the state it left
uv run factory resume BAC-6 --from planning     # rewind to a fresh plan without resetting the worktree
uv run factory resume BAC-6 --authorise         # re-authorise spend for a `failed` run (§16.4)
```

Resume restarts the Codex session **by id** (read from the event stream; never `--last`), so
no work is lost and no Linear comment is repeated. `--from` rewinds to a specific state
(`implementing`, `planning`, `verifying`, `reviewing`); `--authorise` is the human edge that
moves `failed → resumable`.

### `gc` — reclaim what finished runs left behind

```sh
uv run factory gc
uv run factory gc --dry-run              # name everything it would touch, touch none
```

Sweep driven by the floors in `config/projects.toml` `[defaults.gc]`: worktrees after
`worktree_days`, idle sandboxes after `sandbox_idle_hours`, removed after `sandbox_rm_days`,
artifacts after `artifact_days`. A pushed branch, an open PR, and a sandbox the factory did
not create are untouchable at any age.

## What the factory will not do

These hold in every phase and are enforced in code, not just stated here.

- **Merge the pull request.** No code path to `gh pr merge`, `gh pr review`, `git push
  --force`, or a Linear transition to Done — a unit test proves it. James merges.
- **Create a ticket.** No create-issue code path — a grep test proves it. Tickets come only
  from the `mattpocock-skills` intake system James runs.
- **Attach to a `codex-*` sandbox.** That is James's live `csbx` session. Factory sandboxes
  are `factory-build-*` and `factory-review-*`, asserted by name and unit-tested.
- **Write to `~/.codex/config.toml`.** Not to add project trust, not to install a plugin.
  Writing to a user's agent-trust store from an automated process defeats the trust store.
- **Edit anything under `.agents/vendor/`.** It is generated by `vendor_sync.py` and pinned
  by sha. Edit it in `harness@v2` and re-sync.
- **Edit `SOFTWARE-FACTORY-PLAN.md` or `docs/discovery/**`.** Protected paths; re-planning and
  rewriting dated evidence are James's decisions.
- **Create `~/.factory/`.** `sbx skills import` scans that path and it belongs to
  Factory.ai's Droid. This factory lives at `~/factory`.

## How a ticket becomes eligible

A ticket is picked up only when **all** of these hold. The factory verifies each and refuses
with a named reason when any fails (`blocked: <reason>` or not eligible at all).

1. labelled `ready-for-agent` — the signature that starts the factory and nothing else does
2. workflow state `Todo` (not Backlog, not In Progress)
3. team key is `BAC` or `FRO` (or a key in `config/projects.toml`)
4. has a parent issue
5. the parent's description is non-empty and ≥ 200 chars
6. the ticket description has an acceptance-criteria section
7. no `needs-info`, `needs-triage`, `ready-for-human` or `wontfix` label
8. no open PR already references the identifier
9. the resolved repo's `harness.config.json` `tracker.team` equals the identifier prefix

`ready-for-agent` is James's to apply and James's to remove. A ticket that reaches `Done`
but keeps the label re-appears as `blocked: state-not-todo` every tick — remove the label (or
unload the daemon) to quiet the board.

## Configuration

Everything the factory reads is in `config/`; nothing there is written by the factory.

- **`config/projects.toml`** — the project registry. The single place a stack fact lives in
  layer D. `[vault]` (path, write-allowlist, snapshot-exclude), `[defaults]`
  (worktree/attempt/disk/concurrency floors, `deny_network`, planning thresholds, redphase,
  gc floors, per-state timeouts), and one `[projects.<name>]` block per target repo:
  `team`, `path`, `remote`, `base_branch` (always `v2` for the harness repos — never `main`),
  `stack`, `template`, `build_sandbox` / `review_sandbox` names, `vault_mount`,
  `network_allow`, `sensitive_paths`, and per-project `env`. Adding a project is adding a
  block; the factory resolves the team from the ticket's identifier prefix.

- **`config/models.toml`** — model and effort routing. `[roles.*]` (planner, builder,
  reviewer, synthesiser, documenter), `[budget]` (`usd_per_run = 20.0`, `usd_warn_at =
  12.0`), and `[models.<slug>]` catalogue entries. Validation refuses to start if the
  reviewer shares the builder's model, if a role names a model that is not in the catalogue,
  or if the budget is invalid. `ultra` is never routable for the builder (it spawns work the
  control plane did not schedule).

- **`config/prices.toml`** — token→USD. A missing model records `usd = NULL`, never `0`.

- **`harness.config.json`** — this repo's own gates (`ruff check`, `ruff format --check`,
  `mypy`, `pytest`), so the factory is verified by the same layer-A Stop hook it drives.

## Sandboxes

- **`factory-build-<project>`** — the writer sandbox. Workspace bind-mounted at its real
  host path (host and VM address the same files), vault mounted `rw` (layer A's hooks write
  to `Project Learnings/**` and `_VAULT_INDEX.md` — the only writes a run may make; anything
  else is `blocked: vault-write-outside-allowlist`, with before/after snapshots kept).
- **`factory-review-<project>`** — the reviewer sandbox. Workspace mounted `:ro`, no vault,
  no skills store; `sandbox_mode="read-only"` set per invocation. The single-writer rule is
  enforced by the mount, not by a prompt.
- **Never `codex-*`.** That namespace is James's interactive `csbx`; the factory attaching to
  it would put an unattended writer inside a live human session.
- **`deny_network`** is applied to every factory sandbox at creation, including the model
  gateway's endpoint (`mcp.linear.app`) — the compensating control for the one secret `sbx`
  uploads unconditionally.
- **The python-harness build sandbox is `factory-build-python-harness-2`.** The unsuffixed
  name carries an invisible `github` credential binding (`sbx secret ls` cannot see it, `sbx
  secret rm` cannot clear it); the suffixed name comes up clean. Do not "tidy" it back —
  `preflight:no-secrets-in-vm` blocks every run under the old name, correctly.
- **`frontend-harness` carries `requires_clone = true`.** A bind-mounted `node_modules` is
  not loadable in the Linux VM and a sandbox `pnpm install` would overwrite the host's; the
  factory clones the repo into the sandbox VM-side instead.

## Crash safety and recovery

Long work runs **detached inside the sandbox** and reports through the filesystem, so the
control plane can die at any moment and nothing is lost:

- `exec_detached` starts `sbx exec -d` with `start_new_session=True`, so the holder is
  reparented to pid 1 and survives the factory exiting, crashing, or taking a Ctrl-C. **The
  factory process is not in the run's TCB; the machine is.** A reboot, logout, Docker
  Desktop quitting, or `sbx stop` ends the run — and the attempt directory says so.
- The attempt directory is the protocol: `heartbeat` (liveness), `exit` (terminal, written
  by `mv` from a temp file so its appearance is atomic), `events.jsonl`, `last-message.json`,
  `gates.json`, `review-*.json`.
- Every external write goes through the **effects ledger** in `store.py`, committed *before*
  the call and reconciled rather than retried on resume — so a crash between the ledger
  insert and the Linear comment writes the comment exactly once, never twice.
- Recovery has three cases: an `orphaned` attempt (sandbox stopped, heartbeat stale, no
  `exit`) resumes by session id; a `failed` agent run restarts with backoff; the attempt
  budget exhausted goes to `failed` for James to re-authorise. A third consecutive gate/review
  failure rewinds to `planning` and writes a fresh plan rather than re-running the same prompt.

## Artifacts and state

`state/`, `artifacts/` and `logs/` live under this directory and are gitignored. The database
is rebuildable from Linear, git, and the artifacts, which is why losing it is an
inconvenience rather than an incident.

```
state/factory.db                          SQLite, WAL — runs, transitions, effects, attempts, checks, costs
artifacts/<TEAM-NUM>/<attempt>/            copied out of the worktree at each terminal transition
logs/factory-<date>.jsonl                  structured control-plane log
```

Each artifact directory carries a `manifest.json` recording every file with its sha256, so a
run is auditable without re-running anything. Planted secrets (`ghp_…`, `sk-…`,
`-----BEGIN`) quarantine the artifact and fail the run.

## Operating the factory

```sh
uv run factory doctor                       # green before you trust a run
uv run factory status --all                 # the board
uv run factory runtimes                     # which sandboxes are live
uv run factory logs BAC-6 --follow          # watch a run
launchctl load ops/com.jchen.factory.plist   # start the poller (every 60 s)
launchctl unload ops/com.jchen.factory.plist# stop it
uv run factory gc --dry-run                 # what cleanup would touch
```

When a run is `blocked`, read `factory status <ticket> --evidence` for the reason and the
evidence path, fix the cause, then `factory resume <ticket>` (or unblock in Linear and let
the next tick claim it). When a PR is bad, `factory cancel <ticket>`. When it merged,
`factory complete <ticket>`. `docs/runbook.md` answers the three operator questions: it is
stuck, what do I do; it opened a bad PR, what do I do; I want it to stop right now, what do
I type.

## Working in this repository

```sh
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Those four are this repo's Definition of Done, declared in `harness.config.json` and enforced
by the same layer-A Stop hook the factory drives. The repo has no CI — the Stop hook is the
only enforcer — so a green local run is the bar, not a green PR check.

Branches are `<type>/<slug>`; there is no `v2`/`main` split here, because nothing in this
repository is generated except the vendored tree. `AGENTS.md` is canonical and `CLAUDE.md`
is a one-line pointer to it.