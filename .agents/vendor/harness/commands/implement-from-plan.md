---
description: Hand the branch's plan.md + test-plan.md to the /implement skill as its spec, with this repo's gates pinned (terminal 2, implementation model)
argument-hint: '[path to a plan file, or blank to resolve from the current branch]'
---

Two-terminal workflow — **terminal 2 (implementation model)**. This is a thin adapter over the
`/implement` skill: that skill implements "a spec or set of tickets" but does **not** know
about this repository's plan files, its layering, or its gates. This command supplies all
three.

Read `docs/agents/planning.md` in this repository before the first edit. It carries the stack
half: where code is allowed to land, and what this stack requires before a PR is opened.

1. **Locate the plans.** Derive the plan directory from the current branch: take
   `git branch --show-current`, replace each `/` with `-`, and read
   `.agents/plans/<branch-slug>/plan.md` and `test-plan.md` — branch
   `feat/BAC-412-vector-store` resolves to `.agents/plans/feat-BAC-412-vector-store/`. If
   `$ARGUMENTS` names a path, that wins as the implementation plan, with a sibling
   `test-plan.md`.
   - If no plan is found, **STOP** and tell the user to run `/plan` in terminal 1 first. Do
     not improvise a plan here — planning is a separate, signed-off step.
   - If `test-plan.md` is missing, ask the user whether to proceed without it or go back to
     `/plan`. Do not silently skip the test plan.

2. **Read both files in full** before doing anything else. The plan is the source of truth: do
   not re-plan or re-scope it. If a Step looks wrong or impossible, flag it to the user and
   ask — do not quietly substitute your own approach.

3. **Confirm the branch.** `/plan` creates the feature branch off the user's chosen base. Run
   `git branch --show-current`; if you are on the default branch, stop and ask which branch to
   use. Per `AGENTS.md`, direct commits there need an explicit user request.

4. **Invoke `/implement` with the plans as the spec.** Say explicitly that the spec is the
   resolved `plan.md` plus `test-plan.md` — name their full paths — and pass along:
   - the numbered **Steps** from `plan.md` as the task list. Build it with `TaskCreate`, one
     task per Step, marking each in progress and complete as you go.
   - the **test cases** from `test-plan.md` as the cases to drive `/tdd` with, and the
     **seams** it names as the pre-agreed seams. `/tdd` requires seams be confirmed before any
     test is written; the test plan is that confirmation.
   - the **Open questions** from `plan.md`, to raise with the user rather than guess.
   - any further section `docs/agents/planning.md` says this stack's plans carry.

5. **Pin the layering.** Code lands where this repository's structure says it lands, not in a
   folder named after its kind. `docs/architecture.md` and the path-scoped `AGENTS.md` for the
   directory you are editing are the authority; read the relevant one before the first edit.

6. **Pin the gates.** Shared skills say "run typechecking" and "run the test suite"
   generically, and some reach for a toolchain this repository does not have. Wherever a skill
   says that, substitute the commands `harness.config.json` names in `gates`. Run the opt-in
   kinds when their `when` clause applies — one of them usually catches a class of failure the
   others structurally cannot, which is why it is a gate and not a suggestion.

7. **Iterate in the fast loop, not in the slow one.** Where this repository documents an
   interactive way to exercise a change — a dev server plus a browser automation client, a
   REPL, a request client — use it while the behaviour is still moving. Re-running the full
   end-to-end suite to answer "did my change take effect?" is the slow path and asserts
   nothing new. The heavy suite is the regression net: when a behaviour settles, write the
   **minimal** spec for it once, run it once to prove it, and move on.

8. **Finish to the Definition of Done** in `AGENTS.md`. Run `/verify` for the evidence — paste
   the real output rather than asserting the gates passed. Then `/code-review` with the
   merge-base as the fixed point (`git merge-base HEAD <default-branch>`) and no Standards
   findings outstanding. The `Stop` hook re-runs the gates independently, so a turn cannot end
   on a failing gate over a gated path.

9. **Update the plans as you go** — tick off Steps in `plan.md` and cases in `test-plan.md` as
   they land, so an interrupted session can resume from the files. Keep the `Status:` line at
   the top of `plan.md` current, **in the same turn the state changes**: writing "review is
   running" and leaving it there after the session ends plants a lie for the next reader.

   Append divergences under a `## Deviations` heading. Never rewrite the approved Goal,
   Approach or Steps — the record of what was signed off is the only thing that makes the
   deviation visible.
   - **Material** deviation — a changed approach, a changed public signature, a crossed layer
     boundary, changed scope, or an open question that turned out to matter → **STOP and
     re-confirm with the user.**
   - **Immaterial** — helper names, file splits, fixing pseudocode bugs → proceed and log it.

10. **Commit, and open the PR only when asked.** `/implement` commits to the current branch.
    Opening the PR is a separate, explicit step — run `gh pr create` only if the user asks.
    When you do open one:
    - Run this repository's pre-PR check first, if it has one. It checks the process gates the
      code gates cannot see — the body, the tracker link, whether the diff is actually covered
      by tests, the plan's status — and produces the evidence summary.
    - The body follows `.github/PULL_REQUEST_TEMPLATE.md` where one exists. **Never open a PR
      with an empty body** — fill it from the plan and the `/verify` output already in hand.
    - Sync the tracker in the same turn: move the issue to its in-review state, attach the PR,
      and comment the evidence. See `docs/agents/issue-tracker.md` → Status sync.

    Report what landed, what is left, and anything the plan got wrong.

> Do not feed any image into the model without explicit user permission — see the guardrails
> in `AGENTS.md`. Prefer a text snapshot of the interface where one is available.
