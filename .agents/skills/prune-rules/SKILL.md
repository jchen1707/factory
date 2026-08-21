---
name: prune-rules
description: Audit the rule files for drift, duplication and dead rules, then refine them. Use when a rule file has grown, after a stack or tooling change, or when rules contradict each other.
argument-hint: '[a path or area to focus on, or blank for all rule files]'
---

Read `.agents/vendor/harness/skills/prune-rules/SKILL.md` in full and follow it.

This file exists so a harness that discovers skills under `.agents/skills/` finds the
shared one. The body is layer A: generated, pinned by sha, and the same in every stack.
Editing it here is the drift the vendored copy's freshness check exists to catch — edit
it in [`harness`](https://github.com/jchen1707/harness) and re-sync.
