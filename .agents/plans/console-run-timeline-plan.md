# Console UI — run timeline (agent status + runtime waterfall)

## Context

A visual mockup (`/tmp/factory-run-waterfall.html`, kept out of the repo) proposed a new
read-only console view inspired by two sources:

- **disler/super-simple-software-factory**'s visualizer — a swim-lane waterfall with phase
  blocks on a time axis (width = runtime, color = lane), a sessions list, and per-phase
  tool-call drill-down.
- **Google's Gmail MCP server** doc — an "installed servers / Authed" status panel: one
  card per server with a live/authed badge and the tools it exposes.

The factory's analogue: one card per **agent role** (planner/builder/reviewer) with a
live/done/idle badge, a **swim-lane waterfall** of the run's states on a time axis, and a
per-tool-call drill-down of the active agent turn. This plan extends the existing console
(`factory serve`, §18.5) — it does not build a second UI. It is a sibling to
`console-ui-plan.md`; that plan covers model dropdowns, opt-in gates, start controls and
board columns. This one covers the run-lifecycle/timeline view.

## The honesty table — mockup vs what the factory records

The console's own rule (events.py: "never show a number it cannot defend") decides every
row below. The mockup aspirationally showed per-call durations and per-role token
breakdowns; the factory does not record what would defend them, so the plan splits.

| Mockup showed | Factory records it? | Source | Phase |
| --- | --- | --- | --- |
| waterfall block start + width (runtime) | **yes** | `Run.created_at` + `store.transitions` `at` (store.py:520) | 1 |
| lane (engineer / planner / builder / reviewer / code) | **yes** | `State` → lane (see mapping) | 1 |
| dead first attempt (hatched) | **yes** | a block whose exit transition is `→ resumable`/`→ failed` (machine.py:50,132); resume = `resumable → implementing` | 1 |
| live/in-progress block (pulsing edge) | **yes** | last transition `at` → `time.time()` | 1 |
| rung label (RESTART/RESUME/REWIND) | **yes** | `recovery._LADDER[attempt]` (views.py:60) | 1 |
| per-agent card: model, effort | **yes** | `routing.roles[role]` | 1 |
| per-agent card: status live/done/idle | **yes** | current `State` vs `_AGENT_ROLE` (events.py:34): live = current state's role, done = a role whose state is past, idle = not yet reached | 1 |
| per-agent card: current activity | **yes** | latest `item.completed` via `read_turn_view` (events.py:56) | 1 |
| per-agent card: heartbeat age | **yes** | `attempt_dir/heartbeat` mtime (views.py:124) | 1 |
| context % (live agent) | **yes** | `context_percentage(view, state, routing)` (events.py:96) | 1 |
| aggregate tokens + spend (run-level) | **yes** | `store.spend(run.id)` (views.py:129) | 1 |
| **per-call duration** | **no** | `events.jsonl` has no timestamp field (verified: events carry only `type`+`item`/`usage`/`message`; codex-events.md confirms) | 2 |
| **per-role token breakdown** | **no** | one `events.jsonl` per attempt accumulates all agent turns; no event timestamps to window a turn against a state | 2 |
| **per-role spend** | **no** | `store.spend` is per-run aggregate, not per-role | 2 |
| **context "peaked %"** | **no** | `read_turn_view` returns the *latest* turn's usage, not the peak | 2 |

**Phase 1 ships under the existing console constraints** — read-only, no new writer, no
new dependency, `string.Template` + SSE, fresh `Store` per request. **Phase 2 adds a
writer** (an observation-timestamp sidecar emitted by the agent step) and is a boundary
change James approves separately; it is sketched here so the Phase 1 shapes do not paint
Phase 2 into a corner.

## Phase 1 — the read-only timeline view

### Lane mapping (State → lane)

A pure function next to `_AGENT_ROLE` in `console/events.py` (or `views.py`), so the
waterfall and any future CLI view share it:

