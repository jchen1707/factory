---
name: prune-rules
description: Audit the rule files for drift, duplication and dead rules, then refine them. Use when a rule file has grown, after a stack or tooling change, or when rules contradict each other.
argument-hint: '[a path or area to focus on, or blank for all rule files]'
---

Rule files decay. They grow faster than they shrink, they duplicate each other, and they keep
rules for tools the project no longer uses. A rule nobody follows teaches an agent that rules
are optional.

This skill audits them and proposes repairs. The focus is `$ARGUMENTS`; if empty, audit all of
them.

## The rule files

| File                           | Scope                                                                                                              |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| `AGENTS.md`                    | Root. Loaded every session.                                                                                        |
| `docs/architecture.md`         | Cross-cutting standards only.                                                                                      |
| Path-scoped `AGENTS.md`        | Per-directory conventions, loaded only when working in that path.                                                  |
| `docs/agents/*.md`             | The repo-specific half of layer A — tracker, triage, domain, and the stack half of each shared subagent and skill. |
| Subagent definitions           | Also the review axes, so a change here changes what a review checks.                                               |
| Repo-owned skills and commands | Including this one.                                                                                                |

<!-- harness:agnostic -->

| `.agents/vendor/harness/**` | **Generated. Never prune it here** — edit it in `harness`. |

<!-- /harness:agnostic -->

**Glob the tree, do not trust this table.** It is itself a rule file and can go stale; an
audit that reads only the paths listed here will miss whatever moved since it was written.
That has already happened once in each of these repositories.

`docs/agents/` in this repository names the actual paths. Read it, then glob anyway.

## What to look for

**Duplication.** The same rule stated in two files. Two copies drift, and nothing detects it —
the copies stay plausible while disagreeing. Keep one, and point the other at it.

**A rule that belongs upstream.** Layer A is shared, and a rule true in every stack that is
stated only here will be re-derived, differently, in the other one. That is not a local
duplication finding — it is the same one, a level up. Say so, and say which file in `harness`
should hold it.

**Wrong altitude.** A cross-cutting invariant sitting in a leaf file, or a single-directory
detail sitting in `architecture.md`. Path-scoped `AGENTS.md` files are exactly that: an agent
working in one directory never loads another's. A rule that must hold everywhere belongs in
`architecture.md` or root `AGENTS.md`, or it is not enforced where it matters.

**Dead rules.** A rule about a library, a command or a file that no longer exists. Verify
before deleting: read the dependency manifest, run the command, look for the path.

**Contradictions.** Two files that give different answers. This is the highest-value find,
because it means an agent is currently obeying whichever it happened to read. Report both and
say which is newer.

**Tooling overlap.** A rule the linter, formatter or type checker already enforces. Delete it.
A rule the tooling enforces is noise in a prompt, and it dilutes the rules that need a human to
follow them.

**Rules the tooling only appears to enforce.** The opposite failure, and worse. Check that each
machine-enforced claim is actually machine-enforced. A rule that classifies files by pattern
fails open on a file no pattern matches — silently unchecked, while the gate reads green. Where
`harness.config.json` records a gate's `caveat`, that is the list of ways it can pass without
checking. Prose is the right repair for what the config cannot see.

**Unverifiable rules.** "Write clean code." "Be thoughtful." They cost context and change no
behaviour. Replace with something checkable, or cut.

**Missing rationale.** A rule with no reason is a rule that gets deleted by the next person who
finds it inconvenient. If the reason is not obvious, add one line. If nobody can remember the
reason, that is a candidate for deletion.

**Staleness.** Counts, versions and file lists in prose go out of date silently. Check every
number against the thing it describes.

## Method

1. **Read the files in the focus area.** All of them, before proposing anything. A duplication
   finding needs both copies.
2. **Verify each claim you doubt** against the repository. Do not report a rule as dead because
   you do not recognise it — check the manifest, the file tree, the command's `--help`.
3. **Group findings by repair**, not by file. "This rule appears in three places" is one
   finding.
4. **Propose, then wait.** Present the findings with the proposed edit for each, and stop.
   Deleting a rule someone relies on is worse than leaving a stale one.
5. **Apply only what is approved**, and re-run the `verify` skill if any gated file changed.

## Deprecating, not just deleting

When a rule is being withdrawn but code still follows it, do not delete it silently. Mark it,
give the replacement, and give a date:

```markdown
> **Deprecated 2026-08-05.** Use the wrapper in the module's own service layer. The old form
> bypasses the shared error mapping. This note is removed once no call site uses it.
```

Delete the note when the last call site is gone. A deprecation with no removal condition is
just a longer rule.

## What not to do

- **Do not rewrite for style.** The task is correctness and duplication, not prose taste. A
  rule that reads awkwardly but is followed beats a beautiful one that is not.
- **Do not delete a rule because it is inconvenient.** It exists for a reason you may not have
  hit yet. If the reason is not written down, ask before cutting.
- **Do not add rules.** This skill removes and consolidates. Adding is a separate, deliberate
  act.
- **Do not edit generated files.** Layer A arrives from `harness`; a repair to it lands there
  and syncs back. Editing the copy here is the drift this whole arrangement exists to prevent,
  and the freshness check will fail on it.
- **Do not touch `.out-of-scope/`.** Those are decisions, not rules.

## Report

Lead with the count by category, then each finding: the files, the exact text, the proposed
repair, and the reason. Separate the repairs that land here from the ones that land in
`harness` — they are different pull requests in different repositories.

End with what you checked and found healthy. A clean audit is a useful result and should be
said plainly, not padded into findings.
