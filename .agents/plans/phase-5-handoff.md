# Phase 5 — where it stands, and what to do next

Rewritten 2026-08-23, after item 2's mechanism landed in layer A. The previous version is in
git history at `9cf57d2`.

**§19 Phase 5 in `SOFTWARE-FACTORY-PLAN.md` is the authority.** Everything here either points
at it or records a fact measured on 2026-08-23 that §19 does not carry.

---

## Read this before trusting any file path in this repo

**There are two plan files, and one of them is stale.**

| | |
| --- | --- |
| `SOFTWARE-FACTORY-PLAN.md` (repo root, 179k) | **the authority.** `AGENTS.md:4` names it; `harness.config.json` protects it |
| `.agents/plans/software-factory-plan.md` (169k) | **a stale condensed copy.** Last written 2026-08-23 01:24, before the session's corrections |

macOS is case-insensitive, so `SOFTWARE-FACTORY-PLAN.md` and `software-factory-plan.md` look
like the same file and are not. This session read the stale copy first and concluded that the
`agent-review.yml` corrections had never been applied — they had, to the authority. The
previous handoff flagged this drift as "do this first"; it is still unreconciled, and it has
now cost two sessions. **Decide: delete the condensed copy, or put a one-line "NOT CANONICAL —
see /SOFTWARE-FACTORY-PLAN.md" header on it.** It is not a protected file, so an agent can do
either on your word.

---

## Where things stand

| | |
| --- | --- |
| `main` | `1abfc83`, **pushed**. Item 1 merged as factory#32 |
| suite | 533 passed, mypy clean on 71 files, ruff check + format clean |
| open PR (factory) | **#33** — `feat/unavailable-gate-names-its-requirement`, pushed, ready. 3 commits: the fix, this handoff, the §19 edit |
| open PR (harness) | **#18** — `feat/gate-requires-probe` on `v2`, pushed, ready. **Yours to merge** |
| `awaiting_human` | empty |
| runs | 4 `completed`, 30 `cancelled`, 8 `blocked`, 1 `suspended` |
| BAC-6 | `suspended` at attempt 7, deliberately held. Do not touch |
| daemon | loaded, ticking every 60 s, running `main` |

---

## What landed this session

### Item 1 is closed

factory#32 merged. `main` carries it. Nothing left here.

### Item 2 — the two environment assertions: the mechanism is built

**The design tension the previous handoff named is resolved, and the reasoning changed on one
point worth keeping.** That handoff framed option (a) as "a machine-readable field in
`harness.config.json` plus a generic probe in `steps/sandbox.py: preflight`". The field is
right; **the preflight is the wrong home**, and reading §19's own sentence is what settles it:

> otherwise the report says `unavailable`, not `pass`

Only layer A produces that report. `preflight` can record a ledger check by name — it cannot
make a gate come back `unavailable`. So the probe went to `gate_report.mjs`.

**harness#18 — `requires`.** An optional argv beside `caveat`, run immediately before the gate
and only when the gate was going to run anyway. Non-zero or unspawnable, and the gate is
reported `unavailable` and **never runs**, feeding `verdict: incomplete` unchanged.

```json
{ "name": "playwright", "kind": "e2e", "run": ["pnpm", "test:e2e"],
  "requires": ["pnpm", "exec", "playwright", "--version"],
  "caveat": "needs browsers installed: pnpm exec playwright install chromium." }
```

Three judgments, each pinned by a test, each mutation-proved:

- The probe runs **after** `disabled` / `skipped_unchanged` / `not_applicable`. Probing a gate
  those already settled spends a subprocess on a question nobody asked — and on the opt-in
  kinds that is the common case, not the rare one.
- An **unspawnable probe is unmet, not met**. The probe is the cheaper of the two commands; if
  it cannot start, the gate's toolchain is not there either.
- A **met requirement leaves `fail` alone**, so real code defects still reach the agent.
  Without that, this hides the failures it exists to distinguish itself from.

