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
| BAC-6 | Linear ticket moved to `Todo` by James 2026-08-23; its run is still `suspended` at attempt 7 and **still holds the ticket** — see item 4 |

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

---

## The next session's first job

1. **If #46 and #68 have merged, item 2 is closed.** Check with `gh pr view`, and check the
   *branch tip* rather than the PR state — a previous session lost two commits to a merge
   that raced the last push by 24 seconds.
2. **Then fix layer A's schema example.** `plugins/harness/schema/harness.config.schema.json`
   offers `["pnpm", "exec", "playwright", "--version"]` as an example `requires` value, and
   this session measured that it does not detect a missing browser. It is a docstring
   teaching the wrong probe. Two candidate replacements: the screenshot argv now in
   `frontend-harness`, or dropping the example to the `docker info` one alone.

   **This was deliberately not done this session**, because `vendor-freshness.yml` in both
   consumers compares the vendored pin against `harness@v2` HEAD; a harness commit pushed
   while #46 and #68 were open would have made both PRs stale. Do it after they merge, and
   re-vendor as its own pair of PRs.

---

## What Phase 5 has left — items 3 and 4, and how to actually finish them

Read this whole section before touching either. Both were re-measured against the code on
2026-08-23, and the previous handoff's framing of item 3 was **wrong in James's favour**:
less of it is missing than that document claimed.

### 4. The end-to-end tests — one ticket per stack

**James moved BAC-6 to `Todo` in Linear on 2026-08-23. That is necessary and it is not
sufficient, and the reason is worth understanding before you spend a tick wondering why
nothing happened.**

BAC-6 still carries a **`suspended`** run at **attempt 7**, `resume-target-invalid`. Only
`completed` and `cancelled` are terminal (`machine.py:57`), and `_LIVE_RUN_INDEX`
(`store.py:87`) is derived from that set, so a `suspended` run is **live** and holds its
ticket. Intake reads `store.live_run_for_ticket` and skips any ticket that has one —
`cli.py:703`, which in `--verbose` prints `BAC-6 already has a live run at suspended`. The
poller will therefore never look at BAC-6 while that row exists, no matter what Linear says.

The same is true of every `blocked` row on the board, and deliberately: `store.py:80` says
"`blocked` is deliberately live: it is a stop, not an end, so a blocked run still holds its
ticket until someone cancels it. That is the contract, not an oversight."

**So the python half is a three-step, and step 1 is James's:**

1. **`factory cancel BAC-6`.** `cancelled` is in `HUMAN_ONLY_DESTINATIONS` under
   `abandon-is-james`, so an agent cannot do this and should not try to.
2. **Check the ticket is still `Todo` afterwards — read it, do not assume.** This is where
   carried defect 1 lands: `cancel` restores the tracker only to the state *the factory*
   set, and James set this one by hand. The likely output is
   `left BAC-6 at 'Todo' (not the state the factory set)` with no write, which is the
   wanted outcome here — `Todo` is exactly where it needs to be. But defect 1 exists
   because that path has surprised this system before.
3. **Then run it.** BAC-6's 11 intake conditions all passed on 2026-08-23, which is also
   what settled that a `Canceled` parent satisfies the parent-spec condition. The new row
   starts at attempt 1; the 7 attempts belong to the cancelled run.

**The frontend half has no ticket yet, and the same two-step applies to whichever you pick.**
FRO-5, FRO-6 and FRO-10 each sit behind a live `blocked` run at `state-not-todo`, so each
needs its run cancelled *and* the ticket moved to `Todo`. **FRO-7 is not a candidate** — its
work merged as `frontend-harness#45`. FRO-1 is the parent spec and is refused permanently
and correctly.

`needs-info` is currently cleared by James, so nothing is held at intake by a label.

**The third test cannot be written until item 3 exists.** "A monorepo dispatch test proving
a CSS-only change runs no Python gate" needs a monorepo. Do items in the order 3 → 4, or
deliver item 4's two stack tickets and say plainly that the dispatch test is outstanding.

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