```python
_LANE: dict[State, str] = {
    State.APPROVED: "engineer",  # ready-for-agent applied (James)
    State.AWAITING_HUMAN: "engineer",  # the merge waits for James
    State.CLAIMED: "code",
    State.CONTEXT_LOADED: "code",
    State.SANDBOX_CREATING: "code",
    State.SANDBOX_READY: "code",
    State.WORKTREE_READY: "code",
    State.VERIFYING: "code",
    State.PR_READY: "code",
    State.PLANNING: "planner",
    State.IMPLEMENTING: "builder",
    State.REVIEWING: "reviewer",
    # BLOCKED / RESUMABLE / SUSPENDED / FAILED / CANCELLED / COMPLETED: rendered as
    # markers, not lane blocks (see below).
}
```

This matches the AGENTS.md three-actor model (engineer / agent / code), with the agent
lane split by role — the same split SSSF's per-agent color encodes. Agents get saturated
lane color; engineer and code are neutral. **Lane color is identity, not verdict** — it is
deliberately distinct from `--pass/--fail/--warn` (semantic), so a hatched dead block never
reads as "failed gate" and a live block never reads as "passed".

### New dataclasses in `console/views.py`

```python
@dataclass(frozen=True)
class WaterfallBlock:
    state: str
    lane: str  # engineer | planner | builder | reviewer | code
    start: int  # unix s, from transitions
    end: int  # unix s; = time.time() for the live block
    duration_s: float
    attempt: int  # run.attempt for live; the dead attempt's number where known
    dead: bool  # exit transition was → resumable/failed
    live: bool  # this is the current state, still running


@dataclass(frozen=True)
class AgentCard:
    role: str  # planner | builder | reviewer
    model: str
    effort: str
    status: str  # live | done | idle
    context_pct: float | None  # only for the live role; else None + reason
    context_reason: str | None
    activity: str | None
    heartbeat_age: float | None


@dataclass(frozen=True)
class ToolCallRow:
    index: int  # ordinal within the attempt's events.jsonl
    kind: str  # command | file_change | message | error
    summary: str  # command text / "changed N files" / message excerpt / error
    exit_code: int | None
    # NOTE: no duration field in Phase 1 — events.jsonl has no timestamps (honesty table).


@dataclass(frozen=True)
class RunTimeline:
    row: RunRow  # reuse the existing board row (head strip)
    blocks: list[WaterfallBlock]
    cards: list[AgentCard]
    tool_calls: list[ToolCallRow]  # the live agent's current turn only
    elapsed_total_s: float
```

### `run_timeline(...)` — the producer