`probeGate` is injected beside `runGate` and defaults to met, so every config with no
`requires` behaves exactly as before. `verify.mjs` needed no change — `STOP_KINDS` excludes
`e2e`/`integration`, so the Stop hook never runs an opt-in gate.

Six mutations, all caught: removing the probe block, ignoring its result, treating an
unspawnable probe as met, carrying the probe's exit code onto the entry, probing an empty
`requires`, and hoisting the probe above the dispatch decisions.

**factory#33 — the block message names what to install.** `gates-incomplete` is a
human-judgement state and the human's next act is to install something; the message named the
gate and left the *what* in the artifact. It now carries the caveat, attached to its gate, and
only for gates that did not run. This matters more now: a missing tool used to arrive as
`fail` and now arrives as `unavailable`, so this is the message it actually reaches.

**`src/factory/` needed no other change, and that was verified rather than assumed.**
`unavailable` → `verdict: incomplete` → `Blocked("gates-incomplete")`, which blocks and does
*not* loop back to the agent (`steps/verify.py:327`). That is already the wanted behaviour.

---

## What item 2 has left — this is the next session's first job

**Everything remaining is downstream of `harness#18` merging.** Nothing below can be done
before it, because adding `requires` to either config would fail validation against the
vendored schema.

1. **Merge `harness#18`** (yours — §19 reserves every layer-A merge).
2. **Re-vendor into both consumers**, one at a time: `python3 /Users/james/harness/scripts/vendor_sync.py sync`
   in the consumer, then commit the bumped pin. Current pin in both is `3248fbe`.
3. **Add the two `requires` argvs**, in the consumer repos, on `v2`:

   | repo | gate | `requires` |
   | --- | --- | --- |
   | `frontend-harness` | `playwright` | `["pnpm", "exec", "playwright", "--version"]` |
   | `python-harness` | `pytest -m integration` | `["docker", "info"]` |

   **Check the python one against the real config first.** `python-harness`'s integration
   caveat says "needs Docker **and the app extra**", and `docker info` probes only the first
   half. Either the probe covers both or the caveat should stop claiming it does.
4. **Prove it end to end, not in a fixture.** The method section below is not decoration: run
   `gate_report.mjs --json --gate playwright` in the frontend sandbox with chromium *actually
   absent*, and read the status. That is the only evidence that the mechanism does what four
   green unit tests claim.

---

## The authority edit — done

§19 carries the decision. James ran the prepared script on 2026-08-23; the two edits are in
`0ad42cb` on this branch: the Phase 5 bullet gained a paragraph recording that the mechanism
is layer A's `requires` and why not `src/factory/` or `preflight`, and §20.2's harness file
table gained the schema's second optional key.

The route is recorded because it works and cost one keystroke instead of a session: write the
edits as a script that asserts each FIND string appears **exactly once** before touching
anything — all-or-nothing, backs up first, no-op on re-run — validate it against a *copy* of
the file, then hand James one `! python3 <path>` line.

Do **not** try to route around the hook or the classifier. Silently defeating the enforcement
layer is exactly `p0-6-codex-trust.md`.

---

## What Phase 5 has left after item 2

### 3. First layer-C product

`python3 /Users/james/harness/scripts/new_project.py create <name> --api python --web react
--agnostic`, then a registry row with `stack = "monorepo"`. Not started; `stack` is `python` /
`frontend` only today.

### 4. Tests

One ticket per stack end to end, plus a monorepo dispatch test proving a CSS-only change runs
no Python gate. **Blocked — see below.**

---

## The blocker only James can clear

**Phase 5's end-to-end tests need one eligible ticket per stack, and none exists.** Todo holds
only BAC-1 and FRO-1, and both are refused at intake with `no-parent-spec` — correctly and
permanently, because they *are* the parent specs (`parent_identifier` is empty and every other
ticket in their teams names one of them).

Moving a `Canceled` ticket to `Todo` works: BAC-6 did exactly that on 2026-08-23 and all 11
intake conditions passed, which settled the open question — **a `Canceled` parent does satisfy
the parent-spec condition.** So this is a Linear move, not a code problem, and not work to code
around: §24.1, the factory reads tickets and never files them.

