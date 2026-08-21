---
name: verify
description: Run the Definition of Done gates and report the actual output as evidence. Use before claiming work is complete, before opening a PR, or whenever asked whether the change actually works.
argument-hint: '[--all]'
---

Read `.agents/vendor/harness/skills/verify/SKILL.md` in full and follow it.

This file exists so a harness that discovers skills under `.agents/skills/` finds the
shared one. The body is layer A: generated, pinned by sha, and the same in every stack.
Editing it here is the drift the vendored copy's freshness check exists to catch — edit
it in [`harness`](https://github.com/jchen1707/harness) and re-sync.
