# P1-3 — Phase 1's validating run

2026-08-21. `factory run BAC-4` drove one approved Linear ticket from `approved` to
`verifying` and stopped there, which is §19's expected outcome in full. This note is the
evidence, and it replaces `docs/handoff-phase-1-validation.md`.

## The run

```
approved → claimed → context_loaded → sandbox_creating → sandbox_ready
        → worktree_ready → implementing → verifying
```

Every hop `auto`, recorded in `transitions`. Ten of ten eligibility conditions passed.

| | |
| --- | --- |
| Run | `febdfc62e4554e7b`, attempt 1 |
| Sandbox | `factory-build-python-harness-2` (see P1-1 for the `-2`) |
| Session | `01a02287-f022-78d1-9b76-a5ab4eb43d9f` |
| Exit | 0, `outcome = implemented` |
| Tokens | 10,847,616 in (10,666,496 cached), 48,981 out; `usd` NULL per §18.3 |
| Result | 11 files, 4 test files, committed in the worktree as `a3d6b80` |

The attempt directory holds `events.jsonl` (251 KB), `stderr.log`, `sbx-exec.stderr`
(0 bytes), `heartbeat`, `exit` = 0, a schema-valid `last-message.json`, `prompt.md`,
`request.json`, `schema.json` and a `manifest.json` carrying a sha256 for each.

## Checks

| Check | Result |
| --- | --- |
| `preflight:toolchain` | pass |
| `preflight:harness-skip-verify-unset` | pass |
| `preflight:no-secrets-in-vm` | pass |
| `preflight:vendored-tree-intact` | pass |
| `preflight:protect-paths-refuses` | pass |
| `implement_result_schema` | pass |
| `vault_snapshot` | pass |
| `hook_denials` | **fail — and this is the good news** |

`hook_denials` records that layer A refused a tool call mid-run:

> Command blocked by PreToolUse hook: Refusing tool call - inline interpreters can read
> inherited secrets. Command: `uv run python -c 'import fastapi,httpx,…'`

That is the enforcement layer doing its job against a real agent, in a worktree, with
`--dangerously-bypass-hook-trust` in play — the exact configuration P0-6 warned was
silently inert without that flag. It is recorded as `fail` on purpose: a denial never
reaches the JSON stream, so without the check the evidence would show a clean run for a
turn that was blocked. It does not stop the run, because a refused write is enforcement
working rather than a defect.

## What this proved that nothing had proved before

Everything past the preflight. Before this run the list below had never executed once:

- `sbx exec -d` and the detached wrapper script
- the heartbeat / `exit` file protocol, including the atomic `mv`
- `codex exec --json` inside the VM, and `parse_events` against its output
- `last-message.json` and its schema validation
- the vault before/after snapshot and the write allowlist
- `implementing → verifying`

It also exercised, against the real world rather than a fake: the claim's two Linear
writes, `_block`'s comment, `cancel`'s tracker restore (`moved BAC-4 back to Todo`), the
worktree/branch cleanup, and the effects ledger performing a fresh run's writes rather
than reconciling a previous run's away.

## What is still open

Phase 1 is validated. These are not Phase 1 gaps, and two are James's:

1. **§4.2's durability does not hold** — `p1-2-detached-exec.md`. The host process is
   part of the run's TCB on `sbx` v0.38.0. Phase 4's daemon, `resume` and `recovery.py`
   all assume otherwise. **James's decision.**
2. **The poisoned sandbox name** — `p1-1-in-image-codex.md`. Worked around by renaming to
   `factory-build-python-harness-2`; the stale binding under the old name is still there
   and `sbx secret ls`/`rm` cannot see it. **James's, or Docker's.**
3. **`cancel` cannot clean what a run did not record** (defect 6). `cmd_cancel` guards
   cleanup with `if run.worktree:` / `if run.branch:`, but `worktree.py` sets those only
   *after* `git worktree add` returns — so a run that dies inside that window leaves an
   orphan directory and an empty branch that block every later run. Both were cleared by
   hand during this session. The fix is to derive the two paths from the ticket rather
   than the row, and to delete an empty worktree directory and a branch with no commits
   beyond the base ref. Unfixed.
4. **The `--deny-network mcp.linear.app` rule is installed but still unmeasured.** No run
   has generated gateway traffic to test it against.
5. **Nothing pins the in-image Codex CLI.** The parser and 0.146.0 agree today; a
   template bump can move that without warning.
