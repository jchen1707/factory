# Console UI — extension plan

## Context

There is already a UI. `factory serve` (§18.5) is a FastAPI/uvicorn console bound to
`127.0.0.1:7717`, server-rendered with `string.Template` pages and SSE for live updates — no
build step, no bundler, no frontend framework. This plan **extends it**, it does not build a
second one. James asked for four things; two already exist in part, two are gaps. He also
asked for nice-to-haves at the planner's discretion.

**One access problem to fix first.** `factory serve` is a *foreground* process — it only
serves while a terminal holds it open. It is not the always-on launchd daemon. James hit a
dead URL because nothing was serving. The plan adds a one-line README/runbook note and
considers a convenience (below).

## What exists today (the delta baseline)

Routes in `src/factory/console/app.py`, data in `src/factory/console/views.py`:

| Route | What it shows | Edit? |
| --- | --- | --- |
| `GET /` + `GET /sse/board` | the runs board: ticket, project, state, attempt, context %, tokens, spend, liveness | read-only, SSE-live |
| `GET /runs/{ticket}` + `GET /sse/tail/{ticket}` | transition timeline, gate report, review findings, artifacts, live `events.jsonl` tail | read-only, SSE tail |
| `POST /runs/{ticket}/{action}` | controls: `suspend`, `resume`, `resume-planning`, `cancel`, `retry` — via `cli.dispatch_control` (same path as the CLI, records `actor=human`) | **stop only** |
| `GET /runtimes` | `sbx ls --json` joined to runs: sandbox, state, workspace, ports, template, runs-using, last denial | read-only |
| `GET /config` + `POST /config/models` | `models.toml`: model + effort per role, USD budget; validated before write | **model config ✓** |

So: **model config per agent already exists** (ask 1), and **run/sandbox state exists as two
views** (ask 3, split). The gaps are **start a run** (ask 4 — only stop controls exist) and
**lighthouse / opt-in gate config** (ask 2 — not surfaced).

## Ask 1 — model config per agent: enhance what's there

`/config` already edits model + effort per role and validates (reviewer ≠ builder, model in
catalogue, budget). Enhance:

- **Dropdowns, not free-text.** `views.config_view` already has the role list; add the model
  catalogue from `routing.models` (`config/models.toml`'s `[models.*]` entries, excluding
  `hidden = true`) so each role is a `<select>` of real slugs. Effort becomes a `<select>` of
  that model's `supported_efforts`. This kills the "typo a model slug" failure mode at the
  form, not at the validator.
- **Show what each role is for** (planner/builder/reviewer/synthesiser/documenter) and the
  context-window + effective % beside the chosen model, so the operator sees what they are
  trading. The numbers already live in `routing.models`.
- **Per-role budget** is out of scope (the ceiling is per-run, §4.5) — leave it.

Files: `console/app.py` (`config_view` route, render the dropdowns), `console/views.py`
(`ConfigView` gains the catalogue + per-model effort lists), `templates/config.html`. Reuse
`_rewrite_models_toml` + `_validate_models_toml` unchanged — the write path already works.

## Ask 2 — lighthouse / opt-in gates: a per-project allowlist (the `--all` lever)

**The boundary.** Lighthouse's *definition* lives in `frontend-harness`'s
`harness.config.json` (layer B); the factory must not write layer-B files. What the factory
*owns* is **which opt-in gates its verify step asserts and accepts as evidence** — the lever
that used to be `--all` before FRO-7 showed `--all` was a bug (verify.py:405–413). Today
`_asserted_opt_in_gates` (verify.py:392) asserts exactly the opt-in gates the agent *claims*,
intersected with `enabled` in the consumer config. James wants operator control of that
set, per project.

**New file `config/verify.toml`** — a per-project opt-in gate allowlist, separate from
`projects.toml` so `projects.toml` stays fully read-only in the UI (its sandbox-spec keys are
fixed at creation):

```toml
# Per-project opt-in gate policy — §15.2. An opt-in gate (kind e2e/integration) named
# here is the only one the verify step asserts and accepts as evidence. An absent
# project = assert every enabled opt-in gate the agent claims (the current default).
[python-harness]
opt_in = []                 # python has no opt-in gates

[frontend-harness]
opt_in = ["playwright"]     # assert playwright; never lighthouse
```

**How it flows** (the subtle part — claim and assertion must agree):

- `verify._asserted_opt_in_gates` intersects the agent's claim with the allowlist (today it
  intersects claim ∩ `enabled`; add ∩ allowlist). A gate not in the allowlist is not
  asserted → `gate_report.mjs` never runs it → it returns `not_applicable`.
