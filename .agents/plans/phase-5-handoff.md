# Phase 5 — where it stands, and what to do next

Rewritten 2026-08-23, after the first Phase 5 item landed. The previous version of this
file (the one written at the close of Phase 4.5) is in git history at `9e6c172`; its four
"do these before starting" items are all settled, and what replaced them is below.

**§19 Phase 5 in `SOFTWARE-FACTORY-PLAN.md` is the authority.** Everything here either
points at it or records a fact measured on 2026-08-23 that §19 does not carry.

---

## Where things stand

| | |
| --- | --- |
| `main` | `5ee99c2`, **pushed**. Carries the Phase 4.5 fixes, the §19 re-plan and `factory complete` |
| suite | 529 passed, mypy clean on 41 files, ruff clean |
| `awaiting_human` | **empty** — the state that could never be left has nothing in it |
| runs | 4 `completed`, 30 `cancelled`, 8 `blocked`, 1 `suspended` |
| BAC-6 | `suspended` at attempt 7 (planning), worktree and session intact. `needs-info` **removed by James** |
| daemon | loaded, ticking every 60 s, running `main` |

---

## What landed this session

**`factory complete <TICKET>` — §19 Phase 5's first item, and the one §19 does not name.**

The `AWAITING_HUMAN -> COMPLETED` edge existed at `machine.py:103` from the start and
`merge-is-james` reserved it at `machine.py:162`; nothing ever took it. Because
`gc.COLLECTABLE` is `TERMINAL | {FAILED}`, a *successful* run was the one kind `gc` could
never reclaim.

Three pieces: `delivery/github.py: pr_state` (the `gh pr view --json state` query),
`steps/complete.py` (Linear -> Done and one comment, through the §16.2 ledger, best-effort),
and `cli.py: cmd_complete` (the shell that refuses unless the run is at `awaiting_human`,
has a `pr_url`, and `gh` says `MERGED`).

**The design decision worth not re-opening by accident.** No tick step auto-completes on
noticing a merge. `merge-is-james` is in `HUMAN_ONLY` and `advance` refuses any actor but
`"human"`, so an automatic version would need §5.3 changed — a re-plan, and James's. The
merge stays James's act on GitHub; the command records it, and what it adds is *evidence*
rather than trust. `pr_state` returns `None` rather than `False` when `gh` cannot answer,
so an offline or unauthenticated `gh` is not silently the same answer as "not merged".

**Proved by mutation, not by a green suite** — the rule at the bottom of this file:

| mutation | what failed |
| --- | --- |
| drop the `!= "MERGED"` refusal | only `test_an_open_pr_is_refused_and_nothing_moves` |
| fold `None` into "merged" | only `test_gh_failing_is_not_the_same_answer_as_not_merged` |
| remove `actor="human"` | four, including the gc property |

**Validated against production, not only the fakes.** All four parked runs were completed
by hand — FRO-7/#45, FRO-6/#41, FRO-10/#39, BAC-4/#66, every one verified `MERGED` by the
real `gh` first. FRO-7's audit row reads
`awaiting_human -> completed [human] merge-is-james`, detail naming the PR.

---

## Do these first

1. **The two §19 one-liners are in the wrong file.** They were pasted into
   `.agents/plans/software-factory-plan.md` — a *second, condensed* rendering of the plan
   (2241 lines, rev 5) — and not into `SOFTWARE-FACTORY-PLAN.md` (3014 lines), which
   `AGENTS.md:4` and this file both name as the authority and which `harness.config.json`
   protects. The re-plan applied earlier went into the authority. **The two documents have
   now drifted in opposite directions.** Decide which is canonical and reconcile; if the
   condensed copy is not canonical, it should say so at the top or be deleted.
   The change is uncommitted in the working tree.
2. **Agent edits to the authority are blocked twice over.** `harness.config.json` protects
   it by design, *and* the Claude Code auto-mode classifier refused both the direct edit and
   the attempt to lift the protection. Applying §19 text is James's keystroke, or needs an
   explicit Bash permission rule. Budget for that — do not plan a session around editing it.

---

## What Phase 5 has left, in order

### the correction that makes it smaller

**Two of §19's six bullets are already closed by measurement, and the previous handoff was
wrong to list them as gaps.** `docs/discovery/p0-3-toolchain.md:49`:

- `node v22.22.1` **and** `uv 0.9.26` are both present in the stock image, so §8.3 resolves
  to its first branch — *"No new Python template. `python.template` in the registry stays
  `null`."* The empty `template = ""` on the python row **is** the measured answer, not an
  unfilled field.
- The kit bullet is conditional — *"If P0-3 found node or `uv` missing"* — and that
  condition is **false**. `kits/python.yaml` and `kits/frontend.yaml` are **not required**.
  Kits stay the fallback if `codex-pnpm:v1` drifts, which is P0-3's own follow-up 1.

### 1. Draft -> ready-for-review (§24.8)

Six lines across two files, and they have to move together or `--dry-run` stops describing
the command that actually runs, which is the only thing that makes it worth reading:

| | |
| --- | --- |
| `delivery/github.py:104` | the `create_pr` docstring |
| `delivery/github.py:114` | `"--draft"` in the argv |
| `delivery/github.py:262` | the PR-body line "opened by the factory as a **draft**" |
| `steps/deliver.py:1` | module docstring |
| `steps/deliver.py:80` | the `--dry-run` preview string |
| `steps/deliver.py:117` | `draft=True` on the structured log line |

This is also what makes `agent-review.yml` fire on open rather than waiting for James to
un-draft. `frontend-harness` has that workflow; `python-harness` does not (§2.1) — closing
that asymmetry is named in the plan as Phase 5's.