`needs-info` is currently **cleared** by James. Item 2's remaining steps need no ticket, so
this does not block them.

---

## Open decisions

**Completing a run resets its `gc` clock, and nobody has decided whether that is right.**
Carried forward unchanged — it is a decision, not a bug, and **nothing was changed**.

`gc.py:97` computes age from `run.updated_at`, and completing *writes* the run, so all four
completed runs went to `0.0` days old and `worktree_days = 7` (`config/projects.toml:58`) holds
them for a fresh week.

- *For:* the floor is a safety margin after a run becomes collectable, and it only becomes
  collectable at the merge. §16.5's opening line says every rule is a floor, never a promise.
- *Against:* the run stopped being worked on at `awaiting_human`. Late bookkeeping granting a
  fresh week is not what "kept for 7 days" reads like.

Changing it means measuring from the `pr_ready -> awaiting_human` transition rather than
`updated_at` — small in `_collect_run`, but it changes what §16.5 *means*. Two worktrees wait
on it: `python-harness/.factory/worktrees/BAC-4` (latent — pytest does not glob into
`.factory/`) and `frontend-harness/.factory/worktrees/FRO-7`.

---

## Defects carried into the rest of Phase 5

None block the remaining item-2 steps. All four unchanged.

1. **`cancel` cannot restore a tracker it did not set — which is the successful case.** A run
   that opened a PR has been moved to `In Review` by the GitHub integration, so cancelling it
   leaves the ticket at a non-`Todo` state and the poller skips it for ever. It prints
   `left <T> at '<state>' (not the state the factory set)` and nothing says the ticket is now
   unclaimable.
2. **The re-run ceiling counts orphans, not attempts.** `resumable_reentries` counts
   `<state> -> resumable` transitions, so a deterministic environment failure burns the budget
   in three ticks and fails a run whose work is good. An attempt that dies before its first
   heartbeat never ran. **`requires` narrows this but does not close it** — a missing tool now
   blocks at `gates-incomplete` instead of looping, but every other deterministic environment
   failure still burns the budget.
3. **`kill_agent` cannot stop a hung `verifying` gate.** Its body is a node gate report, not
   codex, so neither `pkill -f "codex exec"` nor `pkill -x codex` matches it. Pre-existing.
4. `_SENSITIVE_DIRS["frontend"]` matches nothing, and `deliver._archive` returns silently when
   the attempt directory is missing.

## Small follow-ups, deliberately not built

- The §18.5 console has Suspend and Resume but no **Complete**. The CLI and the console POST
  path share their machinery elsewhere, so the divergence is worth closing.
- Nothing surfaces "PR merged — ready to complete" in `factory status`. The tick could notice
  and *say* so without taking the edge, which stays reserved to `merge-is-james`.
- `.DS_Store` and `.agents/.DS_Store` are untracked in the working tree and probably want a
  `.gitignore` line.

---

## The method, unchanged — and it paid again this session

Every defect in the last five sessions was found by executing the thing, never by reading about
it. This session it caught three things the handoff would otherwise have carried forward:

- **The previous handoff's "first thing to decide" was already decided.** It said the item-1
  branch was "local and unpushed, no PR". `gh pr list` said PR #32, `MERGED`. One command.
- **The stale plan duplicate.** Reading `.agents/plans/software-factory-plan.md` produced a
  confident, wrong conclusion that the `agent-review.yml` corrections had never landed. `git
  show --stat` on the commit named a different filename, which is the only reason it surfaced.
- **The handoff's own design recommendation was half wrong.** `preflight` is genuinely the
  shaped home for a positive assertion — and it still cannot satisfy the bullet, because the
  bullet is about the *report*. Re-reading the authority beat re-reading the summary of it.

**A green suite is a claim, not evidence.** Before believing a test, stash or mutate the source
and watch it fail; check the test sits at the altitude the defect lives at. Watch for the
vacuous assertion too — this session's "a passing gate contributes no caveat noise" passed
before the fixture was changed to make it capable of failing.

**And before believing a handoff — including this one — grep the tree it describes.**
