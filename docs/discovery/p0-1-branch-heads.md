# P0-1 — branch heads

`git -C <repo> fetch origin --prune && git -C <repo> log --oneline -1 origin/v2 origin/main`

| Repo | `origin/v2` | `origin/main` | local `v2` vs `origin/v2` | worktree |
| --- | --- | --- | --- | --- |
| `harness` | `5cdb49f` | `2c10a0a` | 0 / 0 | clean |
| `python-harness` | `307c0f2` | `561ed94` | 0 / 0 | clean |
| `frontend-harness` | `4e62572` | `f66e82a` | 0 / 0 | clean |

**Verdict: matches §1.1 exactly.** No re-read needed.

## Incidental finding — `harness` carries the consumers as submodules

`git fetch` in `harness` printed:

```
Fetching submodule frontend-harness
Fetching submodule python-harness
```

§2.1 does not mention this. It matters for the factory in one place: a `git worktree add`
or a `vendor_sync.py` run against `harness` may traverse into the submodules, and the
factory's `repo.py` must decide explicitly whether to `--recurse-submodules`. Recorded as
an input to Phase 1, not a defect.
