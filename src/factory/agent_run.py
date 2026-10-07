"""One agent run, from a routed role to its verdict.

Every role (planner, builder, reviewer, ...) is one `claude -p` launch that the steps
build here, read back here, and dispose of here. The steps own their files and their
prompts; this module owns what is the same for all of them: how a routed role becomes an
`Invocation`, how the session is pinned before launch, how the attestation is carried to
collect time, and the one table that turns a stream outcome into `Blocked` or `Resumable`.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, assert_never, cast

from factory.agent import stream
from factory.agent.claude import AttemptFiles, Effort, Invocation, Role
from factory.agent.stream import (
    Completed,
    Expected,
    Failed,
    FailureKind,
    Interrupted,
    Run,
    SessionId,
)
from factory.machine import Blocked, Resumable, State

if TYPE_CHECKING:
    from factory.routing import Role as RoutedRole
    from factory.steps import Context

__all__ = [
    "ATTESTATION_FAILED",
    "AUTH_FAILED",
    "DISPOSITION",
    "TRUNCATED",
    "events_name",
    "expected_from",
    "expected_json",
    "invocation",
    "read",
    "record_checks",
    "resumable_session",
    "stop_for",
]

Stop = Blocked | Resumable

ATTESTATION_FAILED = "isolation-attestation-failed"
AUTH_FAILED = "agent-auth"
TRUNCATED = "transcript-truncated"

#: What the control plane does with each failure the stream can report. `Blocked` needs
#: a human: a credential, a model, the money, or a launch the CLI refused outright.
#: `Resumable` goes to the §16.4 ladder, which restarts, resumes or rewinds. A lost
#: session is resumable because `resumable_session` finds no `init` in its stream and
#: the ladder restarts with a fresh one.
DISPOSITION: Mapping[FailureKind, tuple[type[Blocked] | type[Resumable], str]] = MappingProxyType(
    {
        FailureKind.AUTH: (Blocked, AUTH_FAILED),
        FailureKind.MODEL_UNAVAILABLE: (Blocked, "model-unavailable"),
        FailureKind.BUDGET: (Blocked, "budget-exceeded"),
        FailureKind.SESSION_IN_USE: (Blocked, "session-in-use"),
        FailureKind.LAUNCH_REFUSED: (Blocked, "launch-refused"),
        FailureKind.MAX_TURNS: (Resumable, "max-turns"),
        FailureKind.SESSION_LOST: (Resumable, "session-lost"),
        FailureKind.RATE_LIMITED: (Resumable, "rate-limited"),
        FailureKind.EXIT_NONZERO: (Resumable, "agent-failed"),
        FailureKind.CORRUPT: (Resumable, "transcript-corrupt"),
        FailureKind.API_ERROR: (Resumable, "api-error"),
    }
)


def stop_for(run: Run) -> Stop | None:
    """The exception a step raises for this run, or `None` when the run completed.

    Attestation comes first: a run whose `init` disagrees with the launch (another
    session, another model, a tool or MCP server the role was not given) is not evidence
    of anything, whatever its result says.
    """
    if run.violations:
        observed = "; ".join(
            f"{v.what}: expected {v.expected!r}, observed {v.observed!r}" for v in run.violations
        )
        return Blocked(ATTESTATION_FAILED, observed)
    outcome = run.outcome
    match outcome:
        case Completed():
            return None
        case Failed(kind=kind, detail=detail):
            stop, reason = DISPOSITION[kind]
            return stop(reason, detail)
        case Interrupted(exit_code=exit_code, auth_failing=True):
            return Blocked(AUTH_FAILED, f"authentication failed mid-run; exit {exit_code}")
        case Interrupted(exit_code=exit_code):
            return Resumable(TRUNCATED, f"the stream ends before a result event; exit {exit_code}")
        case _:
            assert_never(outcome)


def invocation(
    ctx: Context,
    *,
    role: Role,
    routed: RoutedRole,
    files: AttemptFiles,
    schema: Mapping[str, object],
    session: SessionId,
    resume: bool,
) -> Invocation:
    """The launch for `role` as routing configured it, capped at the run's remaining budget."""
    remaining = ctx.routing.usd_per_run - ctx.store.known_spend(ctx.run.id)
    try:
        return Invocation(
            role=role,
            model=routed.model,
            effort=cast(Effort | None, routed.effort),
            max_turns=routed.max_turns,
            max_budget_usd=remaining,
            session=session,
            resume=resume,
            files=files,
            schema=schema,
            env=ctx.env,
        )
    except ValueError as exc:
        raise Blocked("launch-invalid", str(exc)) from exc


