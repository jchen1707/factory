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

| | |
| --- | --- |
| `factory@main` | `507363d`. This branch adds `9e33944` (the delete above) on top of the merged handoff commits |
| suite | 533 passed, mypy clean on 71 files, ruff check + format clean — re-measured this session |
| `harness@v2` | `134b21c`. Unchanged this session, deliberately — see the schema-example item below |
| `frontend-harness` | PR **#46**, `chore/vendor-gate-requires`, 3 commits, **all 7 checks green**, unmerged |
| `python-harness` | PR **#68**, `chore/vendor-gate-requires`, 2 commits, **all 5 checks green**, unmerged |
| vendor pins | both bumped `3248fbe` → `134b21c` in those PRs; still `3248fbe` on both `v2` until they merge |
| BAC-6 | **running.** Old `suspended` run cancelled by James 2026-08-23; the daemon re-claimed it and it is at `implementing`, attempt 1 |
| FRO-10 | tried as the frontend test and refused at intake, `already-implemented` — 0 tokens spent. The whole FRO backlog was exhausted; see item 4 |
| FRO-11 | **new ticket, filed 2026-08-23**, all 11 intake conditions pass. Claimed by the daemon and `implementing` |
| `harness#19` | open — the `requires` examples fix. **Do not merge before FRO-11's PR lands**; see below |
| `factory#36` | open — two handoff commits stranded when #35 merged ahead of a push |

**Merging #46 and #68 is James's, and it is the only thing between here and item 2 being
closed.** Nothing else in this document depends on them.

One host-state change to know about: **`uv sync --extra app` was run in `python-harness`**,
because proving the new probe's *met* branch needed the extra actually installed. It is the
repo's own documented setup command and it was the host venv, not a sandbox one, so it is
the safe direction of the bind-mount hazard. It also removed two `mypy` "unused type: ignore"
errors that were purely artifacts of the extra being absent.

---

## What landed this session — item 2 is built, proved, and waiting on a merge

The mechanism (`harness#18`, `requires`) and layer D's block message (`factory#33`) were
already in. This session did the three consumer steps the last handoff listed, and both
of them turned out to need a different probe than the one that was prescribed.

### The prescribed probes were wrong, and executing them is the only reason that is known

**`frontend-harness`: `pnpm exec playwright --version` exits 0 with no browsers at all.**
Measured against an empty `PLAYWRIGHT_BROWSERS_PATH`. It probes the npm package, and the npm
package is never the missing half: `network_allow` for the frontend build sandbox is
`registry.npmjs.org`, `*.npmjs.org`, `github.com` — **not `cdn.playwright.dev`** — so
`pnpm install` always succeeds and the browsers can never arrive. That probe would have
reported "environment met" in precisely the environment it was written to catch.

The gate now probes `pnpm exec playwright screenshot about:blank /tmp/playwright-probe.png`,
which launches the browser and so answers the question actually being asked, in ~0.4 s. It
writes to `/tmp` because the probe runs with cwd at the repo root and a probe that leaves a
PNG in the tree shows up in the diff.

**`python-harness`: `docker info` covers one of the gate's two requirements,** exactly as the
last handoff suspected. It is not a theoretical gap — measured on this host with Docker
running and the extra unsynced, `uv run pytest -m integration` errored on `import pydantic`
in three files, because **pytest collects the whole `tests/` tree before any marker filter
applies**, so a missing extra fails collection repo-wide rather than only in the integration
tests. `docker info` would have called that environment met.

The probe is now `uv run python scripts/integration_env_probe.py`: `shutil.which` + `docker
info` for the daemon, `importlib.util.find_spec` for `psycopg` and `pgvector`, and a stderr
message naming both halves and the fix. Six tests, four mutations tried, all four caught.
`scripts/` is a new directory, so it is named in mypy's `files` in the same change that
created it — that gate's own caveat says to.

### Proved end to end, in the real repos, not in fixtures

