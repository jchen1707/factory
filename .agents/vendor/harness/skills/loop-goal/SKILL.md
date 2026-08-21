---
name: loop-goal
description: Run a standing goal until a stated condition holds — doc sweep, architecture satisfaction, test coverage, dependency freshness, or a goal this repository defines. Use when the user wants work driven to completion rather than one pass, or names one of the goals below.
argument-hint: '[goal name or a custom stop condition]'
disable-model-invocation: true
---

A standing goal is work with a **stop condition** instead of a task list. You keep going until
the condition holds, not until one pass finishes.

`disable-model-invocation` is set deliberately: these loops edit code and burn tokens over
many turns. Starting one must be the user's explicit act.

## Protocol

1. **State the stop condition first**, in one sentence, and get agreement if `$ARGUMENTS` was
   vague. A loop with a fuzzy condition never terminates.
2. **Open a progress file** at `.agents/plans/loop-<goal>.md` (gitignored). It survives
   compaction, which the conversation does not. Record:
   - the stop condition, verbatim;
   - a checklist of areas, each `pending` / `done` / `skipped (reason)`;
   - what changed each pass, one line per pass.
3. **Work one area per pass.** The smallest useful unit. After each: run the `verify` skill,
   commit if green, update the progress file.
4. **Re-read the progress file at the start of every pass.** Trust it over your memory of the
   conversation — after a compaction it is the only accurate record.
5. **Stop when the condition holds.** Report what changed, what you skipped and why. If you
   cannot reach the condition, say so plainly and stop; do not loop on something unreachable.

## Guardrails

- **Never loop on a condition you cannot measure.** "Until the architecture is good" is not a
  condition; "until no module in `services/` imports from `api/`" is. Convert vague goals
  before starting.
- **Cap the passes.** Default 10. Stop and report progress at the cap rather than continuing
  silently.
- **Never `git push` or open a PR from inside a loop** unless the goal explicitly says to.
  Committing to the working branch is fine.
- If two consecutive passes produce no change, stop — you have converged or you are stuck, and
  both mean the loop is over.

## Goals

These four hold in any repository. Each names its own stop condition; use it verbatim unless
the user overrides it.

| Goal           | Stop condition                                                                                                                                                                               |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `docs`         | Every claim in `AGENTS.md`, `README.md`, `docs/architecture.md` and every path-scoped `AGENTS.md` matches the code; every documented command runs; no reference to a deleted file or command |
| `architecture` | No violation of the dependency rule in `docs/architecture.md`; every cross-boundary dependency goes through the interface that boundary publishes; the gates in `harness.config.json` green  |
| `tests`        | Every exported unit and every state a user reaches has at least one test exercising real behaviour; every case in the test plan is covered; the `test` gates green                           |
| `deps`         | Every declared dependency is used; nothing used is undeclared; the approved-stack list in `AGENTS.md` matches what is installed                                                              |

**`docs/agents/loop-goals.md` in this repository carries the rest** — the goals that only make
sense in this stack, and any sharpening of the four above. Read it before you start, and use
its wording where it gives one: a stop condition stated against a real rule terminates, and one
stated against a general principle does not.

### Machine-enforced conditions have a failure mode

Where a stop condition leans on a gate — "until the linter is green" — check that the gate is
actually looking at the files in question. A rule that classifies files by pattern passes
vacuously on a file no pattern matches, and a metric the runner could not compute is reported
as a null, not as a failure. `harness.config.json` records this per gate as a `caveat`.

A loop that terminates because its gate stopped checking has not met its condition. It has
found a bug in the gate, and that is the finding to report.

## Running unattended

To run without stopping for input, pass the goal and a turn budget, and rely on the `Stop`
hook to keep the loop honest — it blocks the turn while the Definition of Done fails, so a
pass cannot end on broken code.

<!-- harness:agnostic -->

Do not depend on the hook. Not every harness has a blocking stop event, and a loop whose only
brake is one is unbounded in the harness that does not. Run `verify` after every pass and stop
on a repeated failure regardless of what the hook did.
<!-- /harness:agnostic -->
<!-- harness:claude
Claude Code overrides a `Stop` hook after **8 consecutive blocks**. If a pass hits that, the
loop is stuck on something it cannot fix: stop and report. Do not start another pass — past
that point nothing is enforcing the Definition of Done and the loop is running blind.
/harness:claude -->
