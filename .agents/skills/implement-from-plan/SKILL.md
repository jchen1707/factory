---
name: implement-from-plan
description: Hand the branch's plan.md + test-plan.md to the /implement skill as its spec, with this repo's gates pinned (terminal 2, implementation model)
argument-hint: '[path to a plan file, or blank to resolve from the current branch]'
---

Read `.agents/vendor/harness/commands/implement-from-plan.md` in full and follow it.

This file exists so a harness that discovers skills under `.agents/skills/` finds the
shared one. The body is layer A: generated, pinned by sha, and the same in every stack.
Editing it here is the drift the vendored copy's freshness check exists to catch — edit
it in [`harness`](https://github.com/jchen1707/harness) and re-sync.
