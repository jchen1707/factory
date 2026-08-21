# Defect 6 — `cancel` cannot clean what a run never recorded

**Status: fixed 2026-08-21.** Found during Phase 1's validation and worked around by
hand twice before that. The rest of this note is the design and the evidence it was
built from; what shipped is below.

## What shipped

`cmd_cancel` no longer reads the two fields. `_worktree_paths` derives the directory
from the ticket and the registry, and `_release_local_debris` does the work
(`src/factory/cli.py`), on top of four new primitives in `repo.py`:

| | |
| --- | --- |
| `orphan_worktree_dir` | a directory git does not know as a worktree |
| `holds_only_factory_scaffolding` | rule 1's refusal — nothing but `.factory/` inside |
| `local_branches_matching` | the remote scan's other half, on a word boundary |
| `reason_to_keep_branch` | rule 2's refusal — pushed, or carrying commits |

Three things came out differently from the sketch below, and all three are improvements
the writing did not see:

- **The archive step derives its paths too.** It was guarded by `if run.worktree:` like
  everything else, so an unrecorded worktree holding a half-finished attempt would have
  been removed with its evidence unarchived — the cover-up the archive exists to
  prevent. It now walks the same derived list.
- **A refusal is printed.** Both rules leave debris behind on purpose, and the next run
  fails on exactly that debris. Saying *left `<branch>` in place: it carries 1 commit
  beyond origin/v2* is what turns a mystifying `fatal:` into a decision someone already
  made.
- **The identifier match is `\bBAC-4\b`, not a substring.** `feat/BAC-40-…` contains
  `BAC-4`, and an empty unpushed branch is precisely the shape this deletes. Cancelling
  one ticket would have quietly deleted another's branch.
  `test_cancel_leaves_another_tickets_branch_alone` is that case.

Four tests in `tests/integration/test_pipeline.py`, each run against code that does not
have the rule it guards: the regression fails on the `fatal:` below, and removing either
safety rule deletes the human's commit-carrying branch and `rm -rf`s a directory holding
work. `run.branch`'s own deletion is unchanged — deleting the run's own unpushed branch
is §19's rollback contract and what makes a rerun possible.

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

## The test that had to fail first

The gap survived because nothing exercised cancel against a run that never recorded
anything. `test_cancel_puts_the_ticket_back_where_the_factory_found_it` covers the Linear
halves; `test_cancel_frees_the_ticket_locally_and_not_only_in_linear` covers the run row.
Neither creates debris.

Added, in `tests/integration/test_pipeline.py` beside those two:

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
