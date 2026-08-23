# Phase 5 — what to do first, and what is carried in

Written 2026-08-23, at the close of Phase 4.5. Read
`.agents/plans/phase-4.5-remaining.md` (the "Session of 2026-08-23" section) for how the
last four defects were found; this file is only about what happens next.

**§19 Phase 5 in `SOFTWARE-FACTORY-PLAN.md` is the authority.** Everything below either
points at it or records a fact measured on 2026-08-23 that §19 does not carry.

---

## Where things stand

| | |
| --- | --- |
| Phase 4.5 exit criterion | **met** — run `40d1d56205f64361`, ten `auto` transitions, draft PR frontend-harness#45 |
| FRO-7 | `awaiting_human`. The PR is open and unreviewed |
| BAC-6 | `suspended` at attempt 7 (planning), worktree and session intact, `needs-info` on the ticket |
| `fix/kill-agent-and-session-id-on-the-detached-path` | 3 commits, **unmerged, unpushed** |
| `docs/phase-4.5-remaining` | 2 commits, unmerged |
| daemon | loaded and ticking every 60 s |

---

## Do these before starting Phase 5

Not preferences — each one is a thing that will bite the first Phase 5 run.

1. **Merge the fix branch, or check it out and leave it there.** The launchd plist runs
   `uv run --project /Users/james/factory factory tick --once`, which executes **whatever
   is in the working tree**. So `git checkout` on this repo silently changes what the
   unattended daemon does. While `main` is checked out, the daemon is running code that
   cannot capture a session id, cannot kill an agent without destroying the attempt's
   terminal record, and builds a resume command the binary rejects.
2. **Decide FRO-7 / PR #45.** §19 makes this the Phase 4.5 → 5 approval boundary in
   words: *"James decides whether the unattended run's PR is good enough to proceed to
   Phase 5."* Nothing else is blocked on it, but the phase boundary is.
3. **Apply the §19 re-plan.** The two-track split is proven in practice now, and the
   replacement Steps block is written. `harness.config.json` protects the plan file, so
   the hook refuses the edit — it is James's keystroke by design.
4. **Settle BAC-6.** `factory resume BAC-6` continues it, `factory cancel BAC-6` ends it.
   Either way the `needs-info` label has to come off the ticket by hand or intake will
   refuse it for ever, and `cancel` will not remove it if the tracker also moved (see the
   carried defect below).

---

## What Phase 5 actually asks for, against what exists today

§19's bullets, each annotated with what is already true in the repo.

| § 19 asks | today |
| --- | --- |
| Fill `projects.toml` with the **measured** template answer from P0-3 | `frontend-harness.template = "codex-pnpm:v1"`; **`python-harness.template = ""`** and `kits = []` on both. The python row has never carried a template |
| Author `kits/python.yaml` and `kits/frontend.yaml` if node or `uv` are missing | **`kits/` does not exist.** Nothing in the codebase reads a kit file yet beyond the registry field |
| Assert `playwright install chromium` has run before an `e2e` gate is required — otherwise `unavailable`, never `pass` | The caveat machinery exists (`verify.collect`'s `env-gate-failed`, added in Phase 4), but there is **no explicit chromium assertion**. This is the same failure mode that killed three FRO-7 runs; see [[opt-in-gates-all-is-all-or-nothing]] |
| Assert Docker is available before a python `integration` gate is required | **nothing** — `grep -rn docker src/factory` is empty |
| **PRs move from draft to ready-for-review** | Five places hard-code it: `delivery/github.py:114` (`--draft` in the argv), `delivery/github.py:262` (the PR-body line "opened by the factory as a **draft**"), `steps/deliver.py:80` (the `--dry-run` preview string), `steps/deliver.py:117` (`draft=True` on the structured log line), and the module docstring. They have to move together, or `--dry-run` stops describing the command that actually runs — which is the only thing that makes it worth reading |
| First layer-C product via `new_project.py … --agnostic`, registry row `stack = "monorepo"` | not started. `stack` is `python` / `frontend` only |
| Tests: one ticket per stack end to end, plus a monorepo dispatch test proving a CSS-only change runs no Python gate | not started |

### the thing §19 does not say, and it is the important one

**Nothing in the factory ever reaches `COMPLETED`.** `grep -rn "State.COMPLETED"
src/factory/` outside `machine.py` returns nothing. A successful run therefore sits at
`awaiting_human` for ever, and because `gc.COLLECTABLE` is `TERMINAL | {FAILED}`, `gc` can
never reclaim that run's worktree, branch or artifacts.

This was left open deliberately at the end of Phase 4.5 — the `awaiting_human ->
completed` transition sits on the merge boundary, and §24.8 makes the merge boundary
Phase 5's. **So it is Phase 5's to build**, and it should be built early rather than last:
every Phase 5 test run adds another uncollectable worktree, and the worst symptom (stale
worktrees breaking `pnpm test`) is currently only patched from the consumer side in
frontend-harness#43.

The state machine is already ready for it: `machine.py:103` carries the edge
`AWAITING_HUMAN -> COMPLETED`, and `machine.py:162` reserves it for James under
`merge-is-james`. What does not exist is anything that notices a merged PR and takes the
edge.

---

## Defects carried into Phase 5

None of these block starting; all of them will be met eventually, and each has already
cost a session once.

1. **`cancel` cannot restore a tracker it did not set — which is the successful case.**
   When a run opens a PR the GitHub integration moves the ticket to `In Review`, so
   cancelling any run that got as far as a PR leaves the ticket at a non-`Todo` state and
   the poller skips it for ever. It prints `left <T> at '<state>' (not the state the
   factory set)` and nothing says that this means the ticket is now unclaimable.
2. **The re-run ceiling counts orphans, not attempts.** `resumable_reentries` counts
   `<state> -> resumable` transitions, so a deterministic environment failure burns the
   whole budget in three ticks and fails a run whose work is good. An attempt that dies
   before its first heartbeat never ran.
3. **`kill_agent` cannot stop a hung `verifying` gate.** Its body is a node gate report,
   not codex, so neither the old `pkill -f "codex exec"` nor the new `pkill -x codex`
   matches it. Found 2026-08-23 while fixing the kill; pre-existing, not a regression.
4. `_SENSITIVE_DIRS["frontend"]` matches nothing, and `deliver._archive` returns silently
   when the attempt directory is missing.

---

## The method, unchanged

Every defect in the last two sessions — seven, then four — was found by executing the
thing, never by reading about it. Three of the last four lived in code that had tests
passing over it: an argv assertion that pinned four tokens of a command the binary
rejects, a fake that wrote the session id itself before asserting it survived, and a fake
modelling a wrapper trap that does not exist. **A green suite is a claim, not evidence.**
Before believing a test, stash the source and watch it fail; and check the test sits at
the altitude the defect lives at, not one layer above it.
