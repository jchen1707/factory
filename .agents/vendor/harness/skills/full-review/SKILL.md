---
name: full-review
description: Fan out one diff to every review axis in parallel, then consolidate their findings into one ranked report. Use for broad branch or pull-request reviews needing standards, specification, security, test, simplicity, design, performance and cost coverage.
argument-hint: '[base ref to review against]'
---

# Full review

Review one diff on every axis independently, then consolidate. Keep review and consolidation
in separate contexts.

This is the harness-neutral form. Claude Code runs the same fan-out as a workflow
(`workflows/full-review.js`), which resolves the axes from the same files this skill names —
so the two cannot drift apart.

## Select the diff

Use the base from `$ARGUMENTS` when present. Otherwise `REVIEW_BASE`, then the default branch.

Use the same `git diff <base>...HEAD` range for every reviewer. Report the range in the final
output.

Stop when the base does not resolve. Ask the user for the correct base rather than reviewing
against a range you guessed.

## Work out the axes

Read `harness.config.json` at the repository root. The axes are:

| Axis               | Agent                                       |
| ------------------ | ------------------------------------------- |
| Standards          | `standards-reviewer`                        |
| Specification      | `spec-checker`                              |
| Security           | `security-reviewer`                         |
| Tests              | `test-reviewer`                             |
| _this stack's own_ | `review.ninthAxis` in `harness.config.json` |
| Simplicity         | `simplicity-reviewer`                       |
| Design             | `design-reviewer`                           |
| Performance        | `perf-reviewer`                             |
| Cost               | `cost-reviewer`                             |

The ninth sits after Tests. A repository with no `ninthAxis` runs the eight shared ones; that
is a complete review, not a degraded one.

## Fan out

Spawn one read-only subagent per axis.

Each axis is assembled from two files, and the agent must be given **both**:

1. The **frame** — the shared definition of the role, the method and the reporting rules.
   Layer A owns it, so it is identical in every stack.
2. The **checklist** — `<review.checklistDir>/<agent>.md` in this repository, which is what
   "in this repo's terms" means here.

A definition at `<review.agentDir>/<agent>.md` replaces the frame when it exists. That is how
the ninth axis resolves, and it is the supported way for a stack to override a shared frame
deliberately.

**If an axis resolves to neither a frame nor a checklist, stop and say so.** It means layer A
did not arrive — the plugin is not enabled, or the vendored tree is missing or stale. Running
the axis anyway on a one-line brief produces "no findings" from a reviewer that never
reviewed, which is worse than no review at all because it is indistinguishable from a clean
one.

Give each agent the diff range. Do not give `spec-checker` a ticket summary — it resolves the
spec itself, and a summary is the author's framing sneaking past the one gate meant to check
the work against what was actually filed.

Tell each agent to return only real defects. An empty result is valid.

Use this result shape:

```json
{
  "findings": [
    {
      "file": "path/to/file",
      "line": 12,
      "severity": "high",
      "summary": "One sentence.",
      "why_it_matters": "One sentence."
    }
  ]
}
```

Allow only `critical`, `high`, `medium` or `low` severity.

Start all agents concurrently when capacity permits; start the rest as slots open. Wait for
every agent — **do not consolidate partial results.** If an agent fails, retry it once, and
report the missing axis when the retry fails. A report that silently covers eight axes and
claims nine is a false clean bill.

## Fan in

Start a **fresh** synthesis agent after all reviewers finish. Give it only the reviewer results
and the axis labels; it must not inspect the diff. Use the primary agent only when a fresh one
is unavailable.

Tell it to:

1. Merge findings that describe the same defect.
2. Keep the clearest wording from the duplicates.
3. Record every axis that reported each merged finding — agreement raises confidence.
4. Remove style findings already enforced by the tools named in `review.styleEnforcedBy`.
5. Rank by severity, then by how many axes independently found it.
6. Give the smallest safe fix for each finding.
7. Add no new findings of its own.

## Report

Return `No findings` when every completed axis returns an empty list.

Otherwise report each finding with: severity, file and line, the defect, its impact, the
smallest fix, and the agreeing axes.

End with the reviewed range and the axes that completed. List any axis that failed after its
retry, and any axis that could not be assembled.
