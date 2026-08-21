---
name: full-review
description: Fan out one diff to every review axis in parallel, then consolidate their findings into one ranked report. Use for broad branch or pull-request reviews needing standards, specification, security, test, simplicity, design, performance and cost coverage.
argument-hint: '[base ref to review against]'
---

Read `.agents/vendor/harness/skills/full-review/SKILL.md` in full and follow it.

This file exists so a harness that discovers skills under `.agents/skills/` finds the
shared one. The body is layer A: generated, pinned by sha, and the same in every stack.
Editing it here is the drift the vendored copy's freshness check exists to catch — edit
it in [`harness`](https://github.com/jchen1707/harness) and re-sync.
