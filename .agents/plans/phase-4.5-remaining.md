# Phase 4.5 — what remains, and a proposed re-plan

**Read this first, then `phase-4.5-handoff.md` for how the defects were found.** This file
is only about what is *left*. §19 Phase 4.5 in `SOFTWARE-FACTORY-PLAN.md` is the authority
and is unedited — the re-plan below is a proposal, because that file is protected in
`harness.config.json` and changing it is James's decision.

**The method has not changed and is the only reason any of this was found.** Every one of
the seven defects behind this handoff was found by executing the thing, never by reading
about it. Three of them lived in code that no run had ever reached. A green suite is a
claim, not evidence — see `prove tests fail without the fix` below.

---

## Status at hand-off

| | |
| --- | --- |
| Phase 4 | **closed** — 120 clean ticks, a ticket claimed and driven unattended. Recording that in the plan is James's edit. |
| Phase 4.5 exit criterion | **not met.** Acceptance run `40d1d56205f64361` was in flight at hand-off |
| step 4.5-5 (suspend/resume) | **half done** — live `resume` proven on run `b1aa9785bbe44663`; `suspend` and the browser path not run |
| step 4.5-6 (resume --from planning) | **not started** |
| defects fixed this session | 6, all merged (harness#17, fe#43, py#67, factory#25/#27/#29/#30) |
| defects still open | 3, listed at the bottom. One is James's decision, two are new |

Every hop from `claimed` to `awaiting_human` has now succeeded at least once on real
infrastructure, several for the first time ever. What has never happened is **all of them
in one run with a clean transition log**, which is exactly what the exit criterion asks
for.

---

## 1. Finish the acceptance run

Check this before anything else. The run may have landed, blocked, or still be going.

```sh
uv run factory status
sqlite3 state/factory.db "
  select from_state, to_state, actor, rule from transitions
   where run_id = (select id from runs where linear_id='FRO-7'
                    and state != 'cancelled' order by created_at desc limit 1)
   order by rowid"
```

**Name the run id, never the ticket.** FRO-7 has six run rows and five are `cancelled`,
several with `blocked_reason = env-gate-failed` — a query ordered by `created_at` picks up
a dead one and reports the live run as failed.

The criterion has three clauses, and reaching `awaiting_human` is only the first:

1. state is `awaiting_human`;
2. the PR exists **and `isDraft` is true** (§24.8 — draft through 4.5);
3. **no `actor = "human"` row** anywhere before it.

If it blocked, expect a *new* cause. FRO-7 has now blocked at four distinct causes; reading
a repeat as a recurrence would send you chasing a fix that already landed. Diff the inputs
and read the transition `detail`, never the state.

### if it needs a re-run

The full cleanup, learned the hard way — `factory cancel` does **not** do most of it:

```sh
# 1. If the run opened a PR, close it and delete the branch. `deliver` pushes without
#    --force, so a fresh run cutting the same branch name is rejected non-fast-forward
#    and blocks at the last hop. Tag it first so nothing is lost.
cd /Users/james/frontend-harness
git tag archive/FRO-7-run-<runid> <sha> && git push origin archive/FRO-7-run-<runid>
gh pr close <n> && git push origin --delete feat/FRO-7-filter-your-projects-by-status-including

# 2. Cancel. Watch its output: if it says "left FRO-7 at '<state>' (not the state the
#    factory set)", the tracker was NOT restored and step 3 is mandatory.
uv run factory cancel FRO-7

# 3. Put Linear back by hand: state Todo, and remove `needs-info` if present.
#    Both must be true or the poller skips the ticket forever.

# 4. Only now let the daemon see it. Do NOT do steps 2 and 3 in the other order:
#    the daemon claims on a 60 s timer and will create an intake-blocked run row the
#    moment it sees a non-Todo ticket, which makes it invisible until another cancel.
```

---

## 2 and 3. Steps 4.5-5 and 4.5-6 — and why they need a re-plan

§19 lists the steps `4 -> 5 -> 6` **on one ticket, FRO-7**. That is not satisfiable, and it
is not a matter of effort:

- `suspend` is legal only from `planning`, `implementing`, `verifying`, `reviewing` — so it
  needs a run **in flight**. Suspending the acceptance run writes the `actor = "human"` hop
  that voids it.
- After the run lands, `awaiting_human` has exactly four exits — `implementing`, `blocked`,
  `completed`, `cancelled`. There is no edge to `suspended`. Verified:
  `[s for s in State if can(State.AWAITING_HUMAN, s)]`.

So the acceptance run and the suspend/resume validations are **mutually exclusive on one
ticket**, by the state machine. They need two.

### the second ticket does not exist either

Measured against Linear, every `ready-for-agent` ticket in FRO and BAC:

| | |
| --- | --- |
| Done | BAC-3, BAC-4, BAC-5, FRO-5, FRO-6, FRO-10 |
| Canceled | BAC-2, BAC-6, BAC-7, BAC-8, BAC-9 |
| In Progress / claimed | FRO-7 |
| Todo | **BAC-1 and FRO-1 only** |

BAC-1 and FRO-1 are refused at intake with `no-parent-spec`, and that is correct and
permanent: `parent_identifier` is empty on both because **they are the parent specs**. Every
other ticket in their teams names one of them. Intake is right; no cancel recovers them.

---

## The proposed re-plan

**Three changes to §19 Phase 4.5.** None of them lowers the bar; they make the steps
executable and put each one on a subject that can carry it.

### A. Split the phase's steps into two tracks, on two tickets

| track | ticket | steps | why |
| --- | --- | --- | --- |
| **acceptance** | FRO-7 | 4.5-1 … 4.5-4 | the exit criterion; must stay free of human hops |
| **control validation** | a second ticket | 4.5-5, 4.5-6 | needs a run in flight to suspend, which the acceptance run cannot be |

State the two tracks as independent rather than ordered `4 -> 5 -> 6`. The plan's own
wording already supports this: the unattended run is *"the one thing that closes this
phase"* and 5/6 are *"confirmation on the real adapters, not first proof."*

### B. Name the second ticket, and prefer a python-harness one

**Recommended: BAC-6** (`Generate the Meridian mock corpus and the gold set`), or BAC-7 as
the fallback. Both are `Canceled` with parent BAC-2 and **all blockers `completed`**, so
moving one to `Todo` should make it intake-eligible without inventing anything. Verify
intake before relying on it — a Canceled *parent* may or may not satisfy the parent-spec
condition, and that has not been tested.

Two reasons to prefer BAC over another FRO ticket:

1. **python-harness is bind-mounted, not `--clone`.** Every defect this session found in
   the clone path (`refresh_base`, `release_branch`) and in the review path was found on
   frontend-harness. Running the control validations on the *other* project shape is the
   cheapest coverage available, and `_review_spec`'s read-only worktree — the bind-mounted
   half of the review defect fixed in factory#29 — has still never been exercised live.
2. It leaves FRO-7 alone.

If BAC-6 turns out to be intake-ineligible, a ticket must be **written**, and that is a
hand-off to the ticket-generation system, not work to code around: §24.1, the factory reads
tickets and never files them.

### C. Do the control validations *deliberately*, not unattended

4.5-5 and 4.5-6 both begin with a human typing a command, so there is nothing to prove
about the poller here. Claim the second ticket with `factory run <TICKET>` and drive it by
hand. That also keeps the daemon from racing the suspend.

### the steps, restated

| # | step | command | expected |
| --- | --- | --- | --- |
| 4.5-5a | live suspend | `uv run factory suspend BAC-6 --reason "…"` **while it is `implementing`** | an `actor="human"` transition to `suspended`; the sandbox stops; the attempt keeps a real terminal record (`kill_agent` signals, the wrapper writes `exit`) |
| 4.5-5b | live resume | `uv run factory resume BAC-6` | `actor="human"`; the Codex session resumes **by id**, never `--last`; **no Linear comment repeats** — check the effects ledger, that is what §16.2 is for |
| 4.5-5c | the same two from the browser | `uv run factory serve --port 7717`, then the Suspend and Resume controls | identical transitions and rules to the CLI; the console POST path shares `_cancel_run`/`recovery.resume`, so a divergence here is a real defect |
| 4.5-6 | live resume-from-planning | `uv run factory resume BAC-6 --from planning` | rewinds to a fresh plan **without resetting the worktree** — check the branch head is unchanged and the agent's commits survive |

### D. One thing to fix before running them

`recovery.resume` writes `actor="human"` on every entry, which is correct — but it means
**every one of these steps permanently disqualifies its ticket from ever being an
acceptance run**. That is fine for BAC-6 and worth knowing before choosing the ticket:
pick one you are willing to spend.

---

## Open defects — not fixed, and two are new

1. **Nothing ever reaches `COMPLETED`.** `grep -rn "State.COMPLETED" src/factory/ | grep -v
   machine.py` returns nothing, so a successful run sits at `awaiting_human` forever and
   `gc` can never reclaim its worktree, branch or artifacts (`gc.COLLECTABLE` is
   `TERMINAL | {FAILED}`). **Left open by James's decision** — the transition sits on the
   merge boundary that §24.8 makes Phase 5's. Its worst symptom (stale worktrees breaking
   `pnpm test`) is fixed from the other side in frontend-harness#43.

