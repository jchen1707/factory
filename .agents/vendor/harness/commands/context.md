---
description: Context and memory hygiene audit
---

Audit the working context and the durable knowledge around it. **Report findings; do not
write memory or compact without confirmation.**

1. **Assess context size.** Summarise what is still relevant to the current task versus what
   can be dropped, and propose a compact if one is warranted. Flag files read more than once,
   and files read in full where a language server or a targeted search would have answered the
   question for a fraction of the tokens.

2. **Check the rule files for staleness and duplication** — `AGENTS.md`,
   `docs/architecture.md`, and any path-scoped `AGENTS.md` this repository carries. A rule
   stated in two places is a rule that will eventually disagree with itself. Flag anything
   contradicted by work done in this session. For a full pass, run `/prune-rules`.

3. **Check the tier.** Three exist and they are not interchangeable:

   - **memory** (`~/.claude/projects/<slug>/memory/`) — facts about _this project_ that the
     repository does not itself record;
   - **second brain** (`/search-second-brain`) — transferable lessons that outlive this
     project, written by the session-end hook;
   - **`AGENTS.md` and `docs/architecture.md`** — what has hardened into a rule everyone
     follows.

   Name anything sitting in the wrong tier, and anything that has recurred often enough to be
   promoted up one.

4. **Propose memory entries** — 1–3 concrete ones — for durable facts from this session:
   decisions, preferences, non-obvious constraints. Give each its type (`user`, `feedback`,
   `project`, `reference`) and a one-line pointer for `MEMORY.md`.

   Do not propose saving what the repository already records: code structure, git history,
   anything in `AGENTS.md`. That is not memory, it is a stale second copy.

5. **Suggest `/retro`** for any bug that took real effort, or any tool friction whose lesson
   is not captured yet, so it does not recur.
