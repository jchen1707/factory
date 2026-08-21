# Handoff — finishing Phase 1's validation

**Written 2026-08-21, at the end of the session that performed the first real
`factory run`.** Read `AGENTS.md` first; it carries the ownership rule and the standing
boundaries, and none of them are relaxed here. This file is the delta: what the first run
proved, what it did not, and what is left before Phase 1 can be called validated.

**Do not start Phase 2.** The layer-A `gate_report.mjs` PR gates it and James merges that.

---

## State of play

| | |
| --- | --- |
| Branch | `fix/blocked-tracker-writes-and-secret-scope`, one commit on top of `e011ee4` |
| Gates | four green — ruff, ruff format, mypy, 203 tests |
| `factory doctor` | green, all 17 checks |
| BAC-4 | **In Progress**, blocked, one factory comment, run `03cda9bfebe644d7` |
| Build sandbox | **removed** — it carried a secret fixed at creation; the next run makes a clean one |

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

Three defects the run exposed, all fixed, all with tests proven to fail against the
unfixed code. Full reasoning is in the commit message and in the code comments.

1. **`_block` made no Linear write** though §13.1's table specifies one. New
   `src/factory/steps/block.py` comments the reason and evidence path and adds
   `needs-info`, both through the effects ledger.
2. **`factory cancel` did not restore the tracker**, so `cmd_run`'s advice to cancel and
   re-run was false. `cli._restore_tracker_for_rerun` now does, but only from the state
   the factory itself set.
3. **`TRANSITIONS` had no `-> blocked` edge** from `approved` or `sandbox_creating`.
   `blocked` is now derived onto every non-terminal state, like `cancelled`.

Plus the §8.7 secret assertion, narrowed from "the array is empty" to
`policy.capability_secrets()`. P0-5 assumed a factory-created sandbox inherits no secrets;
it inherited `github` and `mcpgateway`. James re-scoped `github` per-sandbox by hand.
`mcpgateway` cannot be re-scoped, so it is excluded by name with
`deny_network = ["mcp.linear.app"]` as the compensating control.

---

## The remaining work, in order

### 1. Unstick BAC-4 and re-run it

The ticket is In Progress; eligibility condition 2 requires `Todo`. There is no `resume`
in Phase 1 — that is Phase 4's, with `recovery.py`. Cancel-and-rerun is the contract:

```sh
cd /Users/james/factory
uv run factory doctor
uv run factory cancel BAC-4      # moves it back to Todo, strips needs-info
uv run factory run BAC-4 --dry-run
uv run factory run BAC-4
```

`cancel` exercises fix 2 against real Linear for the first time — watch that it prints
`moved BAC-4 back to Todo`. If it prints `left BAC-4 at '…'`, the guard fired and
something moved the ticket by hand.

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

**1. The in-image Codex is a different binary from the one P0-7 measured.**
`docs/discovery/codex-events.md` was captured on the host's `codex-cli 0.147.0`.
`docker/sandbox-templates:codex-docker` carries **0.146.0** (`p0-3-toolchain.md`). Every
event-shape fact `agent/codex.py::parse_events` relies on — `thread.started`,
`turn.completed.usage`, `item.completed` — was measured on the *other* binary. This is
the single most likely cause of a confusing failure past preflight. If `parse_events`
returns an empty or partial transcript, check the in-image version **first**:

```sh
sbx exec factory-build-python-harness /bin/sh -lc 'codex --version'
```

Re-capture the fixture from inside the VM, or pin the in-image CLI. Do not "fix" the
parser to match whatever comes out without recording which binary produced it.

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

- Whether to merge `fix/blocked-tracker-writes-and-secret-scope` into `main` before the
  re-run, or run from the branch. The fixes are not needed to re-run, only to record a
  block properly if one happens again — so running from the branch is fine.
- Whether the one Linear write the factory now makes that §13.1's table does not list —
  `cancel` moving a ticket back to `Todo` — should stay. The alternative was deleting
  §19's rollback promise instead. It is argued in `_restore_tracker_for_rerun`'s
  docstring.
