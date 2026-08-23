# Phase 4.5 handoff — 2026-08-22

**Phase 4 is closed. Phase 4.5 is not**, and what stands between them is one config
flag, half-landed. Read §19 Phase 4.5 in `SOFTWARE-FACTORY-PLAN.md` for the authority;
this file is what happened when it was actually run.

**The method, before anything else.** Every defect below was found by executing the thing,
never by reading about it. Three of them sat behind a §19 row that said **built** — the
plist, the clone refresh, the branch cleanup — and every one of those rows was honest: the
file existed, the code was there, the tests passed. None of them had ever run. Audit §19
and §22 against the code, and then run the code, because the code passing its tests is a
third claim again.

---

## Phase 4 — closed

The one sentence Phase 4 asserted and had never demonstrated was *"a ticket labelled
`ready-for-agent` reaches `awaiting_human` with no keystroke."* The keystroke half is now
proven; the destination half is Phase 4.5's and is not.

| evidence | value |
| --- | --- |
| launchd timer loaded | `gui/501/com.jchen.factory`, `StartInterval 60` |
| consecutive clean ticks | **120**, `last exit code = 0` |
| last error written to `daemon.err.log` | **17:17:25**, the final pre-fix crash |
| claimed a ticket unattended | yes — FRO-7 run `f5ce5803462d4766`, six `auto` transitions |
| drove a detached agent unattended | yes — reaped, collected, advanced `implementing -> verifying` |

That last row is the one that matters. §4.2's property — *the tick that asks is never the
tick that started the run* — held against a real 20-minute Codex run on a real clock, in a
process that did not exist when the run started.