2. **`cancel` cannot restore a tracker it did not set — which is exactly the successful
   case.** It refuses to move a Linear state the factory did not put there, which is the
   right instinct. But when a run opens a PR, the GitHub integration moves the ticket to
   `In Review`, so cancelling *any run that got as far as a PR* leaves the ticket at a
   non-`Todo` state, silently, and the poller then skips it forever. Cancel prints `left
   <T> at '<state>' (not the state the factory set)` and nothing tells the reader that this
   means the ticket is now unclaimable. Measured on FRO-7 twice in one hour. It also left
   `needs-info` in place on one of those two.

3. **The re-run ceiling counts orphans, not attempts.** `resumable_reentries` counts
   `<state> -> resumable` transitions, so a flaky agent gets three real tries while a
   *deterministic* environment failure — a mount the sandbox cannot reach — burns the whole
   budget in three ticks and fails a run whose work is perfectly good. That is what killed
   run `b1aa9785bbe44663`, whose implementation was 7 files, +329/-28, every gate green. An
   attempt that dies **before its first heartbeat never ran**, and arguably should not be
   charged against a ceiling meant to bound a *hanging* one. factory#30 made `--authorise`
   able to recover such a run; it did not address why the run failed.

Carried and still true: `_SENSITIVE_DIRS["frontend"]` matches nothing, and
`deliver._archive` returns silently when the attempt directory is missing.