- `verify._evidence_mismatch` must **tolerate** a claim for an opt-in gate the operator
  excluded: an agent that ran lighthouse anyway is *ignored*, not flagged as a mismatch
  (the operator said don't run it; a stale claim is not a lie). Implement by dropping
  matched opt-in gates not in the allowlist from the mismatch check.
- The implement prompt (implement.py:542–550) does **not** enumerate gates, so no prompt
  change is needed — the toggle is purely a verify filter, which is why this is clean.

**UI.** A new `GET /verify` + `POST /verify` (or fold into `/config` as a second section):
per project, a multi-checkbox list of the opt-in gates that project's `harness.config.json`
declares (read via `factory.harness.HarnessConfig`), with the allowlist pre-checked. The
write path mirrors models.toml: targeted line rewrite → validate (names must be real opt-in
gates in the resolved config) → write only if valid. Hot-reloaded per request.

Files: `config/verify.toml` (new), `src/factory/registry.py` or a small
`src/factory/verify_policy.py` loader, `src/factory/steps/verify.py` (`_asserted_opt_in_gates`
+ `_evidence_mismatch`), `console/app.py` + `console/views.py` + `templates/verify.html`.

## Ask 3 — run state by agent/sandbox: unify the two views

Board and runtimes are separate today. James wants to see runs "along each agent/sandbox."

- **Add a `sandbox` column to the board** (`RunRow` already has the run's project; the
  attempt row's `sandbox` is in the store — surface `factory-build-<project>` /
  `factory-review-<project>` and the codex **session id** from the attempt). The board
  becomes "run ↔ sandbox ↔ agent session" in one row.
- **Run detail gains an "agent" line**: the codex session id, the sandbox name, and a
  link to `/runtimes#<sandbox>`.
- **Runtimes gains the run's current state** beside the run-using it (it already lists
  `runs_using`; add the state of each). So both directions of the join are visible.
- Optionally a **per-sandbox detail page** `/sandboxes/<name>` showing the run(s) on it,
  the workspace, the last denial, and a link to the run — but only if the unified columns
  don't already answer it. (My discretion: ship the columns first, add the page only if the
  board feels crowded.)

Files: `console/views.py` (`RunRow`, `RuntimeRow`, `runtimes`), `console/app.py` (board +
runtimes render), `templates/board.html`, `templates/runtimes.html`,
`templates/run_detail.html`.

## Ask 4 — start/stop runs: add "start" (both flavours)

Stop already exists (suspend/resume/resume-from-planning/cancel/retry via
`dispatch_control`). Start does not. James chose **both** a named-ticket launcher and a
tick-now button.

**`Tick now` button** (top of the board). Runs one `factory tick --once` in a background
subprocess (`subprocess.Popen` with `start_new_session=True`, the same durability pattern as
`exec_detached`), so the HTTP request does not block on a tick that may reap a long run. The
button reuses the existing tick entrypoint `cli.cmd_tick`; the board's SSE refresh shows the
result. Add `tick-now` to `cli._CONTROLS` and route it through `dispatch_control` (or a sibling
endpoint) so it is recorded as `actor=human`.

**`Run <ticket> now` launcher** (a small form on the board). Spawns `factory run <ticket>`
detached (background `Popen`, `start_new_session=True`) — **not** inline, because `factory
run` blocks for the whole agent turn and a request that blocked on it would hang the console.
`factory run` already enforces the full eligibility contract (parent spec, acceptance
criteria, no open PR, `tracker.team` match), so launching from the UI does not bypass the
gate — it just expresses James's "go" via a button instead of a terminal. The detached run
writes its pid where the next tick's reaper can find it, exactly as a typed detached run
would.

Both go through `policy.requires_human` (a UI start is still a human action) and are
recorded with `actor=human`. Neither adds a Merge button (still absent, still test-asserted).

Files: `cli.py` (`_CONTROLS` + a `start`/`tick-now` dispatch, a `_launch_detached` helper
reusing the `factory run`/`tick --once` entrypoints), `console/app.py` (the POST endpoints +
a small form on the board), `templates/board.html`, `templates/controls.html`. The detached
launch must not duplicate the pipeline — it calls the existing `cmd_run`/`cmd_tick`.

## Nice-to-haves (my discretion)

