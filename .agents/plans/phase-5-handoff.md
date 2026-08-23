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
| BAC-6 | `suspended` at attempt 7, deliberately held. Do not touch |

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

## What Phase 5 has left

### 3. First layer-C product — not started, and it needs James twice

`python3 /Users/james/harness/scripts/new_project.py create <name> --api python --web react
--agnostic`, then a registry row with `stack = "monorepo"`. §19's own approval boundary
reserves **the first layer-C repository's creation** to James, and `stack` accepts only
`python` / `frontend` today, so this is a real `src/factory/` change and not only scaffolding.
Asked and answered on 2026-08-23: **not now.**

### 4. Tests — blocked, and only James can clear it

One ticket per stack end to end, plus a monorepo dispatch test proving a CSS-only change runs
no Python gate. Unchanged from the last handoff: Todo holds only BAC-1 and FRO-1, and both are
correctly and permanently refused at intake with `no-parent-spec` because they *are* the parent
specs. Moving a `Canceled` ticket to `Todo` works — BAC-6 did exactly that on 2026-08-23 and
all 11 intake conditions passed, which settled that **a `Canceled` parent satisfies the
parent-spec condition**. It is a Linear move, not a code problem: §24.1, the factory reads
tickets and never files them.

`needs-info` is currently cleared by James.

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