| repo | environment | `--gate` result |
| --- | --- | --- |
| `frontend-harness` | browsers present | `pass`, exit 0, 2869 ms; verdict `pass` across all 7 gates |
| `frontend-harness` | `PLAYWRIGHT_BROWSERS_PATH` empty | `unavailable`, `exit: null`, verdict `incomplete`, process exit 3 |
| `python-harness` | Docker up + extra installed | `pass`, exit 0, 2009 ms; verdict `pass` across all 5 gates |
| `python-harness` | `docker` off `PATH` | `unavailable`, `exit: null`, verdict `incomplete`, process exit 3 |

In both `unavailable` cases the entry's `outputTail` carries the probe's own message, which
is what a human reads out of the `gates-incomplete` block.

### One defect found on the way, fixed in the same PR

`.factory/worktrees/<TICKET>/` is a full second copy of the repository, so **every ignore in
`eslint.config.js` and `.prettierignore` is defeated one level down inside it** and the other
checkout's findings are reported as this one's. `vite.config.ts` learned this in
`frontend-harness@47ca3b5`; the other two had not. Reproduced with the FRO-7 worktree
present — 2 eslint errors, 1 warning, 1 prettier warning, all four in the other checkout —
and clean afterwards with the worktree still there. `python-harness` does not have the
same hole: `ruff` and `mypy` both skip dot-directories.

### A cost worth not repeating

`frontend-harness` CI has a **`body` job that checks the PR body against
`.github/PULL_REQUEST_TEMPLATE.md`** and fails the PR when `## Summary`, `## What changed`,
`## How to demo` or `## Evidence` is missing. One round trip was spent on it. `python-harness`
has no such job — the asymmetry is real, and the frontend template is the stricter one.
**Write the frontend PR body from the template, first time.**

**And a second round trip, in `harness`, for a different reason than the one this document
warned about.** Prettier passed. `generate` failed on `check_version_bump`: a layer A file
changed while `plugins/harness/.claude-plugin/plugin.json` stayed at its version, and
`claude plugin update` compares the **version field, not the sha**, so consumers would have
been told they were already up to date. The local run missed it because **`scripts/check.py`
skips that check entirely without `--since`**. The plan's own command is
`python3 scripts/check.py --since=<base>` — run it that way, and bump the plugin version in
the same commit as any layer A content change.

---

## The next session's first job

1. **Merge order matters now, and getting it wrong red-lines two repositories.**

   `harness#19` fixes the `requires` examples in `plugins/harness/schema/harness.config.schema.json`
   — both of the ones `harness#18` shipped were measured this session to pass in the very
   environment they exist to catch. It is documentation only, all three harness gates green.

   **But `schema/` is vendored.** `vendor_sync.py check` compares vendored *content*, not the
   sha: a commit that changes a vendored file is a **`stale pin` failure**, not the "behind but
   nothing changed" note. So merging #19 turns the `freshness` job red on every open PR in both
   consumers until each re-vendors.

   The order is: **FRO-11's PR lands → merge `harness#19` → re-vendor both consumers as their
   own pair of PRs.** `factory#36` is independent and can merge whenever.

2. **BAC-6 is waiting on James, not on an agent.** It is `blocked` at `review-finding` with
   two `high` findings, and both exits from `blocked` are `unblock-is-a-judgement`. An agent
   can read the findings and propose a repair; it cannot take the edge. Do not try.

3. **Check the branch tip, not the PR state.** `factory#35` merged while two commits were still
   in flight to its branch, and both were stranded — the third time this session's family of
   races has appeared. `git merge-base --is-ancestor <sha> origin/main` answers it in one
   command, and the remote ref is what makes recovery possible.

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
2. **The re-run ceiling counts orphans, not attempts.** `resumable_reentries` counts
   `<state> -> resumable` transitions, so a deterministic environment failure burns the budget
   in three ticks and fails a run whose work is good. `requires` narrows this — a missing tool
   now blocks at `gates-incomplete` instead of looping — but every other deterministic
   environment failure still burns the budget.
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
- `.DS_Store` and `.agents/.DS_Store` are untracked in the working tree and still want a
  `.gitignore` line.

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
