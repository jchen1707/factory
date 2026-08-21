---
description: Research + plan a feature (implementation plan + test plan), get user sign-off, then STOP for handoff (terminal 1, planning model)
---

Two-terminal workflow — **terminal 1 (planning model)**. Research and plan the work, write an
**implementation plan** and a **test plan**, get the user's explicit sign-off, then **STOP**.
Do NOT write application code or implement here — planning only.

The argument is the feature or task to plan: `$ARGUMENTS`.

Read `docs/agents/planning.md` in this repository before step 3. It carries the stack half of
this command: what a design decision has to name here, and any extra pass this stack requires.

1. **Build the planning task list** (`TaskCreate`): `Pick base branch & branch off` →
   `Understand` → `Design approach` → `Write plan.md` → `Write test-plan.md` →
   `Get user sign-off`. Work through them in order, marking each in progress and complete.

2. **Pick the base branch and create the feature branch — FIRST, before research.** The
   feature must be built off the branch the user chooses. Never assume the current branch;
   it may be stale.
   - Inspect what is available so the options are real rather than guessed:
     `git fetch --all --prune` (best effort — note it if you are offline), then `git branch`
     and `git branch -r`. Note the current branch and the default.
   - Ask the user which branch to build off, using `AskUserQuestion`. Offer the default branch
     first as the recommendation, plus the current branch and any obviously relevant local or
     remote branch. They can type another under "Other".
   - Check out the chosen base and update it: `git checkout <base>`, then `git pull` — use
     `git pull --ff-only` when the base tracks a remote. If the pull fails (no upstream,
     offline, conflicts), **STOP** and tell the user; ask whether to proceed from the local
     state or fix it first. Do not force and do not reset.
   - Create the feature branch off it. Name it `<type>/<TEAM>-<num>-<slug>` when a tracker
     issue exists, so the Spec review axis can resolve the ticket mechanically — see
     `docs/agents/issue-tracker.md`. Kebab-case from the feature argument otherwise. Confirm
     the name with the user.
   - Plan files live under `.agents/plans/`, which is gitignored, so switching branches does
     not disturb them. Commit nothing here — this step only sets up the branch.

3. **Understand** — read the relevant existing code and `docs/architecture.md` (or run
   `/arch`). Check whether earlier sessions already have opinions:
   `/search-second-brain <topic>`. Note the modules, layers and files this work touches.

4. **Design approach** — decide the design, where each piece lives, which interfaces to
   define or extend, the dependencies, the risks and the verification steps.
   `docs/agents/planning.md` names what this stack requires a design to state explicitly;
   cover every item it lists.

   **Verify the verb and the version** for every library or external API the design names.
   Confirm the library supports the operation you are asking of it — read versus write, parse
   versus render — and confirm the API shape against the version this repository has locked,
   the installed package rather than memory. Record each check in the plan: "verified against
   httpx 0.28". An unverified claim is copied into the implementation and fails there, one
   terminal later, where it costs the most to find.

5. **Write the implementation plan** — `.agents/plans/<branch-slug>/plan.md`, where
   `<branch-slug>` is the feature branch name with each `/` replaced by `-` (branch
   `feat/BAC-412-vector-store` → `.agents/plans/feat-BAC-412-vector-store/plan.md`).

   One directory per branch is what lets two features be planned in parallel without
   overwriting each other, and `/implement-from-plan` resolves the same slug from the branch
   it is standing on. Worktree-per-ticket makes parallel planning the ordinary case, not the
   exception.

   The plan contains:
   - **Status** — one line at the top: `awaiting sign-off`, `approved`, `implementing`,
     `implemented`, or `review clean`. **Update it in the same turn the state changes.** A
     stale line misleads the session that resumes from this file, and a reader must treat an
     in-progress status left by a previous session as unknown, not as fact.
   - **Goal** — what this change delivers.
   - **Context** — findings from step 3: current state, constraints, files.
   - **Approach** — the design from step 4, including everything `docs/agents/planning.md`
     requires, and what the affected modules publish versus keep internal.
   - **Steps** — numbered, concrete, ordered implementation tasks. Each names its files and
     its layer and is small enough to verify on its own. Terminal 2 turns this list into its
     task list.
   - **Verification** — the gates from `harness.config.json` that this change must pass, any
     opt-in gate whose `when` clause applies, and any setup they need. Then `/code-review`.
   - **Open questions** — anything terminal 2 should confirm before or while implementing.

6. **Write the test plan** — `test-plan.md`, beside `plan.md` in the same directory:
   - **Scope** — the behaviours that must be covered, each tied back to a plan Step.
   - **Offline tests** — the cases per layer, and the fakes and request stubs they use. These
     are the default test gate: no network, no database.
   - **Tests in the other tiers** — the cases needing a browser or a container, with the
     marker or runner they belong to and the seed or reset they rely on; or "none" with a
     one-line reason.
   - **Edge cases and failure modes** — validation errors, not-found, empty input, loading and
     error states, bounds and limits, cancellation, keyboard-only operation. For each
     configuration field the change adds, list three distinct cases: absent, present but
     empty, and invalid.
   - **How to run** — the exact commands, and which gates in `harness.config.json` this
     covers.

   Every case must assert on behaviour the application code produces, at the place that
   produces it. A case that emits the event it asserts on, or recomputes the expected value
   the way the code does, is tautological — replace it before sign-off, not after.

7. **Get user sign-off — REQUIRED checkpoint, do not skip.** Present a concise summary of both
   files and explicitly ask the user to confirm, using `AskUserQuestion` ("Approve plan & test
   plan" / "Revise"). Revise and re-ask until they approve. Do **not** proceed past this
   checkpoint on your own — even in autonomous mode, planning requires this explicit
   verification.

8. **STOP — do not implement.** Mark the planning tasks complete and stop. Tell the user to
   open terminal 2 (implementation model) **on this branch** and run `/implement-from-plan`,
   which resolves the branch's plan directory and feeds `plan.md` and `test-plan.md` to the
   `/implement` skill as its spec — that skill will not find them on its own.

   Implementing is a separate, explicit step. Never roll straight from planning into writing
   code, even when both happen in one terminal.

The plan files are gitignored: local handoff artifacts, not committed. Plan mode's own
auto-saved file uses a random slug name and is not a reliable handoff, which is why this
command writes the stable per-branch pair instead.

> If any task in this session would feed an image into the model — a screenshot, a mockup, a
> diagram — stop and ask the user for permission first. See the guardrails in `AGENTS.md`.
