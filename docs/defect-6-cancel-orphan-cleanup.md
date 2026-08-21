# Defect 6 — `cancel` cannot clean what a run never recorded

**Status: unfixed, ready to pick up.** Found 2026-08-21 during Phase 1's validation and
worked around by hand twice. Everything below is design and evidence; no code has been
written. `AGENTS.md` → "Waiting on James" item 2 points here.

## The symptom

Two consecutive runs of `factory run BAC-4` died at the worktree step, each on debris the
previous one had left:

```
factory: git worktree add -b feat/BAC-4-application-skeleton-settings-structured \
  /Users/james/python-harness/.factory/worktrees/BAC-4 origin/v2 failed:
fatal: '/Users/james/python-harness/.factory/worktrees/BAC-4' already exists

fatal: a branch named 'feat/BAC-4-application-skeleton-settings-structured' already exists
```

`factory cancel BAC-4` had been run in between, reported success, and cleaned neither.
Both had to be cleared by hand — `rm -rf` on the directory and `git branch -D` — before
the run that finally validated Phase 1 could start.

## Root cause

`cmd_cancel` guards its cleanup with fields the run may never have written
(`src/factory/cli.py:357` and `:360`):

```python
if run.worktree:
    repo.remove_worktree(...)
if run.branch:
    repo.delete_local_branch(...)
```

`worktree.py` writes both **after** `repo.add_worktree` returns
(`src/factory/steps/worktree.py:61-62`). So the window in which the command can fail is
exactly the window in which cancel is blind to its own debris. The rollback is least
capable at the only moment it is needed.

There is a second, quieter half. The orphan directory was **not a registered worktree** —
`git worktree list` never showed it — so `repo.remove_worktree` would have returned early
anyway: it checks `worktree_exists` first (`src/factory/repo.py:185`) and prunes. Passing
the right path would not have been enough.

## The fix: derive, do not read

Both values are recoverable from the ticket alone.

**Worktree path** is already deterministic and needs no network:
`project.worktree_path(registry.defaults.worktree_subdir, ticket)`
(`src/factory/registry.py:80`).

**Branch** should *not* be rebuilt with `plan_branch`/`repo.branch_name` — that needs the
Linear issue, and the title it slugifies may have changed since the run. Scan local
branches for the identifier instead, mirroring `repo.remote_branches_matching`
(`src/factory/repo.py:95`), which already does this for the remote side.

Suggested shape — two helpers in `repo.py`, called by `cmd_cancel` **unconditionally**,
alongside (not instead of) the existing `if run.worktree:` archive step, which genuinely
does need the recorded value:

```python
def orphan_worktree_dir(repo: Path, path: Path) -> Path | None:
    """An existing directory at `path` that git does not know as a worktree."""

def stale_local_branches_for(repo: Path, identifier: str, base_ref: str) -> list[str]:
    """Local branches naming the identifier that are safe to delete."""
```

## Two safety rules, and they are the whole design

Cancel must never destroy work, so each helper refuses unless it is certain:

1. **Worktree.** If git knows the path, `repo.remove_worktree` as today. If git does not
   know it but the directory exists, remove it **only** when it holds nothing but the
   factory's own `.factory` scaffolding. Never `rm -rf` an arbitrary tree — that is F15,
   which the existing code already respects. The real orphan contained exactly
   `.factory/run/1/` and no files.
2. **Branch.** Keep the current refusal on anything with a remote counterpart
   (`repo.delete_local_branch`, `src/factory/repo.py:207`) — a pushed branch is visible to
   other people. Add a second condition: delete only when the branch has **no commits
   beyond `base_ref`**. An orphan from a failed `worktree add` has zero. A branch carrying
   the agent's commits is not cancel's to remove silently, and the run that produced it
   already archived its attempt directory.

Rule 2's second half is the one that matters. Without it this fix turns a rollback gap
into a way to lose an implementation.

## The test that has to fail first

The gap survived because nothing exercised cancel against a run that never recorded
anything. `test_cancel_puts_the_ticket_back_where_the_factory_found_it` covers the Linear
halves; `test_cancel_frees_the_ticket_locally_and_not_only_in_linear` covers the run row.
Neither creates debris.

Add, in `tests/integration/test_pipeline.py` beside those two:

- Create the worktree directory and the branch by hand, leave `run.worktree` and
  `run.branch` empty, run `cmd_cancel`, then assert `repo.add_worktree` **succeeds**.
  Against today's code it raises the `fatal:` above. That is the regression.
- A branch with a commit beyond `base_ref` survives cancel. Guards rule 2.
- A worktree directory holding a file the factory did not put there survives cancel.
  Guards rule 1.

## Out of scope

Do not make `add_worktree` "clean up and retry" — a step that erases state to get past its
own precondition is how an unattended writer destroys work. The named `Blocked` it already
raises is correct; the cleanup belongs in the rollback, which is what a human ran.