- **"Start the console" affordance + doc fix.** Add a one-line note to the README and
  `docs/runbook.md` that `factory serve` is foreground. Consider an `ops/` launchd plist
  for the console (like the tick daemon) so it is always on at `127.0.0.1:7717` — loopback
  only, no credential, so the Phase 7 off-machine-auth concern does not apply. Ship the doc
  fix; make the plist optional (James's call to load it).
- **Board filter/search** by project and state (a `?project=&state=` query, no JS framework).
- **Linear deep-link.** The run detail links to the PR already; add a link to the Linear
  ticket.
- **A "blocked reasons" summary** at the top of the board: how many runs are blocked and by
  which reason (the data is already in `RunRow.blocked_reason`).
- **Model catalogue browser** on `/config`: list every model in `routing.models` with its
  display name, supported efforts, context window, and `hidden` flag — so the dropdowns are
  grounded in a visible table.
- **Confirm destructive controls.** A tiny `onclick="return confirm(...)"` on `Cancel` (and
  on `Run now`/`Tick now`) so a misclick does not spawn or kill a run.
- **Global transitions feed.** A `/audit` page showing the most recent N transitions across
  all runs (the data is `store.transitions` per run; aggregate it). Cheap, and it is the
  "what did the factory just do" view the board is not.
- **Spend-over-time.** A small sparkline per run from the `costs` table. Defer if it needs a
  chart lib — the console is deliberately dependency-free; a CSS bar per attempt is enough.

Out of scope (deliberate): any off-machine access (Phase 7), any Merge button, any
edit of layer-B gate definitions, any new runtime dependency.

## Architecture & constraints (unchanged from the existing console)

- `string.Template` pages, SSE, no build step, no new dependencies. New views follow the
  `_render`/`_page` shell.
- One producer: all data goes through `console/views.py`, shared with the CLI, so the page
  and `factory status`/`runtimes`/`config` cannot disagree.
- Hot-reload: every request re-reads `projects.toml`/`models.toml`/`verify.toml` via `_cfg()`;
  a config edit takes effect without a restart.
- Every control goes through `cli.dispatch_control` (or the new start/tick-now siblings) —
  never a second opinion about what is allowed. `policy.requires_human` gates them; the
  transition is recorded `actor=human`.
- Write paths validate **before** they touch the disk (models.toml already does; verify.toml
  does the same — parse, resolve against the project's real gates, write only if valid).
- Fresh `Store` per request thread (the existing `_store_for_this_thread` seam) — new
  endpoints reuse it, do not hold a connection across threads.
- No credential in the page. The console reads SQLite + `sbx ls` + config files; it holds no
  Linear/GitHub key.

## Files to change

**New:** `config/verify.toml`; `templates/verify.html` (opt-in gate editor); possibly
`templates/audit.html`; possibly `ops/com.jchen.factory.console.plist`.

**Edit:**
- `src/factory/console/app.py` — dropdown render in `/config`; new `/verify` GET/POST;
  board + runtimes sandbox/agent columns; `Run now` form + `Tick now`/`start` POST endpoints;
  optional `/audit` and `/sandboxes/{name}`.
- `src/factory/console/views.py` — `ConfigView` gains the model catalogue + per-model
  efforts; `RunRow`/`RuntimeRow` gain sandbox/session/state; a verify-policy view; an audit
  aggregation.
- `src/factory/steps/verify.py` — `_asserted_opt_in_gates` and `_evidence_mismatch` intersect
  the per-project allowlist.
- `src/factory/registry.py` (or new `verify_policy.py`) — load `config/verify.toml`, validate
  gate names against the resolved `HarnessConfig`.
- `src/factory/cli.py` — `tick-now`/`start` control slugs in `_CONTROLS`; a `_launch_detached`
  helper reusing `cmd_run`/`cmd_tick` entrypoints.
- `templates/board.html`, `templates/runtimes.html`, `templates/run_detail.html`,
  `templates/config.html`, `templates/controls.html`.
- `README.md`, `docs/runbook.md` — the "serve is foreground" note + the new views.

Pattern note: the verify.toml writer mirrors `_rewrite_models_toml` (targeted line rewrite,
preserve comments, validate before write) — do not round-trip the file through a TOML
serializer.

## Verification

1. **Gates.** `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run
   pytest` — the console's own test suite (`tests/.../test_console.py`) covers the existing
   views; extend it for the new routes and the verify-policy view. The "no Merge button"
   assertion stays green.
2. **Opt-in gate toggle (mutation test).** With `frontend-harness` `opt_in = ["playwright"]`
   and a fixture `gates_run` that claims both `playwright` and `lighthouse`:
   - `_asserted_opt_in_gates` returns only `playwright`;
   - `_evidence_mismatch` returns `[]` (lighthouse claim ignored, not a mismatch);
   - revert the allowlist to absent → `lighthouse` is asserted and a lighthouse claim with a
     `not_applicable` report is a mismatch (the old behaviour). Red without, green with.
3. **Start/tick-now.** A `FakeSandbox`-backed test that POSTs `tick-now` and `start` and
   asserts a detached launch is requested and the transition is `actor=human`. No real
   `sbx`/model call.
4. **Manual.** `uv run factory serve`; open `http://127.0.0.1:7717/`: edit a role's model
   from the dropdown and see `/config?saved=1`; set `frontend-harness` opt-in to
   `["playwright"]` in `/verify` and run a frontend ticket whose agent claims lighthouse —
   confirm the gate report shows lighthouse `not_applicable` and the run does not block on
   `env-gate-failed`; click `Run now` on a `ready-for-agent` ticket and watch the board
   advance; click `Tick now` and watch a queued ticket get claimed.
5. **Access doc.** A reader who follows the README's serve line gets a live page, not a dead
   URL.