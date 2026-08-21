# Handoff — finishing Phase 1's validation

**Written 2026-08-21, at the end of the session that performed the first real
`factory run`.** Read `AGENTS.md` first; it carries the ownership rule and the standing
boundaries, and none of them are relaxed here. This file is the delta: what the first run
proved, what it did not, and what is left before Phase 1 can be called validated.

**Do not start Phase 2.** The layer-A `gate_report.mjs` PR gates it and James merges that.

---

## State of play

**Updated 2026-08-21 after the second real run.** The sections below are current; §
"What the first run proved" is kept as the record of the run before it.

| | |
| --- | --- |
| Branch | `fix/blocked-tracker-writes-and-secret-scope`, three commits on top of `e011ee4` |
| Gates | four green — ruff, ruff format, mypy, 215 tests |
| `factory doctor` | green, all 17 checks |
| BAC-4 | **In Progress**, blocked, two runs: `03cda9bfebe644d7` cancelled, `10cd0483a64c48c3` blocked |
| Build sandbox | recreated by run 2, **stopped**; still carries `github` (see below) |
| Database | migrated to `SCHEMA_VERSION` 2; both runs' ledgers intact |

## The second run, and where Phase 1 now stands

`factory run BAC-4` was re-run after `factory cancel BAC-4`. It reached the same place as
the first:

```
approved → claimed → context_loaded → sandbox_creating → BLOCKED: enforcement-disabled
```

**Still nothing past the preflight has ever executed.** The list under "Not proved" below
is unchanged in full.

What the second run did prove, all of it new:

- **`cancel` + `run` now works.** It did not before; see defect 4.
- **The 1 → 2 migration is safe on the real database** — both runs' transitions, effects
  and checks survived it, and `PRAGMA foreign_key_check` is clean.
- **A re-run gets a fresh ledger.** Run 2 performed its own two claim writes rather than
  reconciling run 1's away, which is the property that makes a re-run a real re-run.
- **Defect 1's block comment fired against real Linear** — `comment:blocked:...` confirmed,
  a second factory comment on BAC-4.
- **The `needs-info` half of it did not.** See defect 5.

### The two things that now block Phase 1, both James's

1. **The name `factory-build-python-harness` carries an invisible `github` binding.**
   Re-scoping the secret worked; this one name did not stop picking it up. The factory's
   exact `sbx create` argv under any other name gives a clean sandbox — full table in
   `docs/discovery/p1-1-in-image-codex.md`. `sbx secret ls`/`rm` cannot see or clear the
   binding, so the cheapest unblock is to stop reusing the name. The preflight is right
   and `policy.capability_secrets()` must not be widened.
2. **`needs-info` and `ready-for-agent` are in the same Linear label group**, so defect 5
   has no correct fix the factory can choose by itself.
   **Resolved 2026-08-21** — James moved `needs-info` into a new group, `signal`. The
   label write should now succeed; it has not yet been re-run against real Linear.

---

## What the first run proved, and what it did not

`factory run BAC-4` reached `sandbox_creating` and stopped:

```
approved → claimed → context_loaded → sandbox_creating → BLOCKED: enforcement-disabled
```

**Proved.** Eligibility (10/10), the claim's two Linear writes and their ledger rows,
context staging (four files, 21 KB parent spec verbatim), sandbox creation, and four of
five preflight checks — including the protect-paths canary producing a *real* refusal on
`uv.lock`. The refusal itself is the headline: preflight caught a credential the host had
put in the VM and stopped before spending a model call.

**Not proved — none of this has ever executed.** Everything past preflight:

- `sbx exec -d` and the detached wrapper script
- the heartbeat / `exit` file protocol
- `codex exec --json` inside the VM, and `parse_events` against its output
- `last-message.json` and its schema validation
- the vault before/after snapshot and the write allowlist
- `implementing → verifying`

That list is the actual remaining scope of Phase 1 validation.

---

## What is on the branch

Five defects the two runs exposed. Four are fixed, each with tests proven to fail
against the unfixed code; the fifth needs a decision before it can be. Full reasoning is
in the commit messages and in the code comments.

1. **`_block` made no Linear write** though §13.1's table specifies one. New
   `src/factory/steps/block.py` comments the reason and evidence path and adds
   `needs-info`, both through the effects ledger.
2. **`factory cancel` did not restore the tracker**, so `cmd_run`'s advice to cancel and
   re-run was false. `cli._restore_tracker_for_rerun` now does, but only from the state
   the factory itself set.
3. **`TRANSITIONS` had no `-> blocked` edge** from `approved` or `sandbox_creating`.
   `blocked` is now derived onto every non-terminal state, like `cancelled`.