One function, one producer (the console's "terminal and page cannot disagree" rule). It
builds the three bands from data the existing views already read:

1. **Blocks** from `store.transitions(run.id)` (already fetched by `run_detail`). For each
   transition `i` entering `to_state` at `at_i`, the block is `to_state` over
   `[at_i, at_{i+1})`; the last block runs `[at_last, now)` and is `live`. The very first
   block (`approved`) starts at `run.created_at` (store.py:176), not the first transition,
   so the "ready-for-agent applied" wait is visible. A block is `dead` when the *next*
   transition's `to_state` is `RESUMABLE` or `FAILED`; a following `resumable →
   implementing/planning/verifying/reviewing` is the resume. `attempt` is `run.attempt` for
   live/resume blocks; for a dead block it is `run.attempt - 1` (the rung before this one)
   — defensible because the ladder is monotonic and a dead block is by definition the
   rung below the current. Reuse `_rung_label` for the label.

2. **Cards** from `_AGENT_ROLE` keys. For each role: `status` = `live` if the current
   `run.state`'s role is this role, else `done` if this role's state is earlier in the
   transition sequence than the current state, else `idle`. `context_pct` only for the live
   role (via `context_percentage`); `None` + reason for done/idle (the card shows the
   reason, mirroring the board's `context_reason`). `activity` and `heartbeat_age` only for
   the live role (from `read_turn_view` + heartbeat); `None` otherwise.

3. **Tool calls** from the live attempt's `events.jsonl`, using a new sibling parser next
   to `read_turn_view` (same skipping rules for a half-flushed trailing line — events.py:79
   already does this): fold `item.completed` events into `ToolCallRow`s in order, reusing
   `_activity_from` (events.py:123) for the summary and reading `exit_code` from
   `command_execution` items. **Only `item.completed`** (not `item.started`/`item.updated`)
   — one row per real call, the SSSF fold. Phase 1 shows the *whole attempt's* calls (the
   file accumulates across turns); scoping to "the live turn only" is a Phase 2 refinement
   that needs turn-boundary timing.

   The token columns on the cards stay **run-aggregate** (`store.spend`), labelled "run
   total", not "planner: 18.2k". This is the honesty table's central call: do not attribute
   the stream to roles it cannot be attributed to.

### `app.py` — one new route, reusing the shell

```python
@app.get("/runs/{ticket}/timeline", response_class=HTMLResponse)
def run_timeline_view(ticket: str) -> HTMLResponse:
    ...  # resolve run like run_detail does
    tl = console_views.run_timeline(home, reg, rt, st, run)
    return HTMLResponse(_page(ticket, _render("run_timeline.html", ...)))
```

Add a **timeline** link to the run-detail head (run_detail.html: the head strip gains
`· <a href="/runs/{ticket}/timeline">timeline</a>`), so the view is reached from the page
that already exists, not a new nav entry. The board's ticket link still goes to
`/runs/{ticket}`; the timeline is one click deeper, matching the detail page's depth.

The waterfall's SSE: **reuse `/sse/tail/{ticket}`** for the tool-call list's live append
(the existing tail already streams the file the table reads), and a lightweight
`/sse/timeline/{ticket}` that re-runs `run_timeline` blocks+cards on a tick (the same
poll-the-store pattern as `board_stream`, app.py:243). No new transport.

### `templates/run_timeline.html`

Follows the `page.html` shell + the `_render`/`_page` pattern. Three sections, summary
before detail (a dashboard is scanned, not read):

1. **Head strip** — reuse `RunRow` (the same fields `run_detail` renders): ticket, project,
   state pill, attempt/rung, context % (live), spend, elapsed, sandbox, session, heartbeat.
2. **Agent cards** — a CSS grid of `AgentCard`; each card has a 3px left stripe in its lane
   color and a `live`/`done`/`idle` pill. Context bar only when `context_pct` is not `None`;
   otherwise the `context_reason` in muted text.
3. **Waterfall** — a time axis with minute ticks, one row per lane, blocks absolutely
   positioned by `left = (start - t0)/span` and `width = duration/span` (percent). A block
   is `dead` → hatched `repeating-linear-gradient` + dashed border; `live` → a pulsing right
   edge (`@keyframes pulse`, guarded by `prefers-reduced-motion`). Thin blocks (< ~6%
   width) show a `title` tooltip and no inline label, so the axis never lies about a
   18-second "label" block looking as long as a 12-minute implement. A `now` line at the
   live block's right edge.
4. **Tool calls** — a table of `ToolCallRow` (offset omitted in Phase 1 — there is no
   timestamp to compute it from; show `#` ordinal, type, command/summary, exit). A live row
   appends via SSE.

Lane colors and the pass/fail/warn semantic colors are **separate token sets** in
`console.css`, extended from the existing `:root` block (and its dark `@media` +
`[data-theme]` redefinitions — the existing console already does the three-state theme
dance; this plan adds lane tokens the same way). No color is defined only inside a media or
`[data-theme]` block.

## Phase 2 — opt-in timing sidecar (a boundary change, James's call)

To defend the two rows the mockup showed that Phase 1 cannot — **per-call duration** and
**per-role token attribution** — the factory would record what it currently drops: when
each event was *observed*, and which role owned each turn.

**New writer.** `agent/codex.py`'s event loop already parses every line as it streams
(codex.py:207). Add a sidecar `events.timings.jsonl` written alongside `events.jsonl`:
`{"id": <item id>, "type": <event type>, "observed_at": time.time(), "usage": {...}}` per
event. This is the SSSF `agent_pi.py` pattern (tail + timestamp + insert), minus SQLite — a
plain file the console reads like it reads `events.jsonl`.

**Why a sidecar, not enriching `events.jsonl`.** The event stream is the codex client's
contract; rewriting its lines risks the state machine's parser (codex.py:207 raises on
shape). A sidecar is additive and the console's read path already tolerates a missing file
(views.py:206 `_read_json` returns `None`).

**Why this is a boundary change.** The console is read-only by design (views.py:1–10). A
writer in the agent step that exists to feed a console view crosses that line — it is the
same shape of decision as the MCP-gateway token exclusion (AGENTS.md), and it is James's to
make. The cost is small (one file per attempt, deleted by the existing `gc` step), but the
*category* is the thing to notice.

