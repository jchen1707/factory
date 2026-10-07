"""The one disposition table, the attestation round trip, and the resume-or-fresh switch."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from factory import agent_run
from factory.agent import claude, stream
from factory.agent.stream import (
    Completed,
    Failed,
    FailureKind,
    Interrupted,
    Run,
    SessionId,
    Violation,
)
from factory.machine import Blocked, Resumable, State
from tests.support import claude_stream

SESSION = SessionId(claude_stream.SESSION)


def _run(outcome: stream.Outcome, *, violations: tuple[Violation, ...] = ()) -> Run:
    return Run(
        session=SESSION,
        init=None,
        outcome=outcome,
        violations=violations,
        denials=(),
        rate_limit=None,
        context_tokens=0,
        notional_usd=0.0,
        by_model={},
        commands=(),
        files_touched=(),
    )


def test_every_failure_kind_has_exactly_one_disposition() -> None:
    # A kind the stream can report with no row here would raise KeyError at collect
    # time, in the one place that decides whether a human is needed.
    assert set(agent_run.DISPOSITION) == set(FailureKind)


@pytest.mark.parametrize(
    ("kind", "stop", "reason"),
    [
        (FailureKind.AUTH, Blocked, "agent-auth"),
        (FailureKind.MODEL_UNAVAILABLE, Blocked, "model-unavailable"),
        (FailureKind.BUDGET, Blocked, "budget-exceeded"),
        (FailureKind.SESSION_IN_USE, Blocked, "session-in-use"),
        (FailureKind.LAUNCH_REFUSED, Blocked, "launch-refused"),
        (FailureKind.MAX_TURNS, Resumable, "max-turns"),
        (FailureKind.SESSION_LOST, Resumable, "session-lost"),
        (FailureKind.RATE_LIMITED, Resumable, "rate-limited"),
        (FailureKind.EXIT_NONZERO, Resumable, "agent-failed"),
        (FailureKind.CORRUPT, Resumable, "transcript-corrupt"),
        (FailureKind.API_ERROR, Resumable, "api-error"),
    ],
)
def test_each_failure_maps_to_its_stop(kind: FailureKind, stop: type, reason: str) -> None:
    # The table is the contract: a credential, a model, the money or a refused launch
    # need a human; everything else goes to the ladder.
    raised = agent_run.stop_for(_run(Failed(kind, "why")))
    assert type(raised) is stop
    assert raised is not None
    assert raised.reason == reason
    assert "why" in raised.detail


def test_a_completed_run_is_no_stop() -> None:
    assert agent_run.stop_for(_run(Completed(None, "done", 2, 0))) is None


def test_an_interrupted_run_is_resumable_unless_its_credential_was_failing() -> None:
    truncated = agent_run.stop_for(_run(Interrupted(exit_code=143, auth_failing=False)))
    assert isinstance(truncated, Resumable)
    assert truncated.reason == "transcript-truncated"

    auth = agent_run.stop_for(_run(Interrupted(exit_code=143, auth_failing=True)))
    assert isinstance(auth, Blocked)
    assert auth.reason == "agent-auth"


def test_an_attestation_violation_blocks_before_the_outcome_is_read() -> None:
    # A stream whose `init` disagrees with the launch is not evidence of anything, so a
    # clean `Completed` after a wrong session is still blocked.
    violation = Violation("session", SESSION, "other")
    stop = agent_run.stop_for(_run(Completed(None, "done", 2, 0), violations=(violation,)))
    assert isinstance(stop, Blocked)
    assert stop.reason == "isolation-attestation-failed"
    assert "other" in stop.detail


def _invocation(tmp_path: Path) -> claude.Invocation:
    return claude.Invocation(
        role=claude.Role.REVIEWER,
        model="claude-fable-5-1",
        effort="high",
        max_turns=50,
        max_budget_usd=12.5,
        session=SESSION,
        resume=False,
        files=claude.AttemptFiles(
            prompt=tmp_path / "prompt.md",
            events=tmp_path / "events.jsonl",
            stderr=tmp_path / "stderr.log",
            exit=tmp_path / "exit",
            heartbeat=tmp_path / "heartbeat",
            pgid=tmp_path / "pgid",
            last_message=tmp_path / "out.json",
        ),
        schema={"type": "object"},
    )


def test_the_attestation_survives_a_json_round_trip(tmp_path: Path) -> None:
    inv = _invocation(tmp_path)
    payload = json.loads(json.dumps(agent_run.expected_json(inv)))
    assert agent_run.expected_from(payload) == inv.expected
    # And it is the reviewer's set: no Write, no Edit, StructuredOutput admitted.
    assert set(payload["tools"]) == {"Bash", "Read", "Skill", "StructuredOutput"}


def test_invocation_caps_the_budget_at_what_the_run_has_left(tmp_path: Path) -> None:
    ctx = SimpleNamespace(
        routing=SimpleNamespace(usd_per_run=50.0),
        store=SimpleNamespace(known_spend=lambda run_id: 37.5),
        run=SimpleNamespace(id="r"),
        env={"UV_PROJECT_ENVIRONMENT": "/home/agent/venvs/p"},
    )
    routed = SimpleNamespace(model="claude-opus-5-5", effort="medium", max_turns=200)
    inv = agent_run.invocation(
        ctx,  # type: ignore[arg-type]
        role=claude.Role.BUILDER,
        routed=routed,  # type: ignore[arg-type]
        files=_invocation(tmp_path).files,
        schema={"type": "object"},
        session=SESSION,
        resume=True,
    )
    assert inv.max_budget_usd == 12.5
    assert "--max-budget-usd" in claude.argv(inv)
    assert claude.argv(inv)[claude.argv(inv).index("--max-budget-usd") + 1] == "12.5"
    assert claude.argv(inv)[claude.argv(inv).index("--resume") + 1] == SESSION


def test_an_effort_routing_knows_but_the_cli_does_not_blocks_rather_than_crashing(
    tmp_path: Path,
) -> None:
    ctx = SimpleNamespace(
        routing=SimpleNamespace(usd_per_run=50.0),
        store=SimpleNamespace(known_spend=lambda run_id: 0.0),
        run=SimpleNamespace(id="r"),
        env={},
    )
    routed = SimpleNamespace(model="claude-opus-5-5", effort="ultra", max_turns=200)
    with pytest.raises(Blocked) as caught:
        agent_run.invocation(
            ctx,  # type: ignore[arg-type]
            role=claude.Role.BUILDER,
            routed=routed,  # type: ignore[arg-type]
            files=_invocation(tmp_path).files,
            schema={},
            session=SESSION,
            resume=False,
        )
    assert caught.value.reason == "launch-invalid"


# -- the resume-or-fresh switch ---------------------------------------------------


class _Store:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def attempt_row(self, run_id: str, attempt: int, state: State) -> dict[str, Any] | None:
        return self.row


def _ctx(row: dict[str, Any] | None) -> Any:
    return SimpleNamespace(store=_Store(row), run=SimpleNamespace(id="r"))


def test_a_pinned_session_whose_stream_opened_is_resumable(tmp_path: Path) -> None:
    (tmp_path / "events.jsonl").write_text(
        "\n".join(claude_stream.auth_retrying(session=SESSION).lines) + "\n"
    )
    row = {"session_id": SESSION, "artifact_dir": str(tmp_path)}
    assert agent_run.resumable_session(_ctx(row), 1, State.IMPLEMENTING) == SESSION


def test_a_pinned_session_whose_stream_never_opened_starts_fresh(tmp_path: Path) -> None:
    # The row has an id from the moment of launch, but the CLI never wrote `init`: a
    # refused launch (`session_in_use`), a `--resume` into a sandbox that lost the
    # transcript (`session_lost`), or a kill before the first event. `--resume` would
    # fail again, so the ladder starts a new session instead.
    for scenario in (
        claude_stream.session_in_use(session=SESSION),
        claude_stream.session_lost(session=SESSION),
    ):
        (tmp_path / "events.jsonl").write_text("\n".join(scenario.lines) + "\n")
        row = {"session_id": SESSION, "artifact_dir": str(tmp_path)}
        assert agent_run.resumable_session(_ctx(row), 1, State.IMPLEMENTING) is None
    (tmp_path / "events.jsonl").unlink()
    assert agent_run.resumable_session(_ctx(row), 1, State.IMPLEMENTING) is None


def test_the_plan_phase_is_read_from_its_own_stream_file(tmp_path: Path) -> None:
    (tmp_path / "plan-events.jsonl").write_text(
        "\n".join(claude_stream.auth_retrying(session=SESSION).lines) + "\n"
    )
    row = {"session_id": SESSION, "artifact_dir": str(tmp_path)}
    assert agent_run.resumable_session(_ctx(row), 1, State.PLANNING) == SESSION
    assert agent_run.resumable_session(_ctx(row), 1, State.IMPLEMENTING) is None


def test_no_row_or_no_pin_means_fresh() -> None:
    assert agent_run.resumable_session(_ctx(None), 1, State.IMPLEMENTING) is None
    row = {"session_id": None, "artifact_dir": "/nowhere"}
    assert agent_run.resumable_session(_ctx(row), 1, State.IMPLEMENTING) is None


def test_an_invocation_without_an_attestation_is_refused_not_read_unattested(
    tmp_path: Path,
) -> None:
    ctx = SimpleNamespace(
        store=SimpleNamespace(runtime=SimpleNamespace(invocation=lambda _: {"metadata": {}})),
        run=SimpleNamespace(id="r"),
    )
    with pytest.raises(Blocked) as caught:
        agent_run.read(ctx, "r:1:implement", _invocation(tmp_path).files)  # type: ignore[arg-type]
    assert caught.value.reason == "launch-record-missing"