4. **`cancel` could not actually make a ticket re-runnable**, so the contract fix 2 was
   written to serve did not hold end to end. `runs.linear_id` was `UNIQUE`, meaning one
   run row per ticket *for all time*: `cancel` cleaned the worktree, the lease and the
   tracker, and then `run` refused at `BAC-4 is already at cancelled (attempt 0)`. Fix 2's
   tests called `_restore_tracker_for_rerun` directly and only asserted the two Linear
   halves, which is exactly the gap the defect lived in.

   §7.2 wants the column to prevent a *race* — "a second poller or a second tick cannot
   create a second run" — not to bind a ticket to one row forever. `SCHEMA_VERSION` 2
   replaces the constraint with a partial unique index over the live states only
   (`store._LIVE_RUN_INDEX`), so a terminal run is *succeeded* by a fresh one rather than
   resurrected. That keeps `cancelled` terminal (§5.1), keeps the abandoned run's ledger
   intact under its own id, and starts the successor at attempt 0 so its attempt
   directory is `run/1` as §19 expects. `blocked` is deliberately live, so a blocked run
   still holds its ticket until a human cancels it.

   The migration rebuilds `runs` with foreign keys off and `foreign_key_check` after —
   dropping the table with enforcement on would have taken the audit history with it.
   It has been run on the real `state/factory.db`; both runs' rows survived.
5. **`needs-info` cannot be applied while `ready-for-agent` is on the ticket.** *Not
   fixed — it needs a decision.* Defect 1's comment landed, but the label half came back
   400 from Linear:

   > The label 'needs-info' is in the same group as 'ready-for-agent'. Only one label in
   > a group can be applied to an issue.

   Two consequences, and the second is the serious one:

   - The `label:needs-info` effect is stuck at `intended` and can never be confirmed,
     which is a row in the ledger that will never reconcile.
   - **Eligibility condition 7 does not stop a blocked ticket being re-claimed** in this
     workspace. `block.py`'s "the label stops the machine" is false here: BAC-4 is
     blocked and still carries `ready-for-agent` with no `needs-info`. What actually
     stopped the re-claim is the run row — a `blocked` run is live under
     `_LIVE_RUN_INDEX`, so the ticket is held locally. That belt-and-braces is the only
     brace currently doing anything.

   The obvious code fix — swap `ready-for-agent` for `needs-info`, which is what a label
   group *means* — makes the factory remove James's signature label, and then `cancel`
   would have to put it back. `AGENTS.md` reserves applying that label to James, so the
   factory re-applying it is a boundary change, not a bug fix. Left for James; the
   alternative is moving `needs-info` out of that group in Linear, which costs no code.

Plus the §8.7 secret assertion, narrowed from "the array is empty" to
`policy.capability_secrets()`. P0-5 assumed a factory-created sandbox inherits no secrets;
it inherited `github` and `mcpgateway`. James re-scoped `github` per-sandbox by hand.
`mcpgateway` cannot be re-scoped, so it is excluded by name with
`deny_network = ["mcp.linear.app"]` as the compensating control.

---

## The remaining work, in order

### 0. First: make a factory sandbox that has no `github` secret

**Nothing below can run until this is done.** The cause is isolated: the *name*
`factory-build-python-harness` picks up a `github` binding that `sbx secret ls` and
`sbx secret rm` cannot see, while the factory's exact `sbx create` argv under any other
name produces a clean sandbox. The evidence table is in
`docs/discovery/p1-1-in-image-codex.md`.

Two ways out, and the first needs no credential work at all:

- **Give the build sandbox a name that has never been used** — still `factory-build-*`,
  which `assert_factory_sandbox` and `AGENTS.md` both require. One value in the project
  registry. Worth confirming first by creating `factory-build-frontend-harness` (a
  `factory-build-*` name with no history) and checking `sbx inspect` shows `mcpgateway`
  alone; that also settles whether anything about the naming scheme is at fault.
- **Purge the stale binding.** `sbx reset` would, but it clears *all* sandbox state
  including James's `csbx` ones. Otherwise it is a question for Docker: a credential
  binding surviving `sbx rm` and invisible to `sbx secret ls` looks like a v0.38.0 bug.

Either way `sbx rm factory-build-python-harness` first — the secret set is fixed at
creation, so an existing sandbox stays contaminated. (It was removed at the end of the
2026-08-21 session; `sbx ls` should show only the three `codex-*` sandboxes.)

**Do not** widen `policy.capability_secrets()` to get past this. That exclusion list holds
`GATEWAY_CREDENTIAL` alone and `AGENTS.md` calls widening it a boundary change.

### 1. Unstick BAC-4 and re-run it

The ticket is In Progress; eligibility condition 2 requires `Todo`. There is no `resume`
in Phase 1 — that is Phase 4's, with `recovery.py`. Cancel-and-rerun is the contract, and
it now works both halves:

```sh
cd /Users/james/factory
uv run factory doctor
uv run factory cancel BAC-4      # moves it back to Todo, strips needs-info
uv run factory run BAC-4 --dry-run
uv run factory run BAC-4
```

Two things to watch, neither of which has been seen working yet:

- `cancel` should print `moved BAC-4 back to Todo`. Both times so far it printed
  `left BAC-4 at 'Todo' (not the state the factory set)` — the guard firing because the
  ticket was already there. **Fix 2's actual move has still never run against real
  Linear**, only against the fake.
- It should also print `removed the 'needs-info' label`. It will not until defect 5 is
  settled, because the label never gets applied in the first place.

### 2. Expect the run to be long, and drive it from the background

`implementing` has a 5400 s timeout and the model dominates. Run it with
`run_in_background` and watch `logs/factory-<date>.jsonl` rather than blocking a
foreground call. The state to watch for is `implementing → verifying`; the run **stops**
at `verifying` by design — Phase 1 contains no verification, no review, no push, no PR.
Do not add them.

### 3. Read the evidence properly

```sh
uv run factory status BAC-4
sqlite3 state/factory.db 'select * from checks; select * from effects; select * from transitions;'
ls /Users/james/python-harness/.factory/worktrees/BAC-4/.factory/run/1/
```

The attempt directory should hold `events.jsonl`, `stderr.log`, `heartbeat`, `exit` = 0,
and a schema-valid `last-message.json`. §19's expected output is exactly that.

---

## Hazards for the next run, in the order they will bite

**1. The in-image Codex is a different binary from the one P0-7 measured — now
confirmed by running it.** `sbx exec factory-build-python-harness /bin/sh -lc 'codex
--version'` returns **`codex-cli 0.146.0`**; `docs/discovery/codex-events.md`, which
`agent/codex.py::parse_events` is pinned to, was captured on the host's **0.147.0**. Every
event-shape fact the parser relies on — `thread.started`, `turn.completed.usage`,
`item.completed` — was measured on the other binary, and nothing has yet parsed 0.146.0's
output because no run has reached `implementing`.

This remains the single most likely cause of a confusing failure past preflight. If
`parse_events` returns an empty or partial transcript, that is why. Re-capture the fixture
from inside the VM, or pin the in-image CLI. Do not "fix" the parser to match whatever
comes out without recording which binary produced it.

**2. The deny rule is installed but unmeasured.** `--deny-network mcp.linear.app` reaches
`sbx create` and `sbx policy ls <name>` should show it. Whether it actually stops gateway
traffic has not been tested. `policy.py` records the caveat that `sbx inspect --json`
reports `mcp_gateway: true` but **not** the static MCP set, so the factory cannot verify
the set is empty from outside. If you can measure the deny working, do — and write it
into `docs/discovery/`.

**3. Sandbox secrets are fixed at creation.** Re-scoping a secret does not retire a
sandbox that already has it. If preflight fails on `no-secrets-in-vm` again, the fix is
`sbx rm factory-build-python-harness` and let the next run recreate it — never a
relaxation of the check.

**4. `origin/feat/BAC-4-application-skeleton` exists on the remote.** Unmerged prior work
under a *different* branch name from the one the factory will create. It is a free oracle
to diff against, not a collision.

**5. Never touch a `codex-*` sandbox.** Those are James's live `csbx` sessions, and they
now hold the per-sandbox `github` secrets. `assert_factory_sandbox` enforces it at the
adapter; do not route around it.

---

## Things that are explicitly not in scope

- **Phase 2 and beyond.** It waits on a layer-A PR James merges.
- **`resume`.** Phase 4, with `recovery.py`.
- **Merging this branch.** James's, like every merge.
- **Anything that adds verification, review, push or PR to Phase 1.**

## What to ask James

**The two blocking ones, first:**

- **How a factory sandbox gets created without the `github` secret.** This is the
  hard stop; §0 of the remaining work has the detail. It is a credential-scoping
  decision, which `AGENTS.md` reserves to him, and `--profile` is the one creation-time
  lever nobody has looked at yet.
- **What to do about the `needs-info` / `ready-for-agent` label group** (defect 5).
  Moving `needs-info` to its own group in Linear costs no code and keeps the factory out
  of James's signature label. The code alternative makes the factory remove and re-apply
  that label, which is a boundary change.

**And the standing ones:**

- Whether to merge `fix/blocked-tracker-writes-and-secret-scope` into `main` before the
  re-run, or run from the branch. Running from the branch is fine — but note the branch
  now carries the schema migration, so a `main` checkout will open a v1 database and a
  branch checkout a v2 one. The migration is forward-only.
- Whether the one Linear write the factory now makes that §13.1's table does not list —
  `cancel` moving a ticket back to `Todo` — should stay. The alternative was deleting
  §19's rollback promise instead. It is argued in `_restore_tracker_for_rerun`'s
  docstring.
