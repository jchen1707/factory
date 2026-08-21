# Domain Docs

How the engineering skills should consume a repo's domain documentation when exploring the
codebase.

This is shared harness doctrine. Each repo keeps its own `docs/agents/domain.md` giving its
file structure, its nested `AGENTS.md` map and the vocabulary it has already settled.

## Before exploring, read these

- **`docs/architecture.md`** — **the decision record for the repo.** Its "choosing an
  architecture & design patterns" section records the architectural style and design patterns
  chosen per feature, and the rest is the authoritative standards reference (`/arch` loads
  it). Read it before proposing structural change; treat its recorded choices exactly as you
  would an ADR.
- **`AGENTS.md`** — the always-loaded summary: approved stack, the repo's dependency or
  layering rule, Definition of Done. Where it and a skill's generic advice disagree,
  `AGENTS.md` wins.
- **The nested `AGENTS.md` for the directory you are changing**, where the repo has them.
  These are path-scoped: working in one directory does not load another's.
- **`CONTEXT.md`** at the repo root — the domain glossary, if it exists.
- **`docs/adr/`** — individual ADRs, if any exist. Where a repo has none, architectural
  decisions have been recorded in `docs/architecture.md` instead. A per-decision file under
  `docs/adr/` is fine for a decision too narrow to belong in the standards doc, but don't
  migrate an existing record — keep `docs/architecture.md` authoritative.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't
suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs`
and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get
resolved.

## Single-context by default

The harness repos are single-context: one root `CONTEXT.md`, one `docs/architecture.md`. The
multi-context layout — a root `CONTEXT-MAP.md` pointing at per-context `CONTEXT.md` files —
applies to a monorepo carrying more than one deployable, not to a single package or
application. Check the repo's own `domain.md` for which shape it is before assuming.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis,
a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary
explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing
language the project doesn't use (reconsider) or there's a real gap (note it for
`/domain-modeling`).

**"agent" is the standing ambiguity in every one of these repos.** In `AGENTS.md` and in these
docs it means a dev-workflow subagent belonging to the harness — not an application-level AI
agent that the product itself builds and ships. Say which you mean. Each repo's own
`domain.md` records the further ambiguities it has settled.

## Flag ADR conflicts

If your output contradicts a decision recorded in `docs/architecture.md` (or an ADR under
`docs/adr/`), surface it explicitly rather than silently overriding:

> _Contradicts docs/architecture.md §… (the recorded dependency rule) — but worth reopening
> because…_

The repo's dependency or layering rule and its approved stack are the two most likely to be
contradicted by generic advice. Neither changes without an `AGENTS.md` edit first.