**Three test sites pin the current behaviour**, and each must be shown failing against the
*new* intent before it is rewritten — a test edited to match whatever the code now does is
the failure mode this repo has already paid for twice:

| | |
| --- | --- |
| `tests/unit/test_pr_body.py:72` | `assert "draft" in body` |
| `tests/integration/test_phase3.py:56` | `assert "gh pr create --draft" in planned` — the `--dry-run` fidelity check |
| `tests/integration/test_phase3.py:191` | `test_deliver_opens_a_draft_pr_and_announces`, the name and its body |

**A gap worth closing in the same change.** "The factory never merges" is asserted in two
docstrings (`steps/complete.py:3`, `steps/deliver.py:4`) and nowhere else — `grep -rn "pr
merge" src/factory tests` finds no test. Moving PRs to ready-for-review takes the factory one
step closer to that boundary, which makes it the right moment to give the guarantee a test
rather than a sentence.

### 2. The two environment assertions

- **Frontend:** assert `pnpm exec playwright install chromium` has run before an `e2e` gate
  is *required*; otherwise the report says `unavailable`, never `pass`. The caveat machinery
  exists (`verify.collect`'s `env-gate-failed`, Phase 4) but there is no chromium assertion.
  This is the failure mode that killed three FRO-7 runs.
- **Python:** assert Docker is available before a python `integration` gate is required.
  `grep -rn docker src/factory` is still empty. `sbx` gives the VM its own Docker daemon, so
  this is a check, not a blocker.

### 3. First layer-C product

`python3 /Users/james/harness/scripts/new_project.py create <name> --api python --web react
--agnostic`, then a registry row with `stack = "monorepo"`. Not started; `stack` is
`python` / `frontend` only today.

### 4. Tests

One ticket per stack end to end, plus a monorepo dispatch test proving a CSS-only change
runs no Python gate.

---

## The blocker only James can clear

**Phase 5's end-to-end tests need one eligible ticket per stack, and none exists.** Todo
holds only BAC-1 and FRO-1, and both are refused at intake with `no-parent-spec` — correctly
and permanently, because they *are* the parent specs (`parent_identifier` is empty and every
other ticket in their teams names one of them).

Moving a `Canceled` ticket to `Todo` works: BAC-6 did exactly that on 2026-08-23, and all 11
intake conditions then passed, which settled the open question — **a `Canceled` parent does
satisfy the parent-spec condition.** So this is a Linear move, not a code problem. It is not
work to code around: §24.1, the factory reads tickets and never files them.

---

## Open decisions

**Completing a run resets its `gc` clock, and nobody has decided whether that is right.**
`gc.py:97` computes age from `run.updated_at`, and completing *writes* the run — so all four
runs went to `0.0` days old the moment they were completed, and `worktree_days = 7`
(`config/projects.toml:58`) now holds them for a fresh week. FRO-6 had been parked since well
before this session.

- *For keeping it:* the floor is a safety margin after a run becomes collectable, and it only
  becomes collectable at the merge. §16.5's opening line says every rule is a floor, never a
  promise.
- *Against:* the run stopped being worked on at `awaiting_human`. Any late bookkeeping grants
  a fresh week, which is not what "kept for 7 days" reads like.

Changing it means measuring from the `pr_ready -> awaiting_human` transition rather than
`updated_at` — a small change in `_collect_run`, but it changes what §16.5 means, so it is a
decision and not a fix. **Nothing was changed.** Two worktrees are waiting on it:
`python-harness/.factory/worktrees/BAC-4` (the latent one — pytest does not glob into
`.factory/`) and `frontend-harness/.factory/worktrees/FRO-7`. FRO-6's and FRO-10's were
already cleared by hand.

---

## Defects carried into the rest of Phase 5

None block starting. Item 1 of the previous handoff's list is **fixed** — that was this
session's work.

1. **`cancel` cannot restore a tracker it did not set — which is the successful case.** When
   a run opens a PR the GitHub integration moves the ticket to `In Review`, so cancelling any
   run that got as far as a PR leaves the ticket at a non-`Todo` state and the poller skips it
   for ever. It prints `left <T> at '<state>' (not the state the factory set)` and nothing
   says the ticket is now unclaimable.
2. **The re-run ceiling counts orphans, not attempts.** `resumable_reentries` counts
   `<state> -> resumable` transitions, so a deterministic environment failure burns the whole
   budget in three ticks and fails a run whose work is good. An attempt that dies before its
   first heartbeat never ran.
3. **`kill_agent` cannot stop a hung `verifying` gate.** Its body is a node gate report, not
   codex, so neither the old `pkill -f "codex exec"` nor the new `pkill -x codex` matches it.
   Pre-existing, not a regression.
4. `_SENSITIVE_DIRS["frontend"]` matches nothing, and `deliver._archive` returns silently when
   the attempt directory is missing.

## Small follow-ups, deliberately not built

- The §18.5 console has Suspend and Resume controls but no **Complete**. The CLI and the
  console POST path share their machinery elsewhere, so the divergence is worth closing.
- Nothing surfaces "PR merged — ready to complete" in `factory status`. The tick could notice
  it and *say* so without taking the edge, which stays reserved.

---

## The method, unchanged

Every defect in the last three sessions was found by executing the thing, never by reading
about it. Three of the last four lived in code that had tests passing over it: an argv
assertion that pinned four tokens of a command the binary rejects, a fake that wrote the
session id itself before asserting it survived, and a fake modelling a wrapper trap that does
not exist. **A green suite is a claim, not evidence.** Before believing a test, stash or
mutate the source and watch it fail; and check the test sits at the altitude the defect lives
at, not one layer above it.
