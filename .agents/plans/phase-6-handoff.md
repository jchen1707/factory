# Phase 6 — handoff. What the next session inherits

Written 2026-08-24 at the end of the session that closed Phase 5's dangling
follow-ups. Phase 5 is **complete and closed**: both exit tickets merged and
recorded, the layer-C repository exists, every carried decision is resolved,
and the five follow-up PRs from the phase-5 handoff are merged.

**`/SOFTWARE-FACTORY-PLAN.md` is the authority, and it is the only plan file
you may edit.** The verbatim copy at `.agents/plans/software-factory-plan.md`
is generated — never edit it; `scripts/sync_plan_copy.py --check` is in the
suite and in `factory doctor`, so a plan edit that forgets the copy fails the
DoD rather than waiting for a human.

There is one plan file. If you find yourself reading a plan whose path is not
`/SOFTWARE-FACTORY-PLAN.md`, something has gone wrong.

---

## Do this first: merge #57

`main` is at `cdfb131`. **Its mypy DoD is red** — 4 `[attr-defined]` errors in
`tests/unit/test_plan_copy.py`, introduced by **#51** (docs: Phase 5 closes…),
which landed during the close-out session. The factory repo has **no CI mypy
gate** (no `.github/workflows/` runs mypy); the local Stop hook is the only
enforcer, so #51 merged red without anything noticing.

**PR #57** (`fix/plan-copy-mypy-attr-defined`) silences the four dynamic
`ModuleType` attribute injections with `# type: ignore[attr-defined]` — no
behaviour change. Merge it before any Phase 6 edit, or the Stop hook will
refuse the first thing you write. After #57: mypy clean on 75 files, 583
tests passing.

---

## Where things stand

