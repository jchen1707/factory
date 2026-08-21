---
description: Capture lessons from friction into durable memory
---

Run a retrospective on friction hit during this session and write durable **lessons** to the
memory store so the same trap is avoided next time. "Friction" means: a bug that took real
effort to diagnose, an approach that failed before the one that worked, or difficulty using a
tool — wrong flags, Windows or PowerShell quirks, env and setup gotchas, an API used wrongly,
or a command that didn't do what its name implied.

The argument optionally names a specific lesson to capture; if empty, scan the session for
any: `$ARGUMENTS`.

For each durable, non-obvious lesson:

1. **Decide if it's worth saving.** Keep only what would have saved real time had you known it
   up front. Skip anything the repository already records — code, git history, `AGENTS.md`,
   `docs/architecture.md`, any path-scoped `AGENTS.md` — and anything that mattered only to
   this one task.
2. **Check for an existing memory** first — scan `MEMORY.md`. Update that file rather than
   duplicating it; delete one that turns out to be wrong.
3. **Write the lesson** to `~/.claude/projects/<slug>/memory/` as one file with frontmatter
   (`name`, `description`, `metadata.type`), then add a one-line pointer to `MEMORY.md`.
   - `name`: `lesson-<short-kebab-slug>`.
   - `type`: `feedback` for a "how to work here" lesson; `reference` for an environment, tool
     or external-API gotcha.
   - Body: the symptom and what actually worked. For `feedback`, follow with **Why:** (the
     root cause) and **How to apply:** (the concrete thing to do next time). Link related
     memories with `[[name]]`.
4. **Escalate if it's recurring or procedural.** A one-off fact stays a memory. But if the
   lesson is a repeatable procedure or a standard everyone working here should follow, propose
   promoting it:
   - a repeatable workflow → a new command or skill;
   - a durable standard or stack rule → an edit to `AGENTS.md`, `docs/architecture.md`, or the
     path-scoped `AGENTS.md` for the layer it governs;
   - a review blind spot → a new checklist line under this repository's
     `docs/agents/subagents/`, or a new axis if no existing one would ever have caught it.

   Memories are cheap and reversible — write them directly. Promotions are more invasive:
   propose them and confirm before applying.

## Two stores, and only one of them is yours to write here

This command writes **project memory**. The session-end hook writes the **second brain** —
transferable lessons, in the user's own vault — on its own, with no prompting. Do not
duplicate a lesson into both: if it is about this project, it is a memory; if it would help on
an unrelated codebase, it belongs in the vault.

**The hook is not guaranteed to run.** It fires only when the session ends cleanly — a closed
terminal window skips it — and it can fail after firing (`_hook.log` beside the notes records
every outcome). So when this retro surfaces a lesson that is clearly **transferable** and
losing it would hurt, do not just leave it to the hook:

1. Check that the vault directory environment variable is set. If it is unset there is no
   vault to write to — say so and stop.
2. Ask the user whether to write the vault note now. If yes, write one Markdown file into the
   vault's project-learnings directory in the hook's own note format — frontmatter (`date`,
   `project`, `session`, `summary`, `tags`), then the learnings sections.
3. **Never write the vault index files by hand.** See `docs/agents/second-brain.md` in this
   repository for which sessions rebuild them.

A note written here and a note the hook writes later do not collide — the hook's filename
carries the session id. Duplicated content is cheap; a lost lesson is not.

Report what you captured and any promotion you propose. `/context` flags friction you haven't
captured yet.
