# factory

Layer D — the control plane.

The factory takes one Linear ticket that a human approved, drives it through a sandboxed
agent under this system's own gates, and stops at the boundary a human owns. It is not
clever. It is a machine that survives a crash, resumes the ticket it was on, never repeats
a side effect, and produces evidence you can audit without re-running anything.

`SOFTWARE-FACTORY-PLAN.md` is the specification. `docs/discovery/` is the measured evidence
Phase 0 produced, and where a measurement contradicts the plan the discovery file wins.
`AGENTS.md` is the instruction file for anyone — human or agent — working in this repo.

## Where this sits

| Layer | Repository | What it owns |
| --- | --- | --- |
| A | [`harness`](https://github.com/jchen1707/harness) | stack-neutral gates, review frames, hooks |
| B | `python-harness`, `frontend-harness` | one stack's config and checklists |
| C | a product repository | the product |
| **D** | **this repository** | **what runs, when, in what order, and what happens when it dies** |

This repository consumes all three and is consumed by none of them. It holds no gate
command and no review prompt: it reads them from the target repo's `harness.config.json`
and from the vendored layer-A tree.

## Status — Phase 1

`factory run <TICKET>` drives one ticket from `approved` to `implementing` and stops at
`verifying`. A human types the command; there is no poller, no PR and no console yet.

```sh
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
uv run factory doctor                    # registry, sbx, codex, gh, disk, db, keychain
uv run factory run BAC-4 --dry-run       # prints every command, executes none
uv run factory run BAC-4
uv run factory status BAC-4
uv run factory cancel BAC-4              # rollback: worktree removed, lease released
```

What is deliberately absent, and which phase brings it: verification and the gate report
(Phase 2), review and the PR (Phase 3), the poller, `gc` and the console (Phase 4).

## Runtime state

`state/`, `artifacts/` and `logs/` live under this directory and are gitignored. The
database is rebuildable from Linear, git and the artifacts, which is why losing it is an
inconvenience rather than an incident.

Never create `~/.factory/`. `sbx skills import` scans it, and it belongs to Factory.ai's
Droid rather than to this factory.
