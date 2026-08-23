# Phase 5 — where it stands, and what to do next

Rewritten 2026-08-23, after item 2 landed in both consumers. The previous version is in
git history at `5eab90e`.

**§19 Phase 5 in `SOFTWARE-FACTORY-PLAN.md` is the authority.** Everything here either
points at it or records a fact measured on 2026-08-23 that §19 does not carry.

There is no longer a second plan file. `.agents/plans/software-factory-plan.md` — the stale
condensed copy that misled two sessions — was deleted this session on James's word. If you
find yourself reading a plan whose path is not `/SOFTWARE-FACTORY-PLAN.md`, something has
gone wrong.

---

## Where things stand

Re-measured 2026-08-23, end of session. **Item 2 is closed and merged.** Everything below
was measured, not carried forward.

| | |
| --- | --- |
| `factory@main` | `69098fd` — `#37`–`#40` all merged |
| suite | **541 passed**, mypy clean on 71 files, ruff check + format clean |
| `harness@v2` | `a3beb34`, plugin `0.10.1` |
| `frontend-harness@v2` | re-vendored and merged (`#47`, 7/7 green) |
| `python-harness@v2` | `f924eb3` — re-vendored and merged (`#69`, 5/5 green) |
| BAC-6 | **`awaiting_human`**, PR [python-harness#70](https://github.com/jchen1707/python-harness/pull/70) — the full path, through the unblock edge |
| FRO-11 | **`awaiting_human`**, **no PR** — every gate green, stopped by the `test-weakening` guard one step before `deliver` |
| `factory#41` | open — a run sent back from `blocked` is told why |
| `factory#42` | open, **stacked on #41** — `--authorise` buys an attempt |

**James's framing, stated this session and worth keeping at the top of this document:** the
goal is not that the agent executes a ticket perfectly. It is that the *workflow* runs. A
ticket is an instrument for driving the state graph, and an imperfect implementation that
traverses the right edges is worth more than a perfect one that never leaves `implementing`.
Read every "what is left" below through that lens: what matters is which edges have never
fired, not which tests are vacuous.

---


## What this session did

### The re-vendor, and why it was not optional

`harness#19` changed `plugins/harness/schema/harness.config.schema.json`, and `schema/` is
vendored. `vendor_sync.py check` compares vendored *content*, so that commit turned the
consumers' pins into `stale pin` failures rather than behind-but-identical notes — `freshness`
red on both `v2` branches from the moment `#19` landed.

Both consumers were re-vendored with the generator itself
(`python3 /Users/james/harness/scripts/vendor_sync.py sync --harness /Users/james/harness --target .`),
never by hand. The diff is two files in each repo — `MANIFEST.json` (`134b21c` -> `a3beb34`)
and the schema's `requires` description and examples. `vendor_sync.py check` prints
`OK: vendored layer A matches harness@a3beb3457` in both.

Every gate was run on the host before opening either PR, and every one is in the PR body as
its real output, not as an assertion: frontend `81 passed` / `built in 1.02s`, python
`173 passed, 3 deselected` and `3 passed` under `-m integration` with its `requires` probe
exiting 0. CI then agreed — 7/7 and 5/5.

**Two traps the last handoff named were live and both were avoided by following it.** The
frontend `body` job wants all four template headings, and the PR was written from
`.github/PULL_REQUEST_TEMPLATE.md` first time. No layer A content changed here, so no plugin
version bump was owed.

**One new thing worth knowing: `frontend-harness` runs `lint-staged` on commit, and it runs
`prettier --write` over staged JSON.** That is exactly the write that would reflow a vendored
file and break its sha. It did not, because `prettier` honours `.prettierignore` and that file
excludes `.agents/vendor/` — the caveat on the `prettier --check` gate is load-bearing at
commit time, not only at gate time. `vendor_sync.py check` was re-run *after* the commit to
prove it, and that is the check to repeat on any future vendor commit in that repo.

### The stranding race, a fourth time

`9663a1b` — the previous handoff's own last commit — was not in `origin/main`. `#36` merged
while it was still in flight. `git merge-base --is-ancestor 9663a1b origin/main` exits 1, and
`factory#37` recovers it. **Check the branch tip, not the PR state**, every time.

### FRO-11's run row says `worktree = /Users/james/frontend-harness`, and that is not a bug

There is no `.factory/worktrees/FRO-11` on the host — `git worktree list` shows only FRO-7 —
and `src/features/projects/ui/ProjectDetailPage.test.tsx`, which the run's log shows it
editing, does not exist in the host checkout. The run is inside the `sbx` clone
`factory-build-frontend-harness`, which mirrors the host path. A reader who sees that path in
`runs.worktree` and panics about the main checkout will waste the time this paragraph saves.
Host branch work in `frontend-harness` proceeded alongside the run with no interaction.

**Its PR will open with `freshness` red, and not for anything wrong with its work.** The run
claimed a worktree pinned at `134b21c`, so it needs a rebase onto `v2` after `#47` merges.

### A sleeping laptop reaps a healthy run — measured, new, and the sharpest edge on defect 2

FRO-11 attempt 1 was declared `attempt-orphaned` at 17:16:56 UTC after 67 minutes of
implementing. **The agent was not dead. The machine was asleep.**

`pmset -g log` on this host:

```
13:16:39 EDT  DarkWake ... 45 secs
13:17:24 EDT  Entering Sleep state due to 'Maintenance Sleep' ... 1024 secs
13:34:28 EDT  DarkWake ... 45 secs
13:50:58 EDT  Wake ... due to UserActivity
```

The reap fired at 13:16:56 EDT — **inside that 45-second DarkWake**. The heartbeat subshell
(`while :; do date -u +%s > heartbeat; sleep 20; done`, §14.1's wrapper) does not run while
the host is suspended, so on every wake its file is minutes stale. `sbx.poll` then takes the
last branch: holder pid alive, sandbox `running`, beat older than `ORPHAN_AFTER_SECONDS = 90`
-> `ORPHANED` (`sandbox/sbx.py:454`). Recovery re-entered at 13:34:59 EDT, inside the *next*
DarkWake, as attempt 2 with the same session id.

That worked — attempt 2 resumed, found the work committed at `0438fd1`, and went straight to
re-running the two reviews. But **it spent one of the three `resumable` re-entries defect 2
describes, and it spent it on nothing at all.** A host that sleeps four times overnight fails
an unattended run whose work is perfect, and the run's own log will say `attempt-orphaned`
three times with no cause visible anywhere in the factory's evidence. It is only visible in
`pmset`.

The cheap fix is in `poll`: a stale heartbeat with a **live holder pid and a running sandbox**
is not the orphan signature — the signature §16.1 actually wants is the holder gone, which
line 439 already checks first and which fires immediately. Reaching line 454 at all means
"the wrapper stopped writing but everything holding it is still up", and suspension produces
exactly that. Not changed here: it is a §16.1 semantics decision, and a run was in flight.

### And a recovered reconnect threw away a finished attempt — fixed, `factory#38`

Nineteen minutes after the sleep-reap, FRO-11 attempt 2 resumed, found the work committed,
re-ran both reviews, exited **0**, and wrote a `last-message.json` reading `status:
implemented` with commit `e00002b` and "Standards and Spec reviews found no issues".

It was thrown away: `implementing -> resumable`, rule `agent-failed`, detail
`exit 0; Reconnecting... 1/5 (stream disconnected before completion: Transport error…)`.

The 70-line transcript is the whole story — `thread.started` at line 0, one top-level
`{"type": "error"}` at line 5 carrying codex's own retry notice, `turn.completed` at line 69.
`parse_events` (`agent/codex.py`) marked *any* top-level `error` as a failed transcript, and
`implement.collect` raises `agent-failed` on `transcript.failed` regardless of exit code.

**So two of that run's three `resumable` re-entries were spent inside two hours, neither on
anything wrong with the work** — one on a sleeping laptop, one on a network blip codex had
already recovered from by itself.

Fixed in `factory#38`: a top-level `error` fails the transcript only while it is the stream's
last word on the turn; a later `turn.completed` withdraws it. `turn.failed` is held in a
separate variable and is never cleared — it is the model's verdict, not the transport's. The
notice still lands in `error_items`, because a run that reconnected four times succeeded on
worse terms than one that never dropped. Fixtures are the real FRO-11 lines; three mutations
tried, three caught. Suite 533 -> 535.

**One hazard the fix exposed: the daemon executes whatever is checked out in
`/Users/james/factory`.** A `git checkout` in that directory changes the code the next tick
runs. This document was edited from a temporary `git worktree` for exactly that reason, and
anyone doing control-plane work while a run is live should do the same.

### And the rung-3 rewind has never worked — fixed, `factory#39`

Attempt 3 was the rewind, and it died 64 seconds after it started. `plan-stderr.log`, in
full:

```
Failed to read output schema file /Users/james/factory/schemas/implement_result.schema.json: No such file or directory (os error 2)
```

`plan.start` handed codex `--output-schema <ctx.home>/…`. The plan runs **in the build
sandbox**, which mounts the target repo and not the factory, so that path does not exist
where codex reads it. The step had already copied the schema into the attempt directory and
then passed the original; `implement.start` has always pointed at its copy. One line.

**Nobody could see that, and that is the second half.** A rewind is one attempt with two
phases sharing one attempt directory, so the plan wrapper writes `plan-exit` — `exit` belongs
to the implement phase that follows. `sbx.poll` looked only for `exit`, found none, saw the
holder gone, and called a collectable failure `attempt-orphaned`. `RunHandle` now carries
`exit_name` and `reap` passes it, so the same run reaches `blocked [plan-incomplete]` — a
state that names its cause — instead of spending a rung on silence.

### FRO-11 is `failed`, and all three rungs went to things that were not the work

```
implementing (67 min, work committed) -> resumable   [host asleep]
implementing (resumed, exit 0, e00002b) -> resumable [recovered reconnect]
planning (64 s, exit 1) -> resumable                 [rewind schema path]
resumable -> failed                                  [ladder-exhausted, rung 4]
```

`ladder-exhausted` at 18:10:56 UTC. §5.3 gives `failed` exactly one edge and it is James
re-authorising spend. **The work is not lost**: a rewind does not reset the worktree, so
commit `e00002b` — the full detail route, its tests, and both reviews clean — is still in
the clone at `state/clone/frontend-harness/FRO-11/9bf53c8021a14b2f`.

**`factory resume FRO-11` once `#38` and `#39` are in.** Two of the three failure modes that
consumed this run cannot recur after those merge; the third is host sleep, which is still
unfixed and is held off manually with `caffeinate` for now.

**This is what item 4's frontend half actually bought.** Not a green pipeline — three
control-plane defects, each of which needed an unattended multi-hour run in a real sandbox to
show itself, and none of which any unit test in this repo could have produced. The python
half found none of them because BAC-6 happened to run while the laptop was awake and the
network held.

### Both human edges were then taken for the first time, and both were broken

James merged `#38`, `#39` and the docs, and reframed the goal: **the ticket does not have to
be executed well, the workflow has to run.** That turned BAC-6 from a question about test
quality into an unexercised edge, and both edges failed on contact.

**`blocked -> implementing` started an agent that was told nothing.** `continuation_prompt`
scanned the transition log only for entries into `resumable`, so a run sent back by a human
got an empty "How the previous attempt ended" section and a diff stat. Nothing in
`implement.start` reads `review-full.json` either. The reason was never missing from the
record — BAC-6's `reviewing -> blocked` row carries both `high` findings verbatim in `detail`
— so matching `blocked` alongside `resumable` was the entire fix (`factory#41`). Verified by
calling `continuation_prompt` against the live BAC-6 context before and after.

**`factory resume FRO-11 --authorise` did nothing at all:**

```
18:33:11  failed    -> resumable  [reauthorise-spend]   actor=human
18:33:11  resumable -> failed     [ladder-exhausted]    rung 4
```

Same second, before anything ran. `ladder_rung` reads the run's **lifetime** attempt count, so
a run that spent three attempts is at rung 4 for ever. §16.4 calls that command "James's
explicit act" and §5.3 gives `failed` exactly one edge; an edge that returns you to the state
you left is not an edge. Fixed in `factory#42`: the ladder counts attempts since the
authorisation, `max_total_attempts` still counts the whole run. Second attempt with the fix
live: `failed -> resumable -> implementing`, attempt 4.

Two corrections fell out of it, both worth knowing. `state_before(RESUMABLE)` answers `failed`
for a re-authorised run — not a state anything can resume into — so the re-entry now finds the
state that actually died, and a run that orphaned mid-verify re-runs its **gate report**
rather than restarting a 10 M-token implement it does not need. And the backoff read the same
lifetime count, so a human who had just typed the command was told to wait 300 s.

### Two things about BAC-6's findings, since the next session will see them

They were verified rather than taken on trust, by mutation, on a copy of the tree in a
scratchpad — the worktree itself was not touched:

- Changing one gold-set answer to a **different document of the same visibility** (`gold-01`,
  `external-sign-in` -> `external-profile-security`) leaves the suite green. The gold set is a
  query-to-document mapping and its tests assert only counts by category.
- Making the generator render **only the shared context header**, dropping every page body,
  also leaves it green. The PDF test asserts page counts and non-empty text.

Both `high` findings are therefore correct, and the `medium` is too (the README says four
page-specific fields, every document has exactly four, the test allows four to six). Under
James's framing this does not need adjudicating — but if the resumed attempt claims it fixed
them, those two mutations are how to check.

### One small follow-up taken

`.DS_Store` is now in `.gitignore` — it was the third session with two untracked files in
`git status`.

## The next session's first job

1. **Merge `factory#41`, then `#42`.** They are stacked in that order. Both are recovery-path
   fixes measured on live runs today, and **the working checkout already carries them** —
   which matters, because the daemon executes whatever is checked out in `/Users/james/factory`.
   Until they merge, a `git checkout` in that directory silently reverts them for the next tick.

2. **Both runs are done — read the section below before touching either.** BAC-6 has a PR to
   merge or reject; FRO-11 needs a judgement on three rewritten e2e assertions, and produces
   no PR until it gets one.

3. **Host sleep is still unfixed and is the last known way a healthy run dies.** `caffeinate`
   is the stopgap and it expires. The decision is in the sleep section above.

4. **Check the branch tip, not the PR state.** Five times now, the last one on the very PR
   that existed to repair the fourth. `git merge-base --is-ancestor <sha> origin/main`.

## The recovery paths were the whole story today — five defects, four of them fixed

Worth stating as one fact rather than five: **every defect found today lives on a path that
only runs after something else has already gone wrong**, and every one of them was invisible
to a green suite of 533 tests.

| # | defect | state | how it was found |
| --- | --- | --- | --- |
| 1 | host sleep reaps a healthy run as `attempt-orphaned` | **open** — §16.1 semantics decision | FRO-11 attempt 1, cross-checked against `pmset -g log` |
| 2 | a recovered `Reconnecting...` discards a finished attempt | fixed, `factory#38` | FRO-11 attempt 2, exit 0 with a valid result |
| 3 | the rung-3 rewind hands codex a host-only schema path, and its failure reads as an orphan | fixed, `factory#39` | FRO-11 attempt 3, dead in 64 s |
| 4 | a run sent back from `blocked` is told nothing about why | fixed, `factory#41` | called `continuation_prompt` against the live BAC-6 context |
| 5 | `--authorise` writes `resumable` and `failed` in the same second | fixed, `factory#42` | typed the command §16.4 names and read the transition log |

Defects 4 and 5 are the two that only appear when a *human* takes an edge, which is why four
unattended runs never surfaced them. Defect 5 in particular had never worked on any run since
the ladder was written: the rung is derived from the run's lifetime attempt count, so a run
with three attempts is at rung 4 for ever and `_LADDER.get(4)` is `FAIL`.

The method that found all five is unchanged and is stated at the bottom of this document.
Nothing here came from reading the code first.

## Item 4 is done — and the two stacks finished in different shapes

Both runs reached `awaiting_human` on 2026-08-23. That is Phase 5's "one ticket per stack,
end to end", and the difference between the two endings is the part worth keeping.

**BAC-6 — the whole path, including delivery.**

```
blocked -> implementing -> verifying -> reviewing -> pr_ready -> awaiting_human
```

Gate verdict `pass` (`ruff check`, `ruff format`, `mypy`, `pytest`; `pytest -m integration`
reported `not_applicable`, because the diff touches `data/`, `scripts/` and tests only and its
`when` never fired). Both reviews clean. PR **python-harness#70**. This is the python stack end
to end *through* the human-judgement edge rather than around it — the first run ever to leave
`blocked` that way.

The strengthened tests were verified rather than believed. Both mutations that defeated the
originals now fail: swapping one gold answer to another document of the same visibility fails
`test_gold_query_labels_match_the_approved_answers`, and rendering only the shared context
header fails `test_generator_creates_one_readable_pdf_per_document`.

**FRO-11 — all seven gates green, then a guard stopped it before `deliver`.**

```
implementing -> verifying [verdict: pass, 7 gates] -> reviewing -> awaiting_human [test-weakening]
```

`playwright` **passed in 4,572 ms inside an unattended sandbox**. That is item 2's `requires`
probe proved on the frontend side, in the only way it could be: four green unit tests and two
hand measurements never touched it.

Then `redphase.weakening_guard` found three assertion lines removed from the existing
`e2e/projects.spec.ts` and escalated — by design a judgement, not a block — so the run is at
`awaiting_human` **with no PR**, having never reached `deliver`.

The removed lines tested the stub route this ticket deletes, and their replacements are
strictly stronger (real heading text, a literal URL, focus management, and a return journey).
So it reads as a rewrite rather than a weakening — **but the guard cannot tell those apart,
which is exactly why it hands it over.** If the frontend half should also produce a PR,
`factory resume FRO-11 --from reviewing` re-runs review and carries it to `pr_ready`.

**What this leaves for item 4:** the monorepo dispatch test, which still cannot be written
until item 3 exists. Nothing else.

## What Phase 5 has left — items 3 and 4, and how to actually finish them

Read this whole section before touching either. Both were re-measured against the code on
2026-08-23, and the previous handoff's framing of item 3 was **wrong in James's favour**:
less of it is missing than that document claimed.

### 4. The end-to-end tests — both stacks are now running

**The python half ran end to end and is the strongest evidence Phase 5 has produced.**
BAC-6, attempt 1, unattended, 2026-08-23:

```
approved -> claimed -> context_loaded -> sandbox_creating -> sandbox_ready
  -> worktree_ready -> implementing (25 min) -> verifying -> reviewing -> blocked
```

Gate report **verdict: pass**, all five gates. It stopped at `review-finding` on two `high`
findings, both of them the reviewer catching vacuous tests — gold-set tests asserting label
counts rather than query-to-document mapping, and PDF tests asserting page counts rather than
page content. That is the pipeline working, not failing: the stop is a human judgement, and
`unblock-is-a-judgement` reserves both exits from `blocked`.

**Read the gate report on that run before anything else.** `pytest -m integration` came back
`pass`, and the worktree's vendored pin is `134b21c` with `requires` at line 77 of its
`harness.config.json` — python-harness#68 merged at 15:38 UTC and the run claimed at 15:44.
So **item 2's probe was exercised inside a real unattended sandbox and its requirement was
met**: `sbx` gives the VM its own Docker daemon, exactly as §19 predicted. Four green unit
tests and two hand measurements did not prove that. This run did.

**The frontend half needed a ticket that did not exist, and now has one.**

The FRO backlog was surveyed against the base ref and none of it could serve — recorded
below because the survey is what cost the time, not the conclusion:

| ticket | state | on `v2`? | why it could not be the test |
| --- | --- | --- | --- |
| FRO-1 | Todo | yes | parent spec — `no-parent-spec`, permanently and correctly |
| FRO-2, 3, 4 | Canceled | no | `wontfix` (reasonless); superseded by FRO-5/6/7 |
| FRO-5, 6, 7, 10 | Done | yes | `already-implemented` at condition 10 |
| FRO-8 | Todo | no | `wontfix`, no `ready-for-agent`; duplicate of FRO-10, work already in the tree |
| FRO-9 | Todo | no | no labels at all, and it is FRO-10's parent spec |

FRO-10 was tried first, as the smallest candidate, and intake refused it in **0 tokens**:
`approved -> blocked (auto) [already-implemented]` — condition 10
(`intake/linear.py:476`), the one P0-11 added. Two things that run settled:

- **A run row with a reason proves `needs-info` was absent at intake.** It is a *reasonless*
  condition, and `eligibility_verdict` makes a reasonless failure win, which produces no run
  row at all — just a log line. The label now on FRO-10 was written back by `steps/block.py`
  (§13.1), not left there by a human. Do not misread it.
- The previous handoff's "`needs-info` is currently cleared" was true of **BAC-6 only**.

**FRO-11, "See one project at `/projects/:id`", was filed 2026-08-23 and all 11 conditions
pass** — checked against the live ticket and the real repo *before* the daemon saw it, with
`evaluate_eligibility` called directly. The daemon claimed it and it is `implementing`.

The work is real rather than invented: FRO-1 put the detail screen out of scope and said the
link target "can be a stub route", and it still is one — `ProjectDetailStub.tsx` renders the
id back at the user and reads nothing, `ProjectsRepository` declares `listProjects` only, and
`src/test/msw/handlers.ts` serves `/projects` only. So condition 10 is safe by construction,
and the slice exercises the full frontend gate set **including the browser suite** — which
makes FRO-11 the first run to put the new `playwright` `requires` probe through an unattended
sandbox.

**Recorded honestly: the factory did not file it, and neither did the `mattpocock-skills`
system.** James authenticated the personal Linear MCP and asked for it directly, and it was
created through that. §24.1 and §13.1's create-issue-less keychain key are both intact — the
factory still cannot file a ticket, and did not. But the ticket in front of the frontend
stack's first end-to-end run came from an agent, not from the ticket system, and a reader
comparing this to §24.1 deserves to know that rather than infer it.

**What is left on item 4:** BAC-6 needs a human on its two `high` findings, and FRO-11 needs
to reach `awaiting_human`. Then the monorepo dispatch test, which still cannot be written
until item 3 exists.

### 3. First layer-C product — less is missing than the last handoff said

`python3 /Users/james/harness/scripts/new_project.py create <name> --api python --web react
--agnostic`, then a registry row with `stack = "monorepo"`.

**Correction, measured this session: `stack = "monorepo"` needs no `src/factory/` change to
be *accepted*.** `registry.py:190` reads it as a bare `str(raw["stack"])` — there is no enum
and no validation — and the two places that branch on the value already anticipate a
monorepo:

- `harness.py:34`, `EXPECTED_RUNNER = {"python": "uv", "frontend": "pnpm"}`. A stack that is
  not in it makes `cross_check_stack` return early, and `harness.py:169` returns early for
  `config.is_monorepo` regardless — with the comment "a monorepo root declares `apps` and no
  gates of its own; the dispatch in layer A resolves them per app, so there is nothing here
  to cross-check."
- Layer A already carries the dispatch. `apps` is in the schema, `is_monorepo` is
  `bool(self.apps)` (`harness.py:76`), and `load_harness_config` refuses a config with
  neither `gates` nor `apps` (`harness.py:131`).

**What is genuinely missing is one line and two acts:**

- `_SENSITIVE_DIRS` (`steps/review.py:87`) has `python` and `frontend` entries and no
  `monorepo` one, so Tier-2's sensitive-path trigger would be empty for the new project.
  That is carried defect 4's family — the `frontend` entry already matches nothing — so fix
  the two together rather than adding a third guess.
- **`new_project.py create` does `git init` and commits locally. It does not create a GitHub
  repository**, and the registry row requires a `remote`. Someone has to `gh repo create`.
- §19's approval boundary reserves **the first layer-C repository's creation** to James, and
  he has to choose the name. Asked on 2026-08-23 and answered **not now** — so do not
  scaffold anything until he says otherwise.

---

## Open decisions

**Completing a run resets its `gc` clock, and nobody has decided whether that is right.**
Carried forward unchanged, third session running — it is a decision, not a bug, and
**nothing was changed**.

`gc.py:97` computes age from `run.updated_at`, and completing *writes* the run, so all four
completed runs went to `0.0` days old and `worktree_days = 7` (`config/projects.toml:58`)
holds them for a fresh week.

- *For:* the floor is a safety margin after a run becomes collectable, and it only becomes
  collectable at the merge. §16.5's opening line says every rule is a floor, never a promise.
- *Against:* the run stopped being worked on at `awaiting_human`. Late bookkeeping granting a
  fresh week is not what "kept for 7 days" reads like.

Changing it means measuring from the `pr_ready -> awaiting_human` transition rather than
`updated_at` — small in `_collect_run`, but it changes what §16.5 *means*. Two worktrees wait
on it: `python-harness/.factory/worktrees/BAC-4` and `frontend-harness/.factory/worktrees/FRO-7`.

---

## Defects carried forward

None block the remaining Phase 5 work.

1. **`cancel` cannot restore a tracker it did not set — which is the successful case.** A run
   that opened a PR has been moved to `In Review` by the GitHub integration, so cancelling it
   leaves the ticket at a non-`Todo` state and the poller skips it for ever. It prints
   `left <T> at '<state>' (not the state the factory set)` and nothing says the ticket is now
   unclaimable.
2. **The re-run ceiling counts orphans, not attempts — and host sleep manufactures them.** `resumable_reentries` counts
   `<state> -> resumable` transitions, so a deterministic environment failure burns the budget
   in three ticks and fails a run whose work is good. `requires` narrows this — a missing tool
   now blocks at `gates-incomplete` instead of looping — but every other deterministic
   environment failure still burns the budget. **Measured 2026-08-23: so does a laptop
   going to sleep**, which is not an environment failure at all — see the FRO-11 section.
3. **`kill_agent` cannot stop a hung `verifying` gate.** Its body is a node gate report, not
   codex, so neither `pkill -f "codex exec"` nor `pkill -x codex` matches it. Pre-existing.
4. `_SENSITIVE_DIRS["frontend"]` matches nothing, and `deliver._archive` returns silently when
   the attempt directory is missing.
5. **New, and mild:** the frontend probe writes `/tmp/playwright-probe.png`, so it is POSIX-only.
   Nothing in use runs `gate_report.mjs` on Windows — the Stop hook's `STOP_KINDS` excludes
   `e2e` and the sandboxes are Linux — and a Windows run would report the gate `unavailable`
   rather than falsely `pass`, so it fails safe. Recorded rather than fixed.

## Small follow-ups, deliberately not built

- The §18.5 console has Suspend and Resume but no **Complete**. The CLI and the console POST
  path share their machinery elsewhere, so the divergence is worth closing.
- Nothing surfaces "PR merged — ready to complete" in `factory status`. The tick could notice
  and *say* so without taking the edge, which stays reserved to `merge-is-james`.
- ~~`.DS_Store` wants a `.gitignore` line.~~ Done this session.

---

## The method, unchanged — and item 2 is the strongest case for it yet

**Both prescribed probes passed their own unit tests and neither detected the condition it
was written for.** Four green tests in `harness#18` proved the *mechanism*; nothing in them
could have proved that `playwright --version` answers a different question than "are the
browsers here". Only running it against an empty `PLAYWRIGHT_BROWSERS_PATH` did.

Two smaller instances from this session:

- **The local `v2` in `frontend-harness` was two commits behind `origin/v2`**, and the first
  branch was cut from it. `git fetch` before branching, always; FRO-7 had merged since.
- **A green suite is a claim, not evidence.** The four mutations against the python probe were
  each tried and each caught, including the one that mattered most: treating an unfindable
  Docker client as met.

**And before believing a handoff — including this one — grep the tree it describes.**
