---
name: loop-goal
description: Run a standing goal until a stated condition holds — doc sweep, architecture satisfaction, test coverage, dependency freshness, or a goal this repository defines. Use when the user wants work driven to completion rather than one pass, or names one of the goals below.
argument-hint: '[goal name or a custom stop condition]'
disable-model-invocation: true
---

Read `.agents/vendor/harness/skills/loop-goal/SKILL.md` in full and follow it.

This file exists so a harness that discovers skills under `.agents/skills/` finds the
shared one. The body is layer A: generated, pinned by sha, and the same in every stack.
Editing it here is the drift the vendored copy's freshness check exists to catch — edit
it in [`harness`](https://github.com/jchen1707/harness) and re-sync.
