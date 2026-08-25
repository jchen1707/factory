"""The console's read-only view of a run's `events.jsonl` — §18.5.

P0-7 (`docs/discovery/codex-events.md`) settled the shape: the stream carries per-turn
`usage` (`input_tokens` / `cached_input_tokens` / `cache_write_input_tokens` /
`output_tokens` / `reasoning_output_tokens`) and `item.completed` activity, but **no**
`model_context_window`, `context_window`, `tokens_used` or `percent` — those are TUI-only.
So the console's context percentage is `latest_turn.input_tokens /
routing.ModelFacts.usable_context`, with the denominator from the model cache via
`routing`, and it is *hidden with a reason* when the numerator or denominator is missing
rather than estimated. The plan is explicit (§18.5): never show a number it cannot defend.

This is a display parser, not the state machine's. `agent.codex.parse_events` advances on
evidence and raises on a truncated line; this one only displays, so a half-flushed trailing
line (the normal case for a file a live process is writing) is skipped, not fatal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory.machine import State
from factory.routing import Routing

__all__ = [
    "ToolCallView",
    "TurnView",
    "context_percentage",
    "lane_of",
    "read_tool_calls",
    "read_turn_view",
]


#: The agent role each live state runs under — the model that produced the events the
#: console reads. Non-agent states have no model: `verifying` runs a node gate report,
#: `pr_ready`/`deliver` is a host-side push. Their context percentage is hidden with a
#: reason rather than shown against the wrong denominator.
_AGENT_ROLE: dict[State, str] = {
    State.PLANNING: "planner",
    State.IMPLEMENTING: "builder",
    State.REVIEWING: "reviewer",
}


#: The lane each state belongs to on the run timeline — the AGENTS.md three-actor model
#: (engineer / agent / code), with the agent lane split by role. The colour is *identity*,
#: not verdict: it is deliberately separate from `--pass/--fail/--warn` (semantic), so a
#: hatched dead block never reads as a failed gate. States without a lane (`blocked`,
#: `resumable`, `suspended`, `failed`, `cancelled`) are transient or terminal markers and
#: get `None`; the waterfall renders them as the run's exit, not as a lane block.
_LANE: dict[State, str] = {
    State.APPROVED: "engineer",
    State.AWAITING_HUMAN: "engineer",
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
}


def lane_of(state: State) -> str | None:
    """The lane a state occupies on the run timeline, or `None` for a marker state."""
    return _LANE.get(state)


@dataclass(frozen=True)
class TurnView:
    """The latest `turn.completed` usage of a run's active attempt, plus the last thing
    the agent did. `has_turn=False` when no turn has completed yet — the caller reads that
    as 'hide the percentage, the agent is mid-turn and a partial turn is not a number to
    defend'."""

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    activity: str | None
    has_turn: bool


def read_turn_view(events_path: Path) -> TurnView | None:
    """Read an `events.jsonl` for the console's columns.

    `None` when the file is absent — the attempt has not spawned yet, or the run is in a
    state with no events. A `has_turn=False` view when the file exists but no
    `turn.completed` has landed. The last `item.completed` becomes `activity`, the latest
    turn's `usage` becomes the token columns; both are read back rather than remembered,
    because the file is the only thing a fresh console process shares with the run.
    """
    if not events_path.exists():
        return None
    saw_turn = False
    input_tokens = 0
    cached = 0
    output = 0
    reasoning = 0
    activity: str | None = None
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # a half-flushed trailing line is normal for a file being written
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "turn.completed":
            raw = event.get("usage")
            if isinstance(raw, dict):
                input_tokens = int(raw.get("input_tokens", 0) or 0)
                cached = int(raw.get("cached_input_tokens", 0) or 0)
                output = int(raw.get("output_tokens", 0) or 0)
                reasoning = int(raw.get("reasoning_output_tokens", 0) or 0)
            saw_turn = True
        elif kind == "item.completed":
            activity = _activity_from(event.get("item"))
    return TurnView(input_tokens, cached, output, reasoning, activity, saw_turn)


@dataclass(frozen=True)
class ToolCallView:
    """One row in the run-timeline tool-call drill-down — the `events.jsonl` stream folded
    to one entry per `item.completed`, the SSSF visualizer's per-phase call list.

    `duration_s` is `None` unless an `events.timings.jsonl` sidecar (Phase 2) is present.
    The event stream itself carries no timestamp (P0-7, `docs/discovery/codex-events.md`):
    each line has only `type` and `item`/`usage`, never `at`. A duration the console cannot
    defend is the one thing §18.5 refuses to show, so the field is `None` — never a guess —
    until a sidecar defends it. The sidecar is one `{"observed_at": <epoch>}` row per
    `events.jsonl` line, in order; a call's duration is its `item.completed` observed time
    minus its matching `item.started` observed time (paired by item `id`).
    """

    index: int
    kind: str  # the item's `type` — command_execution | file_change | agent_message | error
    summary: str
    exit_code: int | None  # only `command_execution` carries one
    duration_s: float | None  # Phase 2 sidecar; None without it


def _exit_code_of(item: dict[str, Any], item_type: str) -> int | None:
    if item_type != "command_execution":
        return None
    raw = item.get("exit_code")
    if isinstance(raw, bool):  # JSON true/false are ints in Python; refuse them
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    return None


def _read_timings(path: Path) -> list[float | None]:
    """The `events.timings.jsonl` sidecar as a list indexed by `events.jsonl` line number.

    One row per line, in order — blank/unparseable lines become `None` so the indices stay
    aligned with `enumerate(events.jsonl.splitlines())`. Returns `[]` when there is no
    sidecar (Phase 1, or a run whose writer never armed one), which leaves every
    `duration_s` `None` — the Phase 1 behaviour, unchanged.
    """
    if not path.exists():
        return []
    out: list[float | None] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            out.append(None)
            continue
        ts = row.get("observed_at") if isinstance(row, dict) else None
        out.append(float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None)
    return out


def _duration_of(
    item_id: str | None,
    completed_line: int,
    started_line: dict[str, int],
    observed: list[float | None],
) -> float | None:
    """A call's wall-clock duration from the sidecar, or `None` if undefended.

    `item.completed` is emitted *after* the call finishes, so its observed time is the end;
    the matching `item.started` (same `id`, earlier line) is the start. Both must have a
    sidecar timestamp, or the result is `None` — never a guess.
    """
    if item_id is None or item_id not in started_line:
        return None
    start_line = started_line[item_id]
    if start_line >= len(observed) or completed_line >= len(observed):
        return None
    start, end = observed[start_line], observed[completed_line]
    if start is None or end is None:
        return None
    elapsed = end - start
    return elapsed if elapsed >= 0 else None


def read_tool_calls(events_path: Path) -> list[ToolCallView]:
    """Fold an `events.jsonl` into one `ToolCallView` per `item.completed`, in file order.

    A sibling to `read_turn_view`: the same half-flushed-trailing-line skip (a file a live
    agent is writing is the normal case), the same "display only, never raise" stance, and
    the same `_activity_from` summary. Only `item.completed` is folded into rows;
    `item.started` is consumed only to pair start/end with the sidecar for `duration_s`.
    `item.updated` is the stream's noise. Returns `[]` for a missing file (the attempt has
    not spawned, or the state has no agent).

    Per-call `duration_s` is `None` unless an `events.timings.jsonl` sidecar sits beside the
    stream — the Phase 2 producer's call to arm, not the console's to assume.
    """
    if not events_path.exists():
        return []
    observed = _read_timings(events_path.with_name("events.timings.jsonl"))
    rows: list[ToolCallView] = []
    started_line: dict[str, int] = {}
    for i, line in enumerate(events_path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # half-flushed trailing line — see read_turn_view for the reasoning
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        item_id_str = str(item_id) if item_id is not None else None
        if kind == "item.started":
            if item_id_str is not None:
                started_line[item_id_str] = i
            continue
        if kind != "item.completed":
            continue
        item_type = str(item.get("item_type") or item.get("type") or "")
        rows.append(
            ToolCallView(
                index=len(rows),
                kind=item_type,
                summary=_activity_from(item) or item_type or "item",
                exit_code=_exit_code_of(item, item_type),
                duration_s=_duration_of(item_id_str, i, started_line, observed),
            )
        )
    return rows


def context_percentage(
    view: TurnView | None, state: State, routing: Routing
) -> tuple[float | None, str | None]:
    """The context-used percentage for a run, or `None` and the reason it is hidden.

    §18.5: the percentage is either shown from the P0-7-confirmed numerator over the
    cache denominator, or hidden with the reason stated. It is never estimated, so every
    branch that returns `None` names exactly what is missing — no model for the state, no
    completed turn yet, or no context window on file for the routed model.
    """
    role = _AGENT_ROLE.get(state)
    if role is None:
        return None, f"no agent running in {state.value}"
    if view is None:
        return None, "no event stream for this attempt yet"
    if not view.has_turn:
        return None, "the agent has not completed a turn yet"
    model = routing.role(role).model
    facts = routing.models.get(model)
    if facts is None:
        return None, f"no context window on file for {model!r}"
    denominator = facts.usable_context
    if denominator <= 0:
        return None, f"context window for {model!r} is {denominator}"
    return view.input_tokens / denominator, None


def _activity_from(item: Any) -> str | None:
    """One short line describing the last `item.completed` — the 'current activity' column.

    The event shapes are P0-7's: `agent_message` (`text`), `command_execution`
    (`command`, `aggregated_output`, `exit_code`), `file_change` (`changes: [{path,
    kind}]`), `error` (`message`). Truncated to a column-friendly width; the full text is
    in the run-detail tail, not the board."""
    if not isinstance(item, dict):
        return None
    item_type = item.get("item_type") or item.get("type")
    if item_type == "agent_message":
        text = str(item.get("text", "")).strip().replace("\n", " ")
        return (text[:120] + "…") if len(text) > 120 else (text or "agent message")
    if item_type == "command_execution":
        cmd = str(item.get("command", "")).strip().replace("\n", " ")
        return (cmd[:120] + "…") if len(cmd) > 120 else (cmd or "command")
    if item_type == "file_change":
        changes = item.get("changes")
        n = len(changes) if isinstance(changes, list) else 0
        return f"changed {n} file{'s' if n != 1 else ''}"
    if item_type == "error":
        return f"error: {str(item.get('message', ''))[:100]}"
    return str(item_type) if item_type else None