---

## Footguns, cumulative

1. **Name the run id, never the ticket.** FRO-7 has six run rows; five are `cancelled` and
   several carry `env-gate-failed`. A `order by created_at desc limit 1` reports a dead run
   as the live one — it reported the acceptance run as failed before its agent started.
2. **`launchctl bootstrap` reports failure while still loading the plist.** Check
   `launchctl print gui/$(id -u)/com.jchen.factory`, never the exit code. `bootout` is the
   same in reverse.
3. **A gate status lives in two places.** `verify.py` decides what a status *means*;
   `schemas/gate_report.schema.json` decides whether the document may carry it, and it is
   checked **first**. Layer A's `plugins/harness/schema/` does **not** hold the gate-report
   contract — layer D keeps its own copy — so grepping the harness repo finds nothing. The
   drift guard added in factory#27 now fails loudly if they separate.
4. **Blocking at the same state twice does not mean the same defect.** FRO-7 blocked at
   `verifying` four times for three distinct causes. Read the transition `detail`.
5. **A crashing tick discards everything it did.** `_tick_pass` buffers its lines and
   prints at the end, so a crashed tick leaves `daemon.out.log` empty while the transitions
   table records real work. Read the DB, not the log.
6. **The daemon races you.** It claims on a 60 s timer, and a ticket in a non-`Todo` state
   produces an intake-blocked run row that hides the ticket until another `cancel`. Fix the
   tracker *before* cancelling, not after.
7. **`git commit` in frontend-harness runs lint-staged**, which rewrites staged files. Run
   `vendor_sync.py check` *after* committing a sync, not before.
8. **Prove tests fail without the fix.** Stash the source and re-run. Of the seven defects
   this session, three lived in code no test reached — the unit tests for the `disabled`
   status called the predicates directly and passed while the schema rejected the document.
   A test at the wrong altitude is worse than no test, because it reads as coverage.
