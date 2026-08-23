# Phase 5 — complete. What it cost, and what Phase 6 inherits

Rewritten 2026-08-23, after both exit tickets merged and reached `completed`. The previous
version is in git history.

**§19 Phase 5 in `SOFTWARE-FACTORY-PLAN.md` is the authority** and now carries the full
record: the three tests, the eight defects, and the two environment facts. This document is
the operator's view — what is true on this machine right now, and what to do next.

There is one plan file. If you find yourself reading a plan whose path is not
`/SOFTWARE-FACTORY-PLAN.md`, something has gone wrong.

---

## Where things stand

| | |
| --- | --- |
| `factory@main` | `4875ad7` — `#46`, `#47`, `#48` merged today |
| suite | **562 passed**, mypy clean on 41 files, ruff check + format clean |
| BAC-6 | **`completed`** — `python-harness#70` merged |
| FRO-11 | **`completed`** — `frontend-harness#48` merged |
| `test-project` | created, pushed, `stack = "monorepo"`; **no registry row, deliberately** |
| runs at `completed` | six: BAC-4, BAC-6, FRO-6, FRO-7, FRO-10, FRO-11 |

**Phase 5 is done.** Both exit tickets ran end to end and both were merged and recorded by
James. `awaiting_human -> completed` was taken for the first time today, twice.

---

## The three things a new session most needs to know

### 1. There are two OpenAI credentials and they are unrelated

This cost an hour and blocked every run of both stacks while the host looked healthy.

- `codex login` writes `~/.codex/auth.json` (4 KB, OAuth). This is what a **host** codex
  invocation uses.
- A **sandboxed** agent authenticates through `sbx`'s proxy, against a *globally stored*
  OAuth token: `sbx secret ls` shows `(global) service openai (oauth configured)`. The
  sandbox's own `~/.codex/auth.json` is a **40-byte stub** holding only `OPENAI_API_KEY`,
  and is not the credential in use.

When the stored token expires, every sandboxed run fails with `401 … token_expired` buried
in a per-axis transcript, and `codex login` does not fix it. The fix is:

```
sbx secret set openai --oauth      # answer y to the overwrite prompt
```

Two things measured today that save the next reader the same hour: **recreating the sandbox
does not help** (a freshly created reviewer failed identically — the credential was never in
the sandbox), and the four sandboxes' matching `auth.json` dates are a coincidence of
creation times, not the mechanism. Nothing in `factory doctor` checks the stored token.

### 2. The recovery paths are where the defects are, and only humans and real runs find them

Eight defects in Phase 5, seven on FRO-11 alone. Every one is on a path that runs only after
something else has already failed. The suite was green at 533, 541, 549 and 562 tests while
each was live — they are not test-suite-reachable, and no amount of unit testing would have
produced one.

Four of the eight (`#41`, `#42`, `#46`, `#48`) fire **only when a human takes an edge**.
Phase 4.5's four unattended runs surfaced none of them, because nobody typed anything.

The full table is in §19. The method that found all eight is at the bottom of this document
and is unchanged.

### 3. A registry row was deliberately not written for `test-project`

§10.2 resolves a project by ticket prefix alone, and two projects claiming one team is a
`RegistryError` that refuses to start the daemon. The Linear workspace has `BAC` and `FRO`
and no tier for a third team, so there is no prefix left for a product repository.

A row with an invented key would parse, match nothing, and read exactly like a working row.
That is the same class of inert configuration `#47` spent a whole PR eliminating, so nothing
was added. §19 records the shape of the change that would be needed if the factory is ever to
drive a layer-C product on this tier: resolve by label or Linear project *within* a team.

**The monorepo dispatch test did not need the row** and passed on its own — five `apps/api`
gates `skipped_unchanged`, five `apps/web` gates `pass`, on a one-file CSS commit.

---

## Carried forward — none of these block Phase 6

### Open decisions

**1. Completing a run resets its `gc` clock.** Fourth session carried, and it is now visible
in live output rather than theoretical. `gc.py:97` computes age from `run.updated_at`, and
`factory complete` *writes* that row — so BAC-6 and FRO-11, finished minutes ago, are "0 days
old" against `worktree_days = 7`. `factory complete` says their worktrees are collectable;
`gc --dry-run` lists nothing. Both are behaving as designed and they contradict each other in
front of the user.