def expected_json(inv: Invocation) -> dict[str, object]:
    """The attestation, as the invocation record stores it for collect time."""
    expected = inv.expected
    return {
        "session": expected.session,
        "model": expected.model,
        "tools": sorted(expected.tools),
        "plugins": sorted(expected.plugins),
        "permission_mode": expected.permission_mode,
    }


def expected_from(payload: Mapping[str, Any]) -> Expected:
    return Expected(
        session=SessionId(str(payload["session"])),
        model=str(payload["model"]),
        tools=frozenset(str(tool) for tool in payload["tools"]),
        plugins=frozenset(str(plugin) for plugin in payload["plugins"]),
        permission_mode=str(payload["permission_mode"]),
    )


def read(ctx: Context, invocation_id: str, files: AttemptFiles) -> Run:
    """The run as the filesystem recorded it, attested against what was launched.

    An invocation with no stored attestation (one started before sessions were pinned)
    is read without one rather than refused: its evidence is still worth collecting.
    """
    record = ctx.store.runtime.invocation(invocation_id)
    payload = (record or {}).get("metadata", {}).get("expected")
    return stream.parse(
        files.events,
        exit_path=files.exit,
        stderr_path=files.stderr,
        expected=expected_from(payload) if payload else None,
    )


def record_checks(ctx: Context, attempt: int, run: Run) -> None:
    """The attestation and every refused tool call, as check rows beside the attempt."""
    ctx.store.record_check(
        ctx.run.id,
        attempt,
        "isolation_attestation",
        "fail" if run.violations else "pass",
        detail="; ".join(f"{v.what}: {v.observed}" for v in run.violations)[:2000] or None,
    )
    if run.denials:
        # Not a failure on its own, a refused tool is enforcement working, but the
        # stream's result still reads `success`, so without a row the evidence would
        # show a clean run for a turn that was refused.
        ctx.store.record_check(
            ctx.run.id,
            attempt,
            "tool_denials",
            "fail",
            detail="\n".join(f"{d.tool} {d.tool_use_id}: {d.reason}" for d in run.denials)[:2000],
        )
        ctx.log("agent.tool-denied", level="warning", count=len(run.denials))


def events_name(state: State) -> str:
    """The stream file a detached agent state writes into its attempt directory."""
    from factory.steps.plan import PLAN_EVENTS_NAME

    return PLAN_EVENTS_NAME if state is State.PLANNING else "events.jsonl"


def resumable_session(ctx: Context, attempt: int, state: State) -> SessionId | None:
    """The session the next attempt can `--resume`, or `None` for a fresh one.

    The id is pinned on the row before launch, so the row alone does not say whether the
    CLI ever opened the session. Only a stream holding an `init` event can be resumed:
    one killed before it (or refused outright, or resumed into a sandbox that lost it)
    makes the ladder start a fresh session instead of spending a rung on `session-lost`.
    """
    row = ctx.store.attempt_row(ctx.run.id, attempt, state)
    if row is None or not row["session_id"] or not row["artifact_dir"]:
        return None
    events = Path(str(row["artifact_dir"])) / events_name(state)
    return SessionId(str(row["session_id"])) if stream.has_session(events) else None