| | |
| --- | --- |
| `factory@main` | `cdfb131` — #51 through #56 merged this session |
| suite | **583 passed**; mypy red on `main` (fixed by #57, unmerged) |
| BAC-6 / FRO-11 | `completed` — both exit tickets merged and recorded |
| `test-project` | created, pushed, `stack = "monorepo"`; **no registry row, deliberately** |
| layer-B `main` | **green again** — `Generate main` succeeds in both repos after the PAT fix |
| runs at `completed` | six: BAC-4, BAC-6, FRO-6, FRO-7, FRO-10, FRO-11 |

---

## What this session closed (the phase-5 handoff's open list)

Every load-bearing item the phase-5 handoff carried is now resolved:

| item | from | closed by |
| --- | --- | --- |
| gc clock resets on `complete` | carried decision | **#50** (landed before this session) |
| host sleep reaps a healthy run as `attempt-orphaned` | defect 1 / §19 defect 1 | **#54** — `poll` no longer reaps a paused run on a stale heartbeat |
| `kill_agent` can't stop a hung `verifying` gate | defect 3 | **#55** — `kill_agent(name, proc="codex")`; `verify` passes `"node"` |
| `blocked_reason` never cleared on exit from `blocked` | defect 4 | **#53** — `record_transition` nulls it on the way out |
| `factory doctor` doesn't check the sbx OpenAI token | small follow-up (highest-value) | **#52** — `_openai_secret_check` in `doctor` |
| nothing surfaces "PR merged — ready to complete" in `factory status` | small follow-up | **#56** — `_ready_to_complete` prints the notice, takes no edge |
| `Generate main` rejects workflow-file pushes (both harness repos) | found this session | **frontend-harness#49** + **python-harness#71** — `WORKFLOW_PAT` checkout; `main` regenerates green |

All five factory PRs (#52–#56) plus #57 are one-defect-per-PR, mutation-tested
(revert → watch the new test fail → restore), suite-green, mypy + ruff clean.

---

## Carried forward into Phase 6 — none of these block it

The phase-5 handoff said its carried items "don't block Phase 6," and that
still holds. Two small ones remain open by design, not by oversight:

1. **The re-run ceiling counts orphans, not attempts** (defect 2). Narrowed by
   `requires` and by #48, but a deterministic environment failure can still burn
   the attempt budget. This is a **budget-semantics decision**, not a mechanical
   fix — whether the ceiling should count orphans or only real attempts is a
   judgement call, so it stayed open. Evidence for Phase 6: collect a real
   env-failure run before changing it.
2. **`deliver._archive` returns silently when the attempt directory is missing**
   (defect 5 tail). Small; a missing archive dir is a symptom, not a cause, and
   the silence hides the symptom. Not fixed because no run has actually lost an
   archive dir yet — fix it when one does, with that run as evidence.

Two more, deliberately left:

3. **The §18.5 console has no `Complete` button.** James chose to skip console
   work this session; the edge exists and has been taken twice via `factory
   complete`. The CLI `factory status` notice (#56) is the non-console answer.
4. **The frontend `playwright` probe writes `/tmp/playwright-probe.png`, so it is
   POSIX-only.** Fails safe (`unavailable`, never a false `pass`). Recorded, not
   fixed — it cannot produce a misleading green.

---

## What Phase 6 is

`SOFTWARE-FACTORY-PLAN.md` §19 Phase 6 is the authority. Summary:

> **Shared layer-A improvements and vendor synchronisation.** Repository:
> `jchen1707/harness@v2`, then both consumers `@v2`. Only changes that Phases
> 1–5 **proved** were needed — each requiring evidence from a real run.

Candidates (each needs real-run evidence before it is written):

- A **`factory` review frame**, if Tier-1 findings show a recurring axis gap.
- A **`gate_report.mjs` flag** for "assert this `when` clause applies", if the
  factory's path-check proves insufficient.
- **`docs/agents/factory.md`** — doctrine for how a repo declares itself
  factory-eligible.

Commands: `python3 scripts/check.py --since=<base>`; then
`python3 scripts/cross_stack.py` to prove a layer-A change does not break
either stack's own gates — the one question neither stack can ask from inside
itself; then `vendor_sync.py sync` in each consumer and commit the bumped pin.

**Rollback:** revert the layer-A PR; re-sync both consumers to the previous sha.
**Human approval boundary:** every layer-A merge.

### Evidence Phase 5 already produced for Phase 6

- **`requires`** is proven in both stacks, in real unattended sandboxes (the
  Docker `requires` probe ran inside BAC-6's `pytest -m integration`; the
  `playwright` `requires` probe ran inside FRO-11). It is not a candidate — it
  shipped in `harness#18`.
- **The two-tier review's Tier 2 has actually run** (FRO-11), not merely been
  triggerable.
- **The §15.3 companion checks** (red-phase replay, test-weakening) have both
  fired in anger and both have an answer.

Read **§19's Phase 5 section** before proposing anything. The **eight-defect
table** there is the argument for how Phase 6 should be *tested*, and it is the
part most likely to be skipped.

---

## Two environment facts a new session must know

1. **There are two independent OpenAI credentials and refreshing either says
   nothing about the other.** `codex login` writes `~/.codex/auth.json`; a
   sandboxed agent authenticates through `sbx`'s proxy against a *globally
   stored* OAuth token (`sbx secret ls` → `(global) service openai (oauth
   configured)`). When the stored token expired in Phase 5, **every sandboxed
   run of both stacks 401'd**, the host looked fine, and the only symptom was a
   `401 token_expired` buried in a per-axis transcript. `sbx secret set openai
   --oauth` is the fix. **New since this session: `factory doctor` now checks
   the token's *presence*** (#52) and fails loudly with that fix if the
   `(global) openai` row is missing — but `sbx secret ls` cannot distinguish a
   live token from an expired one, so **expiry still needs a live probe** (the
   doctor detail line says so). Recreating a sandbox does not refresh the
   credential; it was never in the sandbox.
2. **The `WORKFLOW_PAT` secret** (this session) is a classic PAT with `repo` +
   `workflow` scopes, stored in **both** `python-harness` and `frontend-harness`.
   It is the only thing that lets `generate-main.yml` push a regenerated `main`
   that includes changed workflow files (the default `GITHUB_TOKEN` cannot).
   Rotate it before its 90-day expiry: re-create at github.com/settings/tokens,
   `gh secret set WORKFLOW_PAT --repo jchen1707/python-harness` and the same for
   frontend-harness. The factory repo's `ops/setup-workflow-pat.sh` wizard that
   provisioned it was deleted on James's word — it is a one-off.

---

## Two operational facts

- **The factory repo has no CI.** No `.github/workflows/` runs mypy, ruff or the
  suite. The **Stop hook is the only DoD enforcer** — it runs mypy across all 75
  files and refuses to finish if it is red. That is how #51's mypy break merged
  unnoticed. Treat a green local Stop hook as the bar, not a green PR check.
- **The launchd daemon is running** (`com.jchen.factory`, every 60s). It
  re-intakes any Linear ticket still carrying the `ready-for-agent` label. BAC-4
  and FRO-7 were cancelled this session but keep re-appearing as
  `blocked: state-not-todo` because the label is still on them and they are in
  `Done`, not `Todo`. `factory cancel` removes `needs-info`, not
  `ready-for-agent` — that label is James's signature to start the factory
  (§6.1). To quiet the board: remove the label from those tickets, or
  `launchctl unload ops/com.jchen.factory.plist`.

---

## The method, unchanged

Nothing in this session was found by reading the code first. Every fix came
from running a real ticket or from a live `factory status` / `gh run` / DB
query, and every fix was mutation-tested. Three habits held again:

- **A green suite is a claim, not evidence.** Every fix was reverted, the new
  test watched to fail, and the revert undone. The one mypy regression that
  slipped through (#51) did so because nothing was mutation-testing *mypy
  itself* — it merged red and only the next session's Stop hook would have
  caught it.
- **Check the branch tip, not the PR state.** `git merge-base --is-ancestor
  <sha> origin/main`. Five stranding races in this project so far.
- **Before believing a handoff — including this one — grep the tree it
  describes.** This session found the `Generate main` failure by checking the CI
  the phase-5 handoff never mentioned, and found the mypy red by running `mypy`
  the handoff assumed green.