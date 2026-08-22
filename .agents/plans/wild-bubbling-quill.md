# Phase 3 — review, PR, Linear updates, artifact collection

## Context

Phase 2 is validated and merged: `factory run BAC-5` reaches `reviewing` through real
running gates (PR #8 merged, `main` @ `5b5a36d`). The factory drives a ticket
`approved → … → reviewing` and stops. **Phase 3 adds the second half: `reviewing → pr_ready
→ awaiting_human`** — a read-only reviewer sandbox (Tier 1 always, Tier 2 when warranted),
the red-phase replay guard, host-side push + draft PR, Linear "In Review" + one comment per
state, and artifact collection. The factory still never merges (`gh pr merge` is denied and
unit-tested); the PR opens as a **draft** and James marks it ready.

Scope was confirmed with James: **build the full Phase 3 factory core + the `--clone` path +
both consumers' layer-B prereqs**, and validate against both a python ticket (`BAC-<n>`) and a
frontend ticket (`FRO-<n>`). The `--clone` path is §19 Phase 7 scope pulled forward; its design
is the plan's: in-container clone wired back to the host via a `sandbox-<name>` git remote.

Layer-A schema work is **already done** (harness@v2 @ `7c13a20`): `review-findings.schema.json`
exists, `full-review.js` reads it from the file, `harness.config.schema.json` has the `tests`
key, and the portable `skills/full-review/SKILL.md` exists. No harness PR needed this phase.

## Build order

Everything in `factory@main` is built and tested with fakes first (no external blocker). The
layer-B PRs (James merges) land in parallel; the real-run validations come last.

### 1. `factory@main` core steps (stack-agnostic, fakes)

| File | Change |
| --- | --- |
| `src/factory/steps/redphase.py` | **NEW** — the red-phase replay + test-weakening guard (§15.3). Runs at the **REVIEWING** entry (called by `review.py` before the review), when `behaviour_changed`. §15.3 says "run in `verifying`" but the machine makes `verifying → awaiting_human` illegal while §15.3's `escalate` option routes to `awaiting_human` — the machine is the reachability authority, so the replay runs in `reviewing` where `blocked`/`awaiting_human`/`proceed` are all legal; `verify.py` stays unchanged. Scratch worktree at base ref, apply only the test half of the diff (via `harness.config.json` `tests` pathspecs), run the repo's `kind: test` gate, require it to FAIL naming a new/changed test. Outcomes: pass→`test-proves-nothing` Blocked, no test files+behaviour_changed→`behaviour-change-without-test` Blocked (both non-configurable); fails-naming-new-test→proceed to review; inconclusive (import/collection error)→`redphase.inconclusive` config (report default / escalate→awaiting_human / block); `tests` key absent→`unavailable` reported. Test-weakening guard: a diff that deletes/relaxes assertions in existing tests → `awaiting_human` (escalate, not block). |
| `src/factory/steps/review.py` | **NEW** — Tier 1 + Tier 2 (§15.2). Tier 1: in `factory-review-<project>` (workspace `:ro`, `sandbox_mode="read-only"`), Standards + Spec independently, prompt **assembled** from `.agents/vendor/harness/agents/<frame>.md` + `docs/agents/subagents/<checklist>.md` via `review.agentDir` (throw if either resolves to nothing, matching layer A). Spec-checker resolves the ticket itself — no summary given. Tier 2: the portable `full-review` SKILL.md, only when a trigger rule fires (≥10 files / ≥400 lines / protected path / `src/app/ai/**` / Tier-1 critical-or-high / Bug label without test); else skipped and PR body names the rule. Reviewers never repair — findings go back as a new implementer turn (not this phase's deliver; loop to `implementing` when critical/high). |
| `src/factory/steps/deliver.py` | **NEW** — `pr_ready` entry action: host-execution guard (`policy.host_execution_verdict` over `repo.changed_paths`), then push + draft PR. `blocked` on vendored-tree edit; `awaiting_human` on a deny-list path (§17.4). Transitions `pr_ready → awaiting_human` on success. |
| `src/factory/delivery/github.py` | **NEW** — `gh` wrapper: `gh pr list --head` (duplicate guard, F16 → edit not create), `gh pr create --draft`, `gh pr edit`. PR body assembled from the template + evidence. Push via `git -c core.hooksPath=/dev/null push -u origin` (§17.4). Never `gh pr merge`/`review`/`--force` (unit-test the refusal). |
| `src/factory/templates/pr_body.md.j2` | **NEW** — §13.2 evidence layout, in order: `Fixes <TEAM-NUM>`, one-para restatement, full gate table (every `not_applicable`/`unavailable`/`skipped` row + caveat), review summary (Tier-1 findings + skipped-Tier-2 rule name), red-phase replay result, out-of-scope, artifact path, cost. |
| `src/factory/artifacts.py` | **EXTEND** — review artifacts (`review-standards.json`, `review-spec.json`, `review-full.json`) into the attempt dir; `manifest.json` already exists; secret-scan/quarantine already exist (F18). Wire `archive` of the review attempt. |
| `src/factory/cli.py` | **EXTEND** `_drive` to run `redphase` (inside verify's pass branch, §15.3 "Run in `verifying`") then `review` then `deliver`. Update the stale "Review and the pull request arrive in Phase 3" final message. Add `pr_url` to `update_run` allow-list (already present). |
| `src/factory/machine.py` | No change — `REVIEWING`/`PR_READY`/`AWAITING_HUMAN` states + transitions already exist. Re-verify `assert_table_is_sound`. |
| `schemas/` | Add `review_result.schema.json` (reference, not copy, to vendored `review-findings.schema.json`) for `--output-schema`. |

**Tests** (extend `tests/integration/conftest.py` fakes + `tests/integration/test_pipeline.py`):
- F27/F28/F29/F30 redphase outcomes (test-proves-nothing blocks; behaviour-change-without-test blocks; weakened-assert escalates; `tests` absent → unavailable).
- Tier-1 review runs in a `:ro` review sandbox with `sandbox_mode=read-only`; prompt assembled from layer-A frame + repo checklist; throws if a frame is missing.
- Tier-2 skipped rule named in PR body when no trigger fires; runs when Tier-1 returns high.
- F1/F2 crash-injection between review and deliver (effects ledger reconciles, one Linear comment per state).
- PR-body golden test (§13.2 order + gate table + skipped-Tier-2 rule + artifact path + cost).
- F16 duplicate PR → `gh pr edit`, not a second create.
- F11 host-exec guard → `awaiting_human` before push; vendored-tree edit → `blocked`.
- F18 secret in transcript → artifact quarantined, run failed, Linear comment says "rotate".
- `factory run --dry-run` prints the review/deliver commands.
- Unit test: `gh pr merge`/`review`/`--force` refusal (grep `src/`).

### 2. `--clone` path (§19 Phase 7, pulled forward)

`SandboxSpec.clone` + `create_argv --clone` already exist. Remove the `clone-not-implemented`
block in `sandbox.py:build_spec`. Design (plan §19 Phase 7): in-container clone wired back via
a `sandbox-<name>` git remote — the host fetches the agent's branch out of the VM clone through
an `ext::sbx exec <sandbox> git …` transport, then pushes from the host. The host stays the
push authority; the sandbox gets no origin credential (§13.2 holds).

Reorder for clone projects: **worktree before sandbox** is *not* viable (clone copies at
creation). Instead the VM clone is the workspace; the host main checkout fetches the branch
back. Resolve the filesystem-protocol gap (`.factory/run/<attempt>` lives in the VM clone; host
reads via `sbx exec cat`): add a `SandboxAdapter` path for clone projects that reads
attempt files through `sbx exec`. This is the riskiest piece — implement after the core is green
and iterate.

### 3. Layer-B PRs (James merges)

**python-harness@v2** (branch `feat/BAC-<n>-agent-review-workflow`):
- `.github/workflows/agent-review.yml` — NEW, mirror of frontend's, branch regex `BAC`.
- `.github/PULL_REQUEST_TEMPLATE.md` — NEW, adapt frontend's (BAC, `Fixes BAC-123`).
- `harness.config.json` — add `.factory/**` to `hooks.protected`; add `"tests": ["tests"]`.
- `docs/agents/issue-tracker.md` — already present; verify the control-plane-owns-writes paragraph.

**frontend-harness@v2** (branch `chore/FRO-<n>-agents-under-dot-agents`) — the §24a layout fix:
- `git mv .claude/agents/{a11y-reviewer,test-writer}.md .agents/agents/`
- `git mv .claude/skills/{delivery,preflight} .agents/skills/` (de-dup; `.agents/` authoritative)
- replace `.claude/agents` + `.claude/skills` with symlinks → `../.agents/…` (git mode 120000)
- `harness.config.json` — `review.agentDir` → `.agents/agents`; `.factory/**` in protected; `tests` pathspecs
- verify `generate_main.py` materialises symlinks as real trees (mechanical check in `/Users/james/harness`)

### 4. Validation (after James merges the layer PRs)

```sh
uv run factory run BAC-<n>          # small python ticket → draft PR
uv run factory run FRO-<n>          # small frontend ticket (--clone) → draft PR
gh pr view <n> --repo jchen1707/python-harness --json body,labels
uv run factory status FRO-<n> --evidence
```

Expected: draft PR body with `Fixes <TEAM-NUM>`, full gate table, Tier-1 findings, skipped-Tier-2
rule, red-phase result, artifact path, cost; Linear In Review with exactly one factory comment
per state. Run `factory run` in the **background** (`nohup … > logs/<t>-run.log 2>&1 &`) — the
foreground-orphan footgun (memory: [[sbx-exec-d-does-not-detach]]).

## Verification

- `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest` — the four
  gates, green before any PR. Every new behavior carries a test run against unfixed-first where
  it applies (memory: [[prove-tests-fail-without-the-fix]]).
- `factory run <TICKET> --dry-run` prints the full reviewing→pr_ready→awaiting_human command chain.
- Real `factory run BAC-<n>` and `factory run FRO-<n>` reach `awaiting_human` with a draft PR open.

## Carry-over footguns (memory)

- `factory run` in foreground Bash orphan-prone → background/nohup.
- `factory cancel` `-D`s the recorded unpushed branch → push/tag before cancelling a run with work.
- `protect_paths.mjs` over-blocks new files in `docs/discovery/**` → temp-remove + restore net-zero.
- Layer-A changes: land on `harness@v2`, vendor-sync via `vendor_sync.py`, commit only `.agents/vendor/**`.
- Two James-pending actions still open: mark BAC-3 Done in Linear (does not block).

## `--clone` design (task #5 — deferred to a real-sandbox calibration session)

**Verified `sbx create --clone` semantics** (from `sbx create codex --help`): the VM runs on a
private in-container clone of the host Git repo (mounted read-only) instead of bind-mounting
the workspace; **the agent's commits are accessible on the host via a `sandbox-<name>` git
remote.** The `SandboxSpec.clone` field + `create_argv --clone` plumbing already exist; the
blocker is `sandbox.py:build_spec` raising `clone-not-implemented`.

**Why it can't be built blind:** the §4.2 filesystem-protocol assumption (the attempt dir at
`<worktree>/.factory/run/<attempt>/` is a host path both sides address identically) breaks for
clone projects — the clone is read-only, so the attempt dir must move to a separate `rw` host
mount, and the agent's code commits (in the VM clone) must be fetched back to the host via the
`sandbox-<name>` remote. Building that without a real `sbx create --clone` run to confirm the
remote appears + the fetch works + the `rw` attempt mount works repeats the P0 "verified the
flag was accepted, not what it did" trap.

**Calibration session (do first, before coding):**
1. `sbx create codex --clone --name factory-build-frontend-harness /Users/james/frontend-harness`
   → confirm the `sandbox-factory-build-frontend-harness` remote appears on the host
   (`git -C /Users/james/frontend-harness remote -v`).
2. `sbx exec factory-build-frontend-harness git -C /Users/james/frontend-harness checkout -b calib origin/v2`
   + a dummy commit in the VM → `git -C /Users/james/frontend-harness fetch sandbox-factory-build-frontend-harness calib`
   → confirm the commit lands on the host.
3. Add a `rw` host-path mount (e.g. a tmp dir) to the clone sandbox → confirm the VM can write
   to it (this is where the attempt dir will live).
4. `sbx rm factory-build-frontend-harness` to clean up.

**The flow to build (after calibration), all behind `requires_clone`:**
- **No host worktree.** The branch is created in the VM clone (`sbx exec … git checkout -b <branch> origin/<base>`) before the agent runs. `worktree.py` becomes a no-op or a "create branch in VM" step for clone projects.
- **Attempt dir on a `rw` host mount.** `implement.py`'s `AttemptDir` moves to a host `state/runs/<id>/attempt-<n>/` mounted `rw` into the clone sandbox (the clone is `:ro`). The codex wrapper writes events/exit/last-message there; the host polls as today.
- **Fetch-back on deliver.** After implement+verify, the host fetches the agent's branch from `sandbox-<name>` (`git -C <repo> fetch sandbox-<name> <branch>`), creates a local branch, and `deliver.py` pushes THAT to origin + opens the PR. The host keeps the push authority; the VM has no origin credential (§13.2 holds).
- **Redphase + review** run against the fetched-back host branch (the host has the agent's commit after the fetch-back, so a host-side scratch worktree at `origin/v2` + the test-half patch works; the review sandbox bind-mounts the host worktree `:ro` as today, NOT a clone). So redphase/review/deliver stay on the host-side branch path; only implement+worktree change for clone.

**Files:** `sandbox.py` (set `spec.clone`, add the `rw` attempt mount for clone), `worktree.py`
(clone branch-in-VM path), `implement.py` (attempt dir on the `rw` mount for clone), `deliver.py`
(fetch-back before push for clone). Plus a clone-aware `FakeSandbox`/fixture for the integration test.