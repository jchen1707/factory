"""James's answer to an implementation worker's `agent-blocked` question.

The answer is bound to the blocking transition's id and to the run's count of confirmed
agent launches when it was recorded. It stays current while the run has moved only between
`implementing` and the parked states since that question and no launch is confirmed. A
resume that is held, refused, or fails before the worker starts keeps it for the next
resume; no prompt carries it once a worker has started with it or the run has moved on.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from factory.machine import Blocked, State

if TYPE_CHECKING:
    import sqlite3

    from factory.steps import Context

MAX_BYTES = 16 * 1024
_ACTION = "blocker-resolution-recorded"
_UNTIL_LAUNCH = {
    str(state)
    for state in (
        State.IMPLEMENTING,
        State.BLOCKED,
        State.SUSPENDED,
        State.RESUMABLE,
        State.FAILED,
    )
}


def record(ctx: Context, instruction: str, *, from_state: str | None) -> None:
    if from_state not in (None, str(State.IMPLEMENTING)):
        raise Blocked(
            "blocker-resolution-target-invalid",
            "--blocker-resolution reaches the worker only through a plain resume "
            "or --from implementing",
        )
    text = instruction.strip()
    if not text:
        raise Blocked("blocker-resolution-invalid", "The blocker resolution is empty")
    if len(text.encode()) > MAX_BYTES:
        raise Blocked(
            "blocker-resolution-invalid", f"The blocker resolution exceeds {MAX_BYTES} bytes"
        )
    opened = _open_question(ctx) if ctx.state is State.BLOCKED else None
    if opened is None:
        raise Blocked(
            "blocker-resolution-unavailable",
            f"{ctx.run.linear_id} has no open agent-blocked question to answer; "
            "--blocker-resolution needs a `blocked` run whose worker stopped with "
            "`agent-blocked` and has not been relaunched or moved past `implementing` since",
        )
    question, answer = opened
    if answer is not None and answer["instruction"] == text:
        return
    ctx.store.runtime.audit(
        "run",
        ctx.run.id,
        _ACTION,
        {"transition_id": question["id"], "launches": _launches(ctx), "instruction": text},
    )


def prompt_section(ctx: Context) -> list[str]:
    opened = _open_question(ctx)
    if opened is None:
        return []
    question, answer = opened
    if answer is None:
        return []
    return [
        "### James's answer to the worker's question",
        "",
        "The worker stopped with `agent-blocked` and asked:",
        "",
        "```",
        str(question["detail"] or "")[:2000],
        "```",
        "",
        "James answered:",
        "",
        answer["instruction"],
        "",
        "This is direction, not evidence that the blocker is resolved. It waives no gate, "
        "test, review obligation, frozen authority or policy, and grants no deployment, "
        "credential, network or live-effect scope. If it conflicts with any of those, "
        "stop and report the conflict.",
        "",
    ]


def _open_question(ctx: Context) -> tuple[sqlite3.Row, dict[str, Any] | None] | None:
    """The question with its current answer, if one is recorded.

    Unanswered, a question is open only while it is the run's latest transition: after any
    later hop, a builder may have run without an answer and moved past it.
    """
    question = _question(ctx)
    if question is None:
        return None
    answer = _answer(ctx, question)
    if answer is None and question["id"] != ctx.store.transitions(ctx.run.id)[-1]["id"]:
        return None
    return question, answer


def _question(ctx: Context) -> sqlite3.Row | None:
    for row in reversed(ctx.store.transitions(ctx.run.id)):
        if (
            row["from_state"] == str(State.IMPLEMENTING)
            and row["to_state"] == str(State.BLOCKED)
            and row["rule"] == "agent-blocked"
        ):
            return row
        if not {row["from_state"], row["to_state"]} <= _UNTIL_LAUNCH:
            return None
    return None


def _answer(ctx: Context, question: sqlite3.Row) -> dict[str, Any] | None:
    for event in reversed(ctx.store.runtime.events(ctx.run.id)):
        if event["action"] != _ACTION:
            continue
        payload: dict[str, Any] = json.loads(event["payload"])
        if payload["transition_id"] == question["id"]:
            return payload if payload["launches"] == _launches(ctx) else None
    return None


def _launches(ctx: Context) -> int:
    return sum(
        1
        for effect in ctx.store.effects(ctx.run.id)
        if effect.system == "agent-launch"
        and effect.key == "spawn"
        and effect.status == "confirmed"
    )