- *For:* §16.5's opening line says every rule is a floor, never a promise, and the run only
  becomes collectable at the merge.
- *Against:* the run stopped being worked on at `awaiting_human`. Late bookkeeping granting a
  fresh week is not what "kept for 7 days" reads like.

The change is small — measure from the `pr_ready -> awaiting_human` transition rather than
`updated_at` in `_collect_run` — but it changes what §16.5 *means*, so it stays a decision.
`python-harness/.factory/worktrees/BAC-4` and `frontend-harness/.factory/worktrees/FRO-7`
have been waiting on it for four sessions.

### Defects

1. **Host sleep reaps a healthy run as `attempt-orphaned`.** Open, and still the last known
   way a good run dies. The heartbeat subshell does not run while the host is suspended, so
   on every wake its file is minutes stale and `sbx.poll` takes the orphan branch with a live
   holder pid and a running sandbox. The signature §16.1 actually wants — the holder gone —
   is already checked first and fires immediately. `caffeinate` is the stopgap and it expires.
2. **The re-run ceiling counts orphans, not attempts.** Narrowed by `requires` and by `#48`,
   but a deterministic environment failure still burns the budget.
3. **`kill_agent` cannot stop a hung `verifying` gate.** Its body is a node gate report, not
   codex, so neither `pkill -f "codex exec"` nor `pkill -x codex` matches it. Pre-existing.
4. **New, small, and misleading:** `runs.blocked_reason` is never cleared when a run leaves
   `blocked`. FRO-11 sat at `awaiting_human` with a merged PR and `blocked_reason =
   review-agent-failed`. Nothing reads it in that state, which is exactly why it will mislead
   somebody — clear it on any transition out of `blocked`.
5. `_SENSITIVE_DIRS["frontend"]` matching nothing is **fixed** (`#47`), and `factory doctor`
   now fails if any declared glob matches no tracked file. `deliver._archive` still returns
   silently when the attempt directory is missing.
6. The frontend `playwright` probe writes `/tmp/playwright-probe.png`, so it is POSIX-only.
   Fails safe (`unavailable`, never a false `pass`). Recorded rather than fixed.

### Small follow-ups, deliberately not built

- **`factory doctor` should check the sbx-stored OpenAI token.** It is the single point that
  silently disables the entire factory, and today it took a transcript dive to find. This is
  the highest-value item on this list.
- The §18.5 console has Suspend and Resume but no **Complete**, though the edge now exists
  and has been taken twice.
- Nothing surfaces "PR merged — ready to complete" in `factory status`. The tick could notice
  and *say* so without taking the edge, which stays reserved to `merge-is-james`.

---

## What Phase 6 inherits

§19's Phase 6 is *"only changes that Phases 1–5 proved were needed"*, each requiring evidence.
Phase 5 produced that evidence for at least these, and each already has its measurement:

- `requires` is proven in both stacks, in real unattended sandboxes.
- The two-tier review's Tier 2 has now actually run (FRO-11), not merely been triggerable.
- The §15.3 companion checks have both fired in anger and both now have an answer.

Read §19's Phase 5 section before proposing anything. The eight-defect table is the argument
for how Phase 6 should be tested, and it is the part most likely to be skipped.

---

## The method, unchanged

Nothing in Phase 5 was found by reading the code first. Every defect came from running a real
ticket and reading what the machine actually wrote — transitions, transcripts, `pmset`,
`sbx secret ls`. Three habits earned their place again today:

- **A green suite is a claim, not evidence.** Every fix in this phase was mutation-tested:
  the change was reverted, the test was watched to fail, and the revert undone. Six mutations
  on `#46`, six on `#47`, three on `#48` — and one of them (`#47`'s fourth) exposed a test
  that asserted a table's contents while nothing asserted where the table's input came from.
- **Check the branch tip, not the PR state.** `git merge-base --is-ancestor <sha> origin/main`.
  Five stranding races in this project so far.
- **Before believing a handoff — including this one — grep the tree it describes.**
