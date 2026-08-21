# P0-11 — intake contract dry run

`scripts/intake_dry_run.py` lists every Linear issue labelled `ready-for-agent` and
evaluates §7.1's nine conditions. **It writes nothing** — no Linear mutation, no git, no PR.
Run with the existing `claude-mcp-linear-fro` key because `factory-linear` does not exist
yet (`p0-8-linear.md`).

```sh
python3 docs/discovery/scripts/intake_dry_run.py --service factory-linear \
  --repo BAC=/Users/james/python-harness --repo FRO=/Users/james/frontend-harness
```

## Result — 14 labelled, 3 eligible, all three on the frontend

| Issue | Verdict | Failing conditions |
| --- | --- | --- |
| **FRO-7** Filter your Projects by status, including archived | **ELIGIBLE** | — |
| **FRO-6** Search your Projects by name | **ELIGIBLE** | — |
| **FRO-5** See your Projects at /projects | **ELIGIBLE** | — |
| FRO-8 raw ZodError escapes instead of ValidationError | not eligible | 4, 5 |
| FRO-1 Build the first product screen | not eligible | 4, 5 |
| BAC-1 … BAC-9 (nine issues) | not eligible | **2 — state is `Canceled`** (BAC-1, BAC-2 also fail 4, 5; BAC-2 also 6) |

## Which conditions actually bite

| # | Condition | Times it failed |
| --- | --- | --- |
| 2 | state is `Todo` | **9** |
| 4 | has a parent issue | 4 |
| 5 | parent description ≥ 200 chars | 4 |
| 6 | acceptance-criteria section | 1 |
| 1, 3, 7, 8, 9 | — | 0 |

Conditions 4 and 5 always fail together here, because an issue with no parent trivially has
no parent description. They are not independent signals in practice.

## Three findings

### 0. Resolved the same day — BAC-4 was re-opened

At James's instruction, `BAC-4 — Application skeleton: Settings, structured logging, app
factory` was moved from `Canceled` to `Todo`. It now passes all nine conditions and is
Phase 1's target. The reasoning, and the stale `origin/feat/BAC-4-application-skeleton`
branch that Phase 1 has to handle, are in `SUMMARY.md`. Finding 1 below records the state
the dry run originally found.

### 1. The Python path had no eligible ticket

**All nine BAC issues are `Canceled`.** Phase 1's validation command, `factory run BAC-<n>`,
has nothing to run against. Either James re-opens a BAC ticket, or **Phase 1 should be
validated against `FRO-6` or `FRO-7` instead** — which changes Phase 1's expected output
(the worktree is under `frontend-harness`, the sandbox is `factory-build-frontend-harness`)
and pulls the `--clone`/`node_modules` problem from `p0-10-gate-timing.md` forward into
Phase 1 rather than Phase 2. That is a real scheduling decision, and it needs James.

### 2. FRO-5 is already built, and the contract does not notice

FRO-5 is `Todo`, labelled `ready-for-agent`, and passes all nine conditions — but the work
is merged: commit `0d08a3d` *"feat: FRO-5 projects list at /projects"*. Its PR (#9) was
**closed, not merged**, so condition 8 (`gh pr list --state open`) sees nothing, and a
merged-PR check would miss it too.

**The factory's first real run would re-implement work already in `main`.** Recommended
tenth condition, cheap and deterministic:

```sh
git -C "$REPO" log --oneline --grep="$IDENTIFIER" "origin/$BASE"   # must be empty
git -C "$REPO" branch -r --list "*$IDENTIFIER*"                    # must also be empty
```

The second line is not redundant. BAC-4 — Phase 1's chosen target — has a complete
implementation on `origin/feat/BAC-4-application-skeleton` that was **never merged**, so the
`git log` check against `origin/v2` finds nothing while the branch sits there ready to
collide with the name Phase 1 wants. Whether an unmerged branch should block or merely warn
is a judgement: it blocks the branch name, but the work itself is unlanded and may well be
worth redoing.

### 3. Condition 8's `gh pr list --search` is a fuzzy full-text match

`gh pr list --search "FRO-6 FRO-7"` returned *"fix: stop the second brain collecting
duplicate notes"* — a PR mentioning neither. As written, condition 8 will produce **false
`duplicate-pr` blocks**. Search the branch name instead (`--head "feat/$IDENTIFIER-"`), or
filter the results by exact identifier match on the title and branch after fetching them.