**Marking it closed in the plan is James's edit.** `SOFTWARE-FACTORY-PLAN.md` is protected
in `harness.config.json` ("a change to it is a re-plan, which is James's decision — raise
the disagreement instead"), so this file records the closure and the plan does not. The
wording that fits §19's table, if you want it:

> **Phase 4 — closed 2026-08-22.** Timer loaded, 120 consecutive ticks at exit 0, one
> ticket claimed and driven unattended through a detached agent run.

---

## Phase 4.5 — where it stopped, and why

The exit criterion is unchanged and unmet:

> A ticket the factory claimed on its own reaches `awaiting_human` with a draft PR, and the
> transition log shows no `actor = "human"` hop before it.

FRO-7 has been claimed unattended **twice** and blocked at `verifying` both times with
`env-gate-failed`. Both times the cause was `lighthouse`, and the second time was not the
same defect as the first.

### Run 1 — `057f4f211f0b4ca5` — the factory over-asserted

The agent claimed six gates, `playwright` among them, and **not** lighthouse. But
`_claims_opt_in` returned a bool and the caller passed `--all`, which asserts *every*
opt-in gate's `when` clause at once. So lighthouse ran anyway, on a `when` — *"performance
or accessibility budgets are in scope"* — that was plainly false for a status filter.

Fixed, landed, and it works: layer A `--gate <name>` (harness#16, 0.8.0) plus
`_asserted_opt_in_gates` on this side (factory#23). Vendored into both consumers
(frontend-harness#42, python-harness#67).

### Run 2 — `f5ce5803462d4766` — the agent over-claimed

Same ticket, same prompt, **different claim**: this time the agent ran lighthouse itself
and reported `lighthouse — exit 0; accessibility 1.00; performance score null; report
upload blocked`. So `_asserted_opt_in_gates` correctly returned `["playwright",
"lighthouse"]`, the factory passed `--gate playwright --gate lighthouse`, and the new hook
faithfully ran both.

**The fix worked. The input changed.** Proof the new path was live rather than the old one:
with the old hook and no `--all`, both opt-in gates would have come back `not_applicable`
and the run would have died on `evidence-mismatch`. Instead `playwright` is `pass` and
`lighthouse` is `fail` — only per-gate assertion produces that shape.

**The lesson worth carrying: which opt-in gates the agent claims is not deterministic.**
Any fix that depends on the agent claiming a particular set is not a fix. That is why the
next change switches the gate off in config rather than trying to keep it unasserted.

### What lighthouse actually does on this host

Measured, not inferred:

| | |
| --- | --- |
| the failure in the report | `❌ Chrome installation not found`, exit 1, **515 ms** — a healthcheck, before Lighthouse ran |
| why | `CHROME_PATH` is unset. Playwright's Chromium **is** in the sandbox at `~/.cache/ms-playwright/chromium-1228/chrome-linux/chrome`; LHCI does not look there |
| with `CHROME_PATH` set | healthcheck passes, Lighthouse runs, real scores appear (`1`, `0.93`) alongside nulls |
| still exits 1 | the report upload is blocked by the egress policy every time (`Blocked by...` from the proxy), and the run is **flaky**: two consecutive invocations gave *"All results processed"* and *"Run #1...failed!"* |
| in GitHub CI | **passes**, 47 s green on frontend-harness#42 |

So the gate is not wrong and deleting it would be. The host is wrong, and the project does
not care about performance budgets yet. That is the decision recorded below.

---

## Session 2 — 2026-08-22, later
### what closed, and what is left

Steps 1-5 below are **done**. Step 6 is confirmed impossible today, against the live board
rather than by report. Four of the five open defects are fixed; the fifth is deliberately
not.

| step | state |
| --- | --- |
| 1. land `enabled: false` | harness#17 **merged**, 0.9.0, three checks green |
| 2. vendor sync both consumers | frontend-harness#43 and python-harness#67 **merged**, both on `harness@3248fbe` |
| 3. lighthouse off in config | **merged** in #43; `gate_report` returns `disabled` even for an explicit `--gate lighthouse` |
| 4. teach the factory `disabled` | factory#25 **merged** |
| 5. re-run FRO-7 | **claimed unattended** as run `4d2faf24b79a4094`; in flight at hand-off |
| 6. suspend / resume | **no subject exists**, now verified — see below |

**Lighthouse still runs in CI, and the last handoff did not say so.** `ci.yml` has its own
`lighthouse` job that calls `pnpm lhci` directly and never reads `harness.config.json`; it
passed in 40 s on #43. So `enabled: false` switches the gate off on the *factory host*,
where it cannot pass, and leaves the check that actually protects `v2` in place. The
uncovered window is only a local or agent run between pushes.

### defects fixed

**2 and 3, the two that did not fail loudly, are now code rather than chores.** The
manual repairs in the previous handoff are obsolete; do not run them.

- `clone.refresh_base` fetches `origin/<base>` inside the clone before a branch is cut,
  and only on the cut — the re-entrant path attaches to the agent's commits, where moving
  the base would change nothing but the diff the review reads. A failed fetch blocks as
  `clone-fetch-failed` rather than falling through to the stale ref. **Verified against
  the real sandbox, not only the fake:** the clone's `origin` is
  `https://github.com/jchen1707/frontend-harness.git` (not the host checkout, which is
  worth knowing — a merge is visible to the clone without the host pulling first), and the
  real fetch moved it `910a2f1 -> d574788`.
- `clone.release_branch` runs at `cancel`, before the sandbox is stopped. It **unnames
  rather than deletes**: the branch is copied to `refs/factory/cancelled/<run id>/<branch>`
  and only then removed from `refs/heads`. A clone's commits were never pushable at all
  (§13.2), so they are the only copy. Verified on the real cancel of FRO-7: the branch is
  gone, the clone is back on `v2`, and the rescue ref holds `431fa85` — the exact head
  recorded beforehand.
- **4** — `.factory/**` added to Vitest's `exclude` in `vite.config.ts`. Reproduced both
  ways with one worktree present: without it, `4 failed | 18 passed (22)`, 146 tests, exit
  1; with it, `9 passed (9)`, 73 tests, exit 0.
- **5** — `ruff` pinned `==0.16.4`, not `>=0.6`.

**Defect 1 is deliberately not fixed.** James's call: nothing reaches `COMPLETED`, `gc`
still cannot reclaim, and it stays open because the transition sits on the merge boundary
that §24.8 makes Phase 5's. Defect 4 removes its worst symptom without touching it.

### step 6 has no subject, and this is now measured

The previous handoff inferred it. Queried against Linear, every `ready-for-agent` ticket
in FRO and BAC:

| | |
| --- | --- |
| Done | BAC-3, BAC-4, BAC-5, FRO-5, FRO-6, FRO-10 |
| Canceled | BAC-2, BAC-6, BAC-7, BAC-8, BAC-9 |
| In Progress | FRO-7 |
| Todo | **BAC-1 and FRO-1 only** |

BAC-1 and FRO-1 are the two the poller keeps rejecting with `no-parent-spec`, and the
reason is not a bug and not repairable by a cancel: **they have no parent because they
*are* the parent specs.** `parent_identifier` is empty on both, and every other ticket in
their teams names one of them. They are epics, so the intake condition is right to refuse
them and will refuse them forever.

So steps 4.5-5 and 4.5-6 need a ticket that does not exist. Writing one is not the
factory's job — §24.1, it reads tickets and never files them — so this is a hand-off to
whoever runs the ticket-generation system, not a task for the next session to code around.

### the acceptance run, in flight

FRO-7 was cancelled and **left alone**, which is the whole point: the claim must not be a
keystroke. The daemon took it 3 ticks later.

| | |
| --- | --- |
| run | `4d2faf24b79a4094` — the **fourth** FRO-7 run; the three before it are `cancelled` |
| claimed by | the launchd timer, unattended, at tick 149 |
| transitions to `implementing` | six, all `actor = auto` |

**Both clone fixes are proved in production by this run, not only against the fake.** The
branch was cut from `d574788` — the merge of #43, which did not exist when the sandbox was
created — and `git show <branch>:harness.config.json` in the clone carries `"enabled":
false`. Before `refresh_base` the cut would have come off the sandbox's frozen
`origin/v2`, the config there still has lighthouse switched on, and the run would have
blocked on it a third time. The branch was cuttable at all because `release_branch` had
removed the abandoned one; `create_branch` would otherwise have checked out the third
run's commits and reported them as this run's work.

### the one thing still unproven

The exit criterion itself — the destination. What the next session must check first,
before anything else:

```sh
uv run factory status
sqlite3 state/factory.db \
  "select from_state, to_state, actor from transitions
    where run_id = '4d2faf24b79a4094' order by rowid"
```

`awaiting_human` with a draft PR and **no `actor = "human"` row before it** closes Phase
4.5. Anything else is the next defect, and the rule from the last session still holds: two
identical failures are not necessarily the same defect — diff the inputs.

---

## The remaining work, in order

**Steps 1-5 are done; see "Session 2" above. This section is kept as the record of what
was decided and why.**

James's decision, 2026-08-22: *"make lighthouse optional since for now I'm just building
internal applications — SEO checks aren't as important. Have it as a config option and
disable it for now and move on."*

### 1. Land `enabled: false` — **in flight, PR open**

[harness#17](https://github.com/jchen1707/harness/pull/17), branch `feat/gate-enabled`,
version 0.9.0. CI was still running when this was written; check it before merging.

- `harness.config.schema.json` — optional `enabled` boolean on a gate, omit for `true`
- `gate_report.mjs` — `enabled: false` reports **`disabled`** and never executes

`disabled` outranks every other reason a gate might not run, `--all` and an explicit
`--gate` included, so an operator's answer does not depend on what the turn touched.
It is not `pass` (nothing ran) and not `incomplete` (nothing failed to start that was
meant to start); `computeVerdict` special-cases only `fail` and `unavailable`, so it falls
through to `pass` with no change there. 130 tests pass; removing the branch fails two.

### 2. Vendor sync into both consumers

```sh
python3 /Users/james/harness/scripts/vendor_sync.py sync \
  --target /Users/james/frontend-harness --harness /Users/james/harness
# same for python-harness; commit .agents/vendor/harness/** + MANIFEST.json only
```

Both repos, on one revision — `vendor-freshness.yml` compares against `v2`, so leaving one
behind goes red on the next unrelated PR.

**frontend-harness enforces its PR template** (`.github/PULL_REQUEST_TEMPLATE.md`) with a
`body` CI check: `## Summary`, `## What changed`, `## How to demo`, `## Evidence`. A body
in any other shape fails the check. python-harness has the same template and does not
enforce it.

### 3. Switch lighthouse off in `frontend-harness/harness.config.json`

```json
{ "name": "lighthouse", "kind": "integration", "run": ["pnpm", "lhci"],
  "enabled": false, "when": "...", "caveat": "..." }
```

Keep `when` and `caveat` — they are the record of what the gate would check and how it
lies, and they cost nothing while it is off.

### 4. Teach the factory about `disabled` — **not yet written**

Two edits in `src/factory/steps/verify.py`, neither of which does anything until a config
carries `enabled: false`:

- `_asserted_opt_in_gates` must not emit `--gate` for a disabled gate. Harmless if missed
  (the hook ignores the assertion) but it misstates intent in the argv.
- **`_evidence_mismatch` must tolerate `disabled`, and this one is load-bearing.** `_RAN`
  is `{"pass", "fail"}`. The agent may still run lighthouse on its own and claim it — it
  did exactly that in run 2 — and the report will now say `disabled`. Without this, the
  run blocks on `evidence-mismatch` instead of `env-gate-failed`, which is the same wall
  wearing a different name.

`factory.harness.Gate` needs the `enabled` field to carry it that far; check
`load_harness_config` parses it and defaults to `True`.

### 5. Re-run FRO-7

```sh
uv run factory cancel FRO-7          # clears the blocked row and restores Todo
                                     # then leave it alone: the daemon claims it
```

**Before cancelling, do the two clone chores in "Open defects" 2 and 3 below**, or the run
builds on a stale base or attaches to an abandoned branch. Both are silent failures.

### 6. Steps 4.5-5 and 4.5-6 still have no subject

Live `suspend` / `resume` / `resume --from planning` against real infrastructure. They
**cannot** be done on FRO-7:

- suspending it mid-flight writes the `actor = "human"` hop that voids the acceptance run;
- after it lands, `machine.can(AWAITING_HUMAN, SUSPENDED)` is `False` — `awaiting_human`
  has exactly three exits, `completed`, `cancelled` and `implementing`.

So they need a second eligible ticket, and none exists today: every other
`ready-for-agent` ticket is intake-blocked, canceled, or holds an open PR. §19 orders the
steps 4 → 5 → 6 on one ticket, which is not satisfiable. The plan's own exit criterion
sentence resolves it — the clean unattended run is *"the one thing that closes this
phase"*, and 5/6 are *"confirmation on the real adapters, not first proof"* — but they are
steps of the phase and they are not done.

---

## Open defects — 1 of 5 still open

All five were found by running the pipeline, and **numbers 2 and 3 were the dangerous
ones: they do not fail, they silently build the wrong thing.** Both would have corrupted
the second FRO-7 run had they not been caught by hand.

**Session 2 fixed 2, 3, 4 and 5 in code; only 1 remains open, by decision.** The entries
below are kept as written — they are the measurements, and the manual repairs in 2 and 3
are now obsolete and must not be run.

1. **Nothing in `src/factory/` ever advances a run to `COMPLETED`.** The state is in
   `_WORKFLOW`, `merge-is-james` is declared for it, and `gc.COLLECTABLE` is
   `TERMINAL | {FAILED}` — but no CLI command, console control or tick step can reach it.
   `grep -rn "State.COMPLETED" src/factory/ | grep -v machine.py` returns nothing. So a
   successful run stays at `awaiting_human` forever and **`gc` can never reclaim its
   worktree, branch or artifacts**; §16.5 collects only abandoned runs. Surfaced when
   FRO-6's stale worktree broke `pnpm test` in frontend-harness and blocked a push.
   BAC-4's equivalent is still sitting in python-harness, latent only because pytest does
   not glob into `.factory/`.

2. **The clone never re-fetches `origin`.** `create_branch` cuts from `origin/<base>`
   *inside* the clone, deliberately without a network call, and nothing refreshes it after
   the sandbox is created. Measured: host `origin/v2` was `910a2f1`, the clone's was
   `1c4422d` — three merges behind. FRO-7 would have been cut from a base with neither
   FRO-6's seams nor the vendored hook. Manual repair:
   ```sh
   sbx exec factory-build-frontend-harness -- git -C /Users/james/frontend-harness fetch origin v2 --prune
   sbx exec factory-build-frontend-harness -- sh -c "cd /Users/james/frontend-harness && git merge --ff-only origin/v2"
   ```

3. **`cancel` does not remove the clone's branch.** `_release_local_debris` deletes the
   *host* branch; for a `--clone` project the branch lives in the VM and survives. Since
   `create_branch` is re-entrant by design, the next run checks out the abandoned branch
   instead of cutting fresh. Manual repair:
   ```sh
   sbx exec factory-build-frontend-harness -- sh -c "cd /Users/james/frontend-harness && git checkout v2 && git branch -D feat/<TICKET>-<slug>"
   ```

4. **`vitest` globs into `.factory/worktrees/**` in frontend-harness.** A stale factory
   worktree makes `pnpm test` fail on files outside the repo — 4 test files failed, 146
   tests passed. It blocked a push until the worktree was removed by hand.

5. **`ruff` is unpinned** (`ruff>=0.6`). It resolved to 0.16.4, whose formatter changed,
   and five files were failing `ruff format --check` on `main` — one of this repo's four
   Definition-of-Done gates — before anyone touched them. Reformatted in factory#23. Pin
   it exactly so a formatter release cannot fail the DoD out from under a run.

Carried from the Phase 4 handoff and still true: `_SENSITIVE_DIRS["frontend"]` matches
nothing, and `deliver._archive` returns silently when the attempt directory is missing.

---

## Footguns this session added

1. **`launchctl bootstrap` reports failure for a trailing argument while still loading the
   plist.** A stray `—` produced `Bootstrap failed: 5: Input/output error`, and the job was
   registered and running anyway. Check `launchctl print` before believing the error.
2. **A crashing tick discards everything it did.** `_tick_pass` collects its lines and
   prints them at the end, so 16 crashed ticks left `daemon.out.log` empty while the
   transitions table recorded real work. Read the DB, not the log, when the daemon looks idle.
3. **`FileNotFoundError` is not in `_work_on`'s caught set.** One missing binary kills the
   whole pass rather than one run. Worth widening — an adapter that is absent is the same
   class of problem as an adapter that fails.
4. **Two identical failures are not necessarily the same defect.** Both FRO-7 runs blocked
   with `env-gate-failed` on lighthouse. The causes were unrelated, and reading the second
   as a recurrence of the first would have sent the next session chasing a fix that was
   already correct and already landed. Diff the *inputs* — here, `gates_run`.
5. **Layer A's `generate` job fails a change that does not bump the version.** *"N layer A
   file(s) changed but version is still 'X'. Consumers would be told they are already up to
   date."* Bump `plugins/harness/.claude-plugin/plugin.json` in the same PR.

---

## Footguns session 2 added

1. **FRO-7 now has four run rows, three of them `cancelled`.** Any query that says "the
   latest FRO-7 run" and orders by `created_at` picks up a dead one, and the dead ones are
   `cancelled` with `blocked_reason = env-gate-failed` — which reads exactly like the live
   run having failed the way the last two did. A watch armed that way reported the
   acceptance run as failed before its agent had started. **Name the run id.**
2. **A `--clone` sandbox's `origin` is GitHub, not the host checkout.** Worth knowing in
   both directions: a merge is visible to the clone the moment it lands, with no host
   `git pull` — and equally, `refresh_base` needs real network egress, so it is not the
   credential-free operation the rest of the clone path is.
3. **`git commit` in frontend-harness runs lint-staged, which rewrites staged files.**
   Harmless here — `.prettierignore` excludes `.agents/vendor/`, so a vendor sync survives
   its own commit — but check `vendor_sync.py check` after committing a sync rather than
   before, because a formatter that did reach those files would break the sha check
   silently.

## State of the board

- **FRO-7** — run `4d2faf24b79a4094`, claimed unattended, in flight. The three earlier
  runs are `cancelled`. Run 3's implementation was real — 6 files, 78 unit tests, 5 e2e
  tests, all five stop gates and playwright passing — and is **not** lost: `cancel` saved
  it to `refs/factory/cancelled/f5ce5803462d4766/feat/FRO-7-...` in the clone, at
  `431fa85`. It is being redone from scratch regardless; the ref is there if the fourth
  run produces something worse.
- **FRO-6** — Done, #41 merged. Host worktree removed by hand. **Its clone branch is still
  there** — `release_branch` only runs on a cancel, and FRO-6 ended at `awaiting_human`.
  It misdirects nothing while FRO-6 stays Done, and it is the visible tip of open defect 1:
  a run that succeeds is never collected.
- **BAC-4** — `awaiting_human`, #66 merged, stale worktree still in python-harness.
- Six BAC tickets and FRO-1/FRO-5 carry `blocked` run rows from intake failures. **A
  reasoned intake failure creates a live run row, and `_claim_new_work` checks
  `live_run_for_ticket` before `assess`** — so those tickets are invisible to the poller
  forever, even if their Linear state changes. The recovery is `factory cancel <TICKET>`,
  and nothing documents that. Not a bug today; it accumulates under a timer.

## Landed session 2

| repo | PR | state |
| --- | --- | --- |
| harness | [#17](https://github.com/jchen1707/harness/pull/17) — `enabled:false`, 0.9.0 | merged |
| frontend-harness | [#43](https://github.com/jchen1707/frontend-harness/pull/43) — vendor 0.9.0, lighthouse off, Vitest exclude | merged |
| python-harness | [#67](https://github.com/jchen1707/python-harness/pull/67) — vendor 0.9.0 | merged |
| factory | [#25](https://github.com/jchen1707/factory/pull/25) — `disabled`, `refresh_base`, `release_branch`, ruff pin | merged |

## Landed session 1

| repo | PR | state |
| --- | --- | --- |
| harness | [#16](https://github.com/jchen1707/harness/pull/16) — `--gate`, 0.8.0 | merged |
| harness | [#17](https://github.com/jchen1707/harness/pull/17) — `enabled:false`, 0.9.0 | **open, CI running** |
| frontend-harness | [#42](https://github.com/jchen1707/frontend-harness/pull/42) — vendor 0.8.0 | merged |
| python-harness | [#67](https://github.com/jchen1707/python-harness/pull/67) — vendor 0.8.0 | open |
| factory | [#23](https://github.com/jchen1707/factory/pull/23) — `--gate` caller, launchd PATH | merged |

**Do not start Phase 5.** It flips PRs from draft to ready-for-review (§24.8), and the
unattended path still has not delivered one. That is also why open defect 1 stays open:
the `awaiting_human -> completed` hop is the merge boundary, and the merge boundary is
Phase 5's.
