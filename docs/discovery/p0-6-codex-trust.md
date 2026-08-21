# P0-6 / P0-9 — Codex project trust and hook trust

Probed in a throwaway detached worktree at
`/Users/james/python-harness/.factory/worktrees/p0-6-probe`, against an **isolated copy** of
`CODEX_HOME` so the real `~/.codex/config.toml` was not mutated by the experiment.
The worktree has been removed and `git worktree prune` run.

Baseline: `~/.codex/config.toml` sets `approval_policy = 'never'` and
`sandbox_mode = 'danger-full-access'` globally, and `[projects]` has **no** entry for the
worktree path.

## §8.6 item 2 — answered: an untrusted project is **not** refused

The run proceeded, wrote to `README.md`, and exited 0. No prompt, no warning, no refusal.

→ The factory does **not** need `-c projects.<escaped-path>.trust_level="trusted"`.
Delete that contingency from §8.6.

## The hazard is real, and it is worse than "hooks do not fire"

Same worktree, prompt: append a line to `uv.lock` — a **protected path**
(`harness.config.json` → `hooks.protected`, *"regenerate with `uv lock`, never hand-edit"*).

| Invocation | Result |
| --- | --- |
| `codex exec` | **`uv.lock` was edited.** Exit 0. Nothing fired, nothing warned |
| `codex exec --dangerously-bypass-hook-trust` | **Blocked** by `protect_paths.mjs`, with the config's own `why` string. Exit 0; the agent reported the block and stopped |

So at an untrusted path the enforcement layer is not merely inert — it is **invisible**. A
run that violates a protected path looks exactly like a run that respected it. This is the
same failure shape as `verify.mjs` returning 0 on a spawn error (§2.3), reached by a
different route.

**`--dangerously-bypass-hook-trust` is therefore mandatory on every factory invocation**, as
§8.6 item 1 already prescribes — this measurement upgrades it from prudent to load-bearing.
Pair it with `vendor_sync.py check` against the worktree in the same step, which is what
makes the flag safe rather than reckless.

Two parser consequences, both covered in `codex-events.md`:

- The flag emits two `item.completed` events with `type: "error"` **before** `turn.started`
  on a run that then succeeds. `type: error` must not be read as failure.
- The hook denial itself never reaches the JSON stream. It is a stderr line from
  `codex_core::tools::router`. Capture stderr or lose the evidence.

## P0-9 — the stale trust entry, re-confirmed

`~/.codex/config.toml` `[hooks.state]` keys, verbatim:

```
/Users/james/python-harness/.codex/hooks.json:{post_tool_use,pre_tool_use×2,session_end,stop}:0:0
/Users/james/frontend-development-harness/.codex/hooks.json:{pre_tool_use,post_tool_use,stop}:0:0
```

`/Users/james/frontend-harness` — the real clone — has **no** entry, and neither does
`[projects]`. Combined with the measurement above, this is not a cosmetic defect:
**James's own interactive Codex sessions in `frontend-harness` run today with
`protect_paths`, `format_edited` and `verify` silently disabled.**

It shows up in `sbx ls` too: the sandbox `codex-frontend-development-harness` is *running*
with workspace `/Users/james/frontend-development-harness (missing)`.

Fix (human only — **an agent must never write to `~/.codex/config.toml`**): open an
interactive `codex` session in `/Users/james/frontend-harness` and re-approve the hooks, then
delete the stale keys by hand.

> ✅ **Done, 2026-08-20.** The config now holds **four** `[hooks.state]` keys for
> `/Users/james/frontend-harness/.codex/hooks.json` — `pre_tool_use`, `post_tool_use`,
> `session_end`, `stop` — one more than the stale set, which had no `session_end`, plus a
> `[projects]` entry. Every `frontend-development-harness` reference is gone and the sandbox
> of that name is removed.
>
> **Verified by canary, not by reading the file.** `codex exec` asked to append a line to
> `pnpm-lock.yaml` was blocked by `protect_paths.mjs`: *"Refusing to edit pnpm-lock.yaml -
> regenerate with `pnpm install`, never hand-edit."* Repo clean afterwards, no new
> `[projects]` stanza. Note there is no `codex hooks trust` subcommand and `codex doctor`
> reports nothing about trust — the canary is the only real verification, and the factory's
> `doctor` should adopt it.

## Litter this discovery created, for James to remove

The P0-7 capture ran `codex exec` in a scratch repo, and **Codex auto-wrote a trust entry**
for it into the real config:

```toml
[projects."/private/tmp/claude-501/-Users-james-factory/15aa2ee0-61bd-4c79-a1f6-488d781b29e3/scratchpad/p0-7-scratch"]
trust_level = "trusted"
```

Left in place deliberately, because the rule against an agent editing that file has no
exception for tidying up after itself. ✅ **James deleted it on 2026-08-20.**

This is also a **new finding for the design**: with `approval_policy = 'never'`,
`codex exec` silently appends a `[projects]` stanza for a directory it has not seen before.
One per worktree, one worktree per ticket — `~/.codex/config.toml` would grow without bound
under an unattended factory. Interestingly, the *worktree* runs did **not** add an entry
(the parent repo is already trusted), so the growth is bounded to genuinely new roots — but
Phase 1 should assert the file's stanza count is unchanged after a run, and fail loudly if
it is not.
