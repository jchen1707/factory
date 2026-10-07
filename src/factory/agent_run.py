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

from factory import artifacts
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
from factory.steps import attempt_names

if TYPE_CHECKING:
    from factory.routing import Role as RoutedRole
    from factory.steps import Context

__all__ = [
    "ATTESTATION_FAILED",
    "AUTH_FAILED",
    "DISPOSITION",
    "TRUNCATED",
    "conclude",
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

#: `Blocked` needs a human: a credential, a model, the money, or a launch the CLI refused
#: outright. `Resumable` goes to the §16.4 ladder. A lost session is resumable because
#: `resumable_session` finds no `init` in its stream, so the ladder moves on (a fresh
#: session or a rewind) instead of replaying the id.
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

    A run whose `init` disagrees with the launch is not evidence, whatever its result says.
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

    Every launch records its attestation; a record without one is not a run this code
    launched, and reading it unattested would be the silent pass this check exists to
    refuse.
    """
    record = ctx.store.runtime.invocation(invocation_id)
    payload = (record or {}).get("metadata", {}).get("expected")
    if not payload:
        raise Blocked("launch-record-missing", f"{invocation_id} recorded no attestation")
    return stream.parse(
        files.events,
        exit_path=files.exit,
        stderr_path=files.stderr,
        expected=expected_from(payload),
    )


def conclude(
    ctx: Context,
    *,
    attempt: int,
    state: State,
    invocation_id: str,
    files: AttemptFiles,
    record: bool,
    label: str | None = None,
) -> Run:
    """Read the run back, record its checks, and raise its stop; return it when it completed.

    The one collect tail for every role. `record` is false when the attempt's evidence
    rows were already written by an earlier pass (a re-entered collect, or the review
    fan-out's final pass over axes it already landed). The stop carries the exit code and
    the last 40 lines of stderr, which is where the CLI's own refusals are written.
    """
    run = read(ctx, invocation_id, files)
    if record:
        record_checks(ctx, attempt, run)
    stop = stop_for(run)
    if stop is None:
        return run
    try:
        exit_code: int | None = int(files.exit.read_text().strip())
    except (OSError, ValueError):
        exit_code = None
    ctx.store.finish_attempt(ctx.run.id, attempt, state, exit_code=exit_code, outcome=stop.reason)
    prefix = f"{label}: " if label else ""
    tail = artifacts.tail_lines(files.stderr, 40)
    raise type(stop)(stop.reason, f"{prefix}exit {exit_code}; {stop.detail}\n{tail}")


def record_checks(ctx: Context, attempt: int, run: Run) -> None:
    if run.init is None:
        status, reason = "skip", "the stream has no init event to attest"
    else:
        status, reason = ("fail" if run.violations else "pass"), None
    ctx.store.record_check(
        ctx.run.id,
        attempt,
        "isolation_attestation",
        status,
        reason=reason,
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


def resumable_session(ctx: Context, attempt: int, state: State) -> SessionId | None:
    """The session the next attempt can `--resume`, or `None` for a fresh one.

    The id is pinned on the row before launch, so the row alone does not say whether the
    CLI ever opened the session; only a stream holding an `init` event can be resumed.
    """
    row = ctx.store.attempt_row(ctx.run.id, attempt, state)
    if row is None or not row["session_id"] or not row["artifact_dir"]:
        return None
    events = Path(str(row["artifact_dir"])) / attempt_names(state).events
    return SessionId(str(row["session_id"])) if stream.has_session(events) else None
