---
name: search-second-brain
description: Search the second brain for prior learnings on a topic and report the patterns across them, not just the hits. Use before designing something the notes may already have opinions about, when a problem feels familiar, or when a decision needs prior art.
argument-hint: '[topic, question, or error message]'
---

Search the user's personal knowledge base for what past sessions already learned, and report
**the pattern across the notes** — not a list of files.

The topic is `$ARGUMENTS`. If empty, ask what to look for rather than dumping the directory.

## Where to look

The notes live outside this workspace, so read them with **absolute paths**. The vault root is
`$OBSIDIAN_VAULT_DIRECTORY` — in PowerShell, `$env:OBSIDIAN_VAULT_DIRECTORY`. Learnings live
in its `Project Learnings` directory.

If the variable is unset, say so and stop. Do not guess at a path.

Two generated indexes sit above the notes. Both are cheap; read them before anything else:

| Index                          | Covers                                | Columns                    |
| ------------------------------ | ------------------------------------- | -------------------------- |
| `_VAULT_INDEX.md` (vault root) | **every** note in the vault           | path, tags, what it covers |
| `Project Learnings/_INDEX.md`  | the auto-distilled session notes only | date, project, summary     |

- The wider vault is where most of the value is — hand-written notes are usually more
  considered than anything auto-distilled. `_VAULT_INDEX.md` is what makes them findable
  without opening them.
- Learnings written by the session-end hook live in `Project Learnings`. Each has
  `tags: [project-learnings, session-retro]` and two sections — _Implementation learnings_ and
  _Architecture & design learnings_.

An `.base` file is **not** an index you can read. It is a query that Obsidian evaluates in its
own UI; reading it returns the query definition, never any notes. It is there for the human.
The Markdown indexes above are the ones for you.

**Never write to the vault from this skill.** It reads. The hook writes. Keeping those apart is
what stops a search from quietly editing the thing it was searching.

**Read `docs/agents/second-brain.md` in this repository** for anything the vault holds that is
specific to it. Which sessions rebuild the indexes is no longer such a fact: the writer and both
indexers are shared, so a session ending in any repository that installs this harness rebuilds
them.

## Method

1. **Read `_VAULT_INDEX.md` first. Always.** One row per note across the whole vault — path,
   tags, and a line on what it covers. It is the cheapest read available, and it tells you
   which notes are worth opening before you spend a token on any of them. Add
   `Project Learnings/_INDEX.md` when the question is about a past session specifically; it
   adds the date and the originating project.

   The two refresh on different schedules. `_VAULT_INDEX.md` rebuilds whenever a session ends
   with the second brain configured; `Project Learnings/_INDEX.md` rebuilds only when that
   session actually wrote a note, so it can lag behind a note renamed or deleted in Obsidian.
   A note added in Obsidian since the last rebuild is missing from both.

   A blank "what it covers" means the indexer found no prose, heading or bullet to quote. That
   is usually an empty stub — but a note built only from a table lands there too, so treat a
   blank row as low priority rather than as proof the note is empty.

   Never read every note to answer one question. That cost is exactly what the indexes exist to
   avoid, and it grows with every session.

2. **Search wide before reading deep.** `Grep` the whole vault for the topic and for its
   obvious synonyms — the notes were written in the language of the moment, not yours. An error
   message is best matched on its distinctive fragment, not the whole string.

   Use this to catch what the summaries do not say: a summary names topics, the body holds the
   detail. Search both, open bodies selectively.

   **Never skip this step because step 1 returned hits.** An unindexed note is a normal state,
   not an edge case. Skipping the grep turns "the vault has nothing on this" into a confident
   falsehood, which is the one answer this skill must never produce.

3. **Read the hits in full.** These notes are short. Skimming a lesson is how you get the
   correction backwards.
4. **Prefer the specific over the recent.** A note that names the exact tool or failure beats a
   newer one that gestures at the area.
5. **Check the repository too.** `AGENTS.md`, `docs/architecture.md`, the path-scoped
   `AGENTS.md` files and `docs/agents/` may already encode what a note only observed. Where
   they agree, say so — a lesson that made it into a standard is settled. Where they conflict,
   that is the most interesting finding you can return, and you should say which is newer.

## What to report

Lead with the **pattern**, then the evidence:

- **What the notes already establish** — the recurring lesson, stated once, in your own words.
  If three notes circle the same mistake, that is one finding with three citations, not three
  findings.
- **Where they disagree, or where a later note revised an earlier one.** Say which is newer and
  what changed.
- **What is settled versus still open** — a lesson promoted into `AGENTS.md` or
  `docs/architecture.md` is settled. One that lives only in a note is an observation that has
  not yet earned a rule.
- **What the notes do _not_ cover.** Say it plainly. A confident answer assembled from notes
  that never addressed the question is the failure mode this skill exists to avoid.

Cite each note by filename and quote the line you are relying on. The user should be able to
check you without re-searching.

## When a note you expected is missing

A session that should have produced a note but did not is a diagnosable event, not a mystery.
Read `_hook.log` in `Project Learnings` — the session-end hook appends one line per run:
`wrote`, `skipped`, `no learnings`, or `failed:` with the reason. No line at all for the
session means the hook never fired; a closed terminal window skips it. Report which case it was
rather than guessing.

## When the search comes up empty

Say so in one line and stop. Do not reconstruct an answer from general knowledge and present it
as if it came from the vault — the entire value here is that these are _the user's own_
hard-won conclusions, and blurring that makes the second brain untrustworthy.

Offer the alternative instead: answer from first principles, clearly labelled as such.

## One indexer, and it is this one

Read this before you propose adding an indexer to a repository. **Do not add one.**

An indexer in every repository is one artifact with several writers. One repository shipped a
port of another's indexer for exactly one day. In that time the pair re-diverged on a header
line inside a single fix cycle — with only one side under test, because neither repository's
suite can see the other's output. Tests can pin a contract between two implementations. They
cannot stop the two from disagreeing about what a description should say.

For a while the answer was to keep the single implementation in one repository, which bought
correctness at the price of a lag: notes written from anywhere else were unindexed until a
session happened to end in the repository that owned the indexer. That trade is over. The
indexer is here, in the shared layer, so every repository runs the same one and none of them
owns it. There is no lag to work around and no second implementation to disagree with.

The grep in step 2 is still not optional, for the reason that survives: a note added by hand in
Obsidian between sessions is unindexed until the next session ends anywhere. That is a normal
state, not an edge case.

## Escalating a pattern

When a lesson recurs across several sessions, it has outgrown the vault. Propose promoting it —
a repeatable procedure becomes a command or a skill; a durable standard becomes an edit to
`AGENTS.md`, `docs/architecture.md`, or the path-scoped `AGENTS.md` for the layer it governs.
Propose and confirm; do not apply it unasked. This mirrors what `/retro` does for memories.
