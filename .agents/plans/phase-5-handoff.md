# Phase 5 — where it stands, and what to do next

Rewritten 2026-08-23, after §19 Phase 5's item 1 landed. The previous version (written after
`factory complete` landed) is in git history at `df057b0`; everything it listed under "Do
these first" is settled and recorded below.

**§19 Phase 5 in `SOFTWARE-FACTORY-PLAN.md` is the authority.** Everything here either points
at it or records a fact measured on 2026-08-23 that §19 does not carry.

---

## Where things stand

| | |
| --- | --- |
| `main` | `5ee99c2` + two docs commits, **pushed** |
| `feat/pr-opens-ready-for-review` | `8f7b9f6`, **local and unpushed**, two commits — item 1. No PR |
| suite | 532 passed, mypy clean on 71 files, ruff clean |
| `awaiting_human` | **empty** |
| runs | 4 `completed`, 30 `cancelled`, 8 `blocked`, 1 `suspended` |
| BAC-6 | `suspended` at attempt 7, deliberately held. Do not touch |
| daemon | loaded, ticking every 60 s, running `main` |

**First thing to decide:** the branch above is unpushed and has no PR. It is the factory's own
repo, so the merge is James's the same way every other one is.

---

## What landed this session

### §19 Phase 5 item 1 — the PR opens ready for review, not as a draft (§24.8)

`69e7ead`. The previous handoff tabled six lines; `grep -rni draft` found **ten**. The four it
missed were all user-facing text that would have gone on describing a draft the argv no longer
asks for: three `cli.py` prints, the `awaiting_human` Linear comment in `block.py`, a `review.py`
comment, and two runbook paragraphs. *Grep the word, not the table.*

**The dry-run fidelity property is a test now, not a convention.**
`test_the_dry_run_preview_names_the_flags_gh_pr_create_actually_carries` measures **both**
halves — the argv from a captured `create_pr`, the preview from a real dry run — and asserts the
flag sets are equal. Proved by mutation: removing `--draft` from the preview alone fails it with
`Extra items in the left set: '--draft'`. It is an invariant, so it stays green through the
change; that is why it had to be mutation-proved rather than trusted.

All three rewritten tests were shown red against the new intent before they were touched.

### "The factory never merges" has a test

`test_the_factory_never_merges_a_pull_request` reads the set of `gh` verb pairs off `src/` and
compares it against an allow-list of five — derived, not enumerated, so an unaccounted verb fails
rather than passing unnoticed. A second test closes the other spelling of the same act,
`gh api --method PUT .../pulls/{n}/merge`, where `merge` is a path segment and there is no argv
token to grep. Both mutation-proved, along with an unlisted `gh pr close`.

**A correction to the previous handoff:** it claimed `grep -rn "pr merge" src/factory tests`
found no test. The pre-existing `test_no_forbidden_git_or_gh_argument_appears` *did* already catch
the argv form — it spells the token `'"merge"'`, which that grep could not match. The gap was
narrower than stated; the REST route and the open verb surface were the parts genuinely uncovered.

### §2.1's `agent-review.yml` asymmetry does not exist, and draft was never a trigger

`8f7b9f6`, applied to the authority by James. **Measured 2026-08-23, so nobody re-derives it:**

- `python-harness` **has** `.github/workflows/agent-review.yml`, stack-correct with the `BAC`
  branch pattern. It landed 2026-08-21 in `bc802ca` as a Phase 3 prereq. §2.1 and three "**New**"
  file rows in §11/§19 were stale and now say landed.
- Both harnesses' copies are `on: pull_request: types: [labeled]` behind
  `if github.event.label.name == 'agent-review'` — **label-gated on purpose**, because it is
  billed model spend. Un-drafting never fired it.
- `ci.yml` in both is a bare `on: pull_request:` with no type filter, so it ran on drafts already.
- Nothing in either `.github/` mentions `ready_for_review` or `draft`.