**What it unlocks.** With `observed_at` per event: per-call duration (`end - start` by item
`id`); per-turn duration (`turn.completed` minus the preceding `turn.started`); and per-
role attribution (window each turn against the transition timeline — a turn whose
`observed_at` falls in `[planning_at, implementing_at)` is the planner's). That last one
also defends per-role tokens and per-role spend, replacing the run-aggregate card with the
per-role breakdown the mockup drew.

Phase 1's dataclasses are shaped so Phase 2 is additive: `ToolCallRow` gains an optional
`duration_s`, `AgentCard` gains optional per-role `tokens_in/out` and `spend_usd`, and the
template renders them only when present. Nothing in Phase 1 is rewritten.

## Files to change

**Phase 1:**
- `src/factory/console/views.py` — `WaterfallBlock`, `AgentCard`, `ToolCallRow`,
  `RunTimeline`, `run_timeline(...)`. Reuse `RunRow`, `_rung_label`, `context_percentage`,
  `read_turn_view`, `store.transitions`, `store.spend`, `factory_dir_for`.
- `src/factory/console/events.py` — `_LANE` (or a `lane_of(state)` function); a
  `read_tool_calls(events_path)` sibling to `read_turn_view` (same trailing-line skip).
- `src/factory/console/app.py` — `GET /runs/{ticket}/timeline`; `GET /sse/timeline/{ticket}`
  (poll pattern from `board_stream`); link from `run_detail` head.
- `src/factory/console/templates/run_timeline.html` — new; follows `page.html`/`_render`.
- `src/factory/console/templates/console.css` — lane color tokens (light + dark + the two
  `[data-theme]` redefinitions); waterfall + card + tool-call-table styles.
- `src/factory/console/templates/run_detail.html` — timeline link in the head.

**Phase 2 (separate, behind approval):**
- `src/factory/agent/codex.py` — write `events.timings.jsonl` alongside the event stream.
- `src/factory/console/events.py` — `read_tool_calls` reads timings for `duration_s`;
  per-role windowing against transitions.
- `src/factory/gc.py` — delete `events.timings.jsonl` with the attempt dir (verify it
  already sweeps the attempt dir, don't add a second path).

## Verification

1. **Gates.** `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run
   pytest` — extend `tests/.../test_console.py` for `run_timeline` (the existing suite
   covers the views; this adds the new view). The "no Merge button" assertion stays green.
2. **Waterfall from transitions (property test).** Given a fixture transition sequence
   `approved→claimed@t0, claimed→context_loaded@t1, …, reviewing→?@t_last` with
   `run.created_at < t0`: `run_timeline` returns blocks whose `[start,end)` partition
   `[created_at, now]` with no gap or overlap; the last block is `live`; a sequence ending
   `implementing→resumable@t_d, resumable→implementing@t_r` yields one `dead` block over
   `[t_d-entry, t_d)` and one live/resume block over `[t_r, now)`. Assert the partition and
   the dead/live flags — not pixel positions.
3. **Honesty assertions.** `ToolCallRow` has no `duration_s` field in Phase 1 (mypy: the
   field is absent, not `Optional`). `AgentCard.context_pct` is `None` for every non-live
   role. A run in `reviewing` yields `reviewer` live, `planner`/`builder` done; a run in
   `implementing` yields `builder` live, `planner` done, `reviewer` idle. These are the
   honesty table made executable — a regression here is the bug the rule exists to prevent.
4. **Manual.** `uv run factory serve`; open a run's `/runs/{ticket}/timeline`: the
   waterfall's blocks line up with the transition table on the detail page (same `at`
   values, same order); the live block's right edge sits at the `now` line; the agent cards'
   live role matches the state pill; the tool-call table's last row matches the board's
   "activity" column; no card shows a per-role token number.
5. **Theme.** The page reads in light, dark, and an explicit `data-theme` stamp — no lane
   color is defined only inside a media or `[data-theme]` block (scan the CSS).

## Out of scope (deliberate)

- Phase 2's writer (boundary change — James's call, separate change).
- Any off-machine access (Phase 7), any Merge button, any edit of layer-B gate definitions,
  any new runtime dependency, any new state-machine transition.
- Per-role token/spend attribution before Phase 2's sidecar exists.