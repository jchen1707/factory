# Phase 5 — closed. What the next session inherits

Written 2026-08-23 at the end of the session that finished it. Phase 5 is **complete**: both
exit tickets merged and recorded, the layer-C repository exists, and the last carried
decision is resolved.

**`/SOFTWARE-FACTORY-PLAN.md` is the authority, and it is the only file you may edit.**

There is a second copy at `.agents/plans/software-factory-plan.md`, restored 2026-08-23 on
James's word: he reads the plan through a model that can only see files under `.agents/` and
cannot open the published artifact. **It is generated and verbatim — never edit it.**

The distinction matters, because a copy at that exact path is what misled two sessions before
`9e33944` deleted it. That one was a *condensed* 169k rewrite: it said almost the same thing
in slightly different words, so a reader could not tell it had gone stale. The restored copy
is byte-for-byte, which means it can only diverge in **age** — and age is checkable:

```
python3 scripts/sync_plan_copy.py           # regenerate after editing the plan
python3 scripts/sync_plan_copy.py --check   # exits 1 when it has fallen behind
```

`factory doctor` runs that check (`plan copy` row) and so does the test suite
(`tests/unit/test_plan_copy.py`), so a plan edit that forgets the copy fails CI rather than
waiting to be noticed. **If you edit the plan, run the sync.**

---

## Where things stand

| | |
| --- | --- |
| `factory@main` | `fc74f4c` — `#46`, `#47`, `#48`, `#49`, `#50` merged today |
| suite | **565 passed**, mypy clean on 41 files, ruff check + format clean |
| `factory doctor` | all 19 checks pass |
| BAC-6 | **`completed`** — `python-harness#70` merged |
| FRO-11 | **`completed`** — `frontend-harness#48` merged |
| `test-project` | layer C, created and pushed; **no registry row, deliberately** |
| runs at `completed` | six: BAC-4, BAC-6, FRO-6, FRO-7, FRO-10, FRO-11 |
| open decisions | **none** |

---

## Start here: three things that will cost you an hour if you don't know them

### 1. There are two OpenAI credentials and they are unrelated

The single fact most likely to waste a session. It disabled the entire factory today while
every host-side check stayed green.

- `codex login` writes `~/.codex/auth.json` (~4 KB OAuth) — used by **host** codex calls.
- A **sandboxed** agent authenticates through `sbx`'s proxy against a *globally stored*
  OAuth token: `sbx secret ls` shows `(global) service openai (oauth configured)`. The
  sandbox's own `~/.codex/auth.json` is a **40-byte stub** and is not the credential in use.

When the stored token expires, every sandboxed run fails with `401 … token_expired` buried
in a per-axis transcript. `codex login` does **not** fix it:

```
sbx secret set openai --oauth      # answer y; through a non-tty shell use `yes | …`
```

Measured: **recreating the sandbox does not help** — a freshly created reviewer failed
identically, because the credential was never in the sandbox. `factory doctor` does not check
it. See [the follow-up list](#the-follow-ups-in-the-order-i-would-take-them).

### 2. The daemon runs whatever is checked out in `/Users/james/factory`

A `git checkout` there changes the code the next tick executes. Do control-plane work in a
`git worktree` — every PR in this session was built that way — and `git pull` the main
checkout immediately after a merge, or the daemon silently runs the old code.

### 3. Check the branch tip, not the PR state

`git merge-base --is-ancestor <sha> origin/main`. Five stranding races in this project so far,
one of them on the very PR that existed to repair the fourth.

---

## What Phase 5 proved, and the finding that should shape Phase 6

Two end-to-end tickets produced **eight control-plane defects**, seven on FRO-11 alone. Not
one was reachable by the test suite, which was green at 533, 541, 549, 562 and 565 tests
while each was live. The full table is in §19; the shape is what matters:

**Every one lives on a path that only runs after something else has already gone wrong.** And
four of the eight (`#41`, `#42`, `#46`, `#48`) fire *only when a human takes an edge* — which
is exactly why Phase 4.5's four unattended runs surfaced none of them.

Two of them are worth internalising rather than just reading:

- **`#47`** — the Tier-2 sensitive-path glob matched **0 of 192** tracked files and had since
  it was written. An inert trigger and a trigger that happened not to fire produce
  byte-identical evidence. `factory doctor` now fails if a declared glob matches nothing;
  that check is the pattern to copy whenever configuration claims something about a tree.
- **`#48`** — a resumed verify/review re-collected its own dead attempt for ever, so the run
  could not be moved by any command, and the block blamed the schema while the real cause
  (`turn.failed … token_expired`) sat unread one file away.

**The method that found all eight:** run a real ticket, then read what the machine actually
wrote — transitions, transcripts, `pmset -g log`, `sbx secret ls`. Nothing came from reading
the code first.

**And prove every fix by mutation.** Revert the change, watch the named test fail, restore it.
Six mutations on `#46`, six on `#47`, three on `#48`, four on `#50`. Two of `#50`'s were not
caught on the first attempt — nothing distinguished the first entry into a state from the
last, and the fallback test asserted the direction where a mistake merely delays collection
rather than the one where it destroys work. A green suite is a claim, not evidence.

---

## Carried defects — none block Phase 6

1. **Host sleep reaps a healthy run as `attempt-orphaned`.** Open, and the last known way a
   good run dies. The heartbeat subshell does not run while the host is suspended, so on
   every wake its file is minutes stale and `sbx.poll` takes the orphan branch *with a live
   holder pid and a running sandbox*. The signature §16.1 actually wants — the holder gone —
   is checked first and fires immediately, so reaching the stale-heartbeat branch at all
   means "the wrapper stopped writing but everything holding it is up", which is what
   suspension produces. `caffeinate` is the stopgap and it expires. **This is a §16.1
   semantics decision, not a bug fix** — that is why it is still here.
2. **The re-run ceiling counts orphans, not attempts.** Narrowed by `requires` and by `#48`,
   but a deterministic environment failure still burns the budget.
3. **`kill_agent` cannot stop a hung `verifying` gate.** Its body is a node gate report, not
   codex, so neither `pkill -f "codex exec"` nor `pkill -x codex` matches it. Pre-existing.
4. **`runs.blocked_reason` is never cleared when a run leaves `blocked`.** FRO-11 finished at
   `awaiting_human` with a merged PR and `blocked_reason = review-agent-failed`. Nothing reads
   it in that state — which is precisely why it will mislead somebody. Clear it on any
   transition out of `blocked`.
5. `deliver._archive` returns silently when the attempt directory is missing.
6. The frontend `playwright` probe writes `/tmp/playwright-probe.png`, so it is POSIX-only.
   Fails safe (`unavailable`, never a false `pass`). Recorded rather than fixed.

---

## The follow-ups, in the order I would take them

1. **`factory doctor` should check the sbx-stored OpenAI token.** It is the single point that
   silently disables the whole factory, it took a transcript dive to find, and doctor was
   fully green throughout. Highest value on this list by a distance.
2. **Clear `blocked_reason` on the way out of `blocked`** (defect 4). Small, and it stops a
   stale string misleading the next reader.
3. **Decide defect 1** (host sleep). A decision, then a small change in `sbx.poll`.
4. **The §18.5 console has no Complete button**, though the edge now exists and has been taken
   twice. The CLI and console share their machinery elsewhere; the divergence is worth closing.
5. **Nothing surfaces "PR merged — ready to complete" in `factory status`.** The tick could
   notice and *say* so without taking the edge, which stays reserved to `merge-is-james`.

Deliberately **not** on this list: a registry row for `test-project`. §10.2 resolves by ticket
prefix, the Linear workspace has only `BAC` and `FRO`, and a row with an invented key would
parse, match nothing and read like a working row — the `#47` failure mode exactly. §19 records
the change that would be needed (resolve by label or Linear project *within* a team) if the
factory is ever to drive a layer-C product on this tier. Nothing in Phases 1–5 needed it.

---

## Phase 6, when you get there

§19's Phase 6 is *"only changes that Phases 1–5 **proved** were needed"*, each requiring
evidence. Phase 5 supplied evidence for these and each already carries its measurement:

- `requires` is proven in both stacks, in real unattended sandboxes (`playwright` on FRO-11,
  Docker on BAC-6).
- Tier 2's nine-axis fan-out has now actually run (FRO-11), not merely been triggerable.
- Both §15.3 companion checks have fired in anger, and both now have an answer a human can give.

Read §19's Phase 5 section first — particularly the eight-defect table and the two
environment facts under it. It is the part most likely to be skipped and the part most likely
to save the session.

---

**One loose end, flagged not fixed:** `software-factory-plan.html` at the repository root is a
235k render dated 2026-08-20, from Phase 1. It is four phases stale and it is the same
duplicate-plan hazard `9e33944` removed, in a different file extension. Regenerate it from the
canonical markdown or delete it — but do not leave it there to be read.