So §19's "which is also what makes `agent-review.yml` fire on open" was false twice over. The
bullet now carries the real reason: "draft" is GitHub's word for work in progress, and by delivery
the gates are green and the two-tier review is clean; a draft takes no review request and cannot
be merged, so un-drafting was a manual step carrying no information in front of two acts that are
James's regardless.

**Deliberately left alone:** the "draft PR" in §19's Phase 3 and 4.5 *evidence* rows
(`frontend-harness#45`, the `awaiting_human` transition log). Those record runs that really did
open drafts. Rewriting measured evidence to match today's code is the failure this plan's own
method section warns about. §19's Phase 3 section still says "opened as a **draft** in this
phase", which is correctly scoped and left as written.

### How to edit the authority without burning a session on it

The previous handoff said to budget for this. **The route that works:** write the edits as a
Python script to the scratchpad that asserts each FIND string appears **exactly once** before
touching anything — all-or-nothing, backs up first, no-op on re-run — then hand James one
`! python3 <path>` line. He runs it; that is the keystroke the protection is asking for, and it
costs him one command instead of N find-and-replaces. Do **not** try to route around the hook or
the classifier: silently defeating the enforcement layer is exactly `p0-6-codex-trust.md`.

---

## What Phase 5 has left, in order

### 2. The two environment assertions ← **next**

§19: assert the tool is present **before** an opt-in gate is *required*, so the report says
`unavailable` rather than running and failing.

- **Frontend:** `pnpm exec playwright install chromium` has run. This is the failure mode that
  killed three FRO-7 runs.
- **Python:** Docker is available before a python `integration` gate is required. `grep -rn docker
  src/factory` is still empty. `sbx` gives the VM its own Docker daemon, so this is a check, not a
  blocker.

**Two facts that shape this, both read on 2026-08-23 — start here rather than re-deriving:**

1. **The existing caveat machinery is reactive, and this item is not.** `verify.collect`'s
   `env-gate-failed` (`steps/verify.py:284`) fires *after* the gate ran and failed, when every
   failing gate carries a `caveat`. Item 2 wants the assertion *before* the gate is required. The
   two are complementary; do not mistake one for the other.
2. **`steps/sandbox.py: preflight` is the shaped home for it.** It is already a list of positive
   assertions (`preflight(ctx, spec)`, `sandbox.py:111`), check 1 is literally a toolchain probe,
   and each check is recorded to the ledger by name. Adding two more fits without inventing a
   mechanism.

**The design tension to resolve before writing code — this is the whole difficulty.** AGENTS.md's
ownership rule says the factory holds no gate command, and that *"a gate name appearing anywhere
in `src/` is a review failure; `uv` and `pnpm` appear only as expectations to cross-check"*. So
`docker` and `chromium` must not be hardcoded into `src/factory/`.

The target repos already declare the condition — but in **prose**:

| repo | gate | `caveat` |
| --- | --- | --- |
| `python-harness` | `pytest -m integration` | "needs Docker and the app extra." |
| `frontend-harness` | `playwright` | "needs browsers installed: pnpm exec playwright install chromium." |

Prose is not probeable. The two honest options:

- **(a) Add a machine-readable field to `harness.config.json`** — a `requires` or `probe` argv
  beside the existing `caveat` — and have the preflight run whatever the config names. Generic
  mechanism in layer D, stack-specific data in layer B. **This is the one that respects the
  ownership rule**, and it means the change is layer A/B *and* factory, not factory alone. Budget
  for a `harness@v2` plugin bump plus a re-vendor into both consumers.
- **(b) Hardcode the two probes in `src/factory/`.** Faster, and a review failure by this repo's
  own stated rule. Named here only so nobody rediscovers it as an idea.

Whichever is chosen, §19 needs a line saying so — use the script route above.

### 3. First layer-C product

`python3 /Users/james/harness/scripts/new_project.py create <name> --api python --web react
--agnostic`, then a registry row with `stack = "monorepo"`. Not started; `stack` is `python` /
`frontend` only today.

### 4. Tests

One ticket per stack end to end, plus a monorepo dispatch test proving a CSS-only change runs no
Python gate. **Blocked — see below.**

---

## The blocker only James can clear

**Phase 5's end-to-end tests need one eligible ticket per stack, and none exists.** Todo holds only
BAC-1 and FRO-1, and both are refused at intake with `no-parent-spec` — correctly and permanently,
because they *are* the parent specs (`parent_identifier` is empty and every other ticket in their
teams names one of them).

Moving a `Canceled` ticket to `Todo` works: BAC-6 did exactly that on 2026-08-23 and all 11 intake
conditions passed, which settled the open question — **a `Canceled` parent does satisfy the
parent-spec condition.** So this is a Linear move, not a code problem, and not work to code
around: §24.1, the factory reads tickets and never files them.

`needs-info` is currently **cleared** by James. Item 2 needs no ticket at all, so this does not
block starting.

---

## Open decisions

**Completing a run resets its `gc` clock, and nobody has decided whether that is right.** Carried
forward unchanged — it is a decision, not a bug, and **nothing was changed**.

`gc.py:97` computes age from `run.updated_at`, and completing *writes* the run, so all four
completed runs went to `0.0` days old and `worktree_days = 7` (`config/projects.toml:58`) holds
them for a fresh week.

- *For:* the floor is a safety margin after a run becomes collectable, and it only becomes
  collectable at the merge. §16.5's opening line says every rule is a floor, never a promise.
- *Against:* the run stopped being worked on at `awaiting_human`. Late bookkeeping granting a fresh
  week is not what "kept for 7 days" reads like.

Changing it means measuring from the `pr_ready -> awaiting_human` transition rather than
`updated_at` — small in `_collect_run`, but it changes what §16.5 *means*. Two worktrees wait on
it: `python-harness/.factory/worktrees/BAC-4` (latent — pytest does not glob into `.factory/`) and
`frontend-harness/.factory/worktrees/FRO-7`.

---

## Defects carried into the rest of Phase 5

None block starting. All four are unchanged from the previous handoff.

1. **`cancel` cannot restore a tracker it did not set — which is the successful case.** A run that
   opened a PR has been moved to `In Review` by the GitHub integration, so cancelling it leaves the
   ticket at a non-`Todo` state and the poller skips it for ever. It prints `left <T> at '<state>'
   (not the state the factory set)` and nothing says the ticket is now unclaimable.
2. **The re-run ceiling counts orphans, not attempts.** `resumable_reentries` counts
   `<state> -> resumable` transitions, so a deterministic environment failure burns the budget in
   three ticks and fails a run whose work is good. An attempt that dies before its first heartbeat
   never ran. **Note this one is adjacent to item 2** — a missing chromium is exactly such a
   deterministic environment failure.
3. **`kill_agent` cannot stop a hung `verifying` gate.** Its body is a node gate report, not codex,
   so neither `pkill -f "codex exec"` nor `pkill -x codex` matches it. Pre-existing.
4. `_SENSITIVE_DIRS["frontend"]` matches nothing, and `deliver._archive` returns silently when the
   attempt directory is missing.

## Small follow-ups, deliberately not built

- The §18.5 console has Suspend and Resume but no **Complete**. The CLI and the console POST path
  share their machinery elsewhere, so the divergence is worth closing.
- Nothing surfaces "PR merged — ready to complete" in `factory status`. The tick could notice and
  *say* so without taking the edge, which stays reserved to `merge-is-james`.
- A stale `type: ignore` in `test_sbx.py:421` was failing mypy on `main` before this session and is
  cleared in `69e7ead`. Mentioned only so it is not mistaken for part of item 1.

---

## The method, unchanged — and it paid again this session

Every defect in the last four sessions was found by executing the thing, never by reading about it.
This session the previous handoff was wrong on three counts, and each was caught by running
something: the six-line table (it was ten), the missing merge test (it partly existed), and the
`agent-review.yml` asymmetry and its whole justification (neither existed).

**A green suite is a claim, not evidence.** Before believing a test, stash or mutate the source and
watch it fail; check the test sits at the altitude the defect lives at, not one layer above it.
**And before believing a handoff — including this one — grep the tree it describes.**
