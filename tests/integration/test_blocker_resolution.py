"""`factory resume --blocker-resolution`: James's answer reaches the worker that asked."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from factory import blocker_resolution, cli, driver, execution, recovery, workflow_launches
from factory.machine import Blocked, State
from factory.runtime_jobs import RuntimeJobs
from factory.steps import Context, advance, block
from tests.integration.conftest import GOOD_RESULT, Launch, git
from tests.integration.test_phase4 import _fake, _start_an_attempt
from tests.integration.test_resume_holds import hold

ANSWER = "Use the existing settings module; do not add a new dependency."
SUPERSEDED = "Read settings from the config file."
QUESTION = "Should the health endpoint read settings from env or the config file?"


def _agent_blocked(ctx: Context) -> None:
    _fake(ctx).result = dict(GOOD_RESULT, status="blocked", blocked_reason=QUESTION)
    _start_an_attempt(ctx)
    driver.drive(ctx)
    ctx.refresh()
    latest = ctx.store.transitions(ctx.run.id)[-1]
    assert (latest["from_state"], latest["to_state"], latest["rule"]) == (
        "implementing",
        "blocked",
        "agent-blocked",
    )
    _fake(ctx).result = dict(GOOD_RESULT)


def _cli(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACTORY_HOME", str(ctx.home))
    monkeypatch.setattr(cli, "load_routing", lambda path: ctx.routing)
    monkeypatch.setattr(cli, "_open_store", lambda home: ctx.store)
    monkeypatch.setattr(cli, "LinearClient", lambda: ctx.linear)
    monkeypatch.setattr(cli, "_context_for", lambda *args: ctx)


def _recorded(ctx: Context) -> list[dict[str, Any]]:
    return [
        json.loads(event["payload"])
        for event in ctx.store.runtime.events(ctx.run.id)
        if event["action"] == "blocker-resolution-recorded"
    ]


def _agent_launches(ctx: Context) -> list[Launch]:
    # `cmd_resume` drives on into `verifying`, whose gate report is a `None` launch.
    return [launch for launch in _fake(ctx).launches if launch is not None]


def _prompt_of_last_launch(ctx: Context) -> str:
    return (Path(_agent_launches(ctx)[-1].events).parent / "prompt.md").read_text()


def test_a_plain_resume_hands_the_answer_to_the_session_that_asked(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Catches an answer that is recorded but never rendered, and one delivered to a fresh
    # session that lacks the context the question was asked in.
    _agent_blocked(ctx)
    blocked_launch = _agent_launches(ctx)[-1]
    _cli(ctx, monkeypatch)

    assert cli.main(["resume", ctx.run.linear_id, "--blocker-resolution", ANSWER]) == 0

    assert len(_agent_launches(ctx)) == 2
    resumed = _agent_launches(ctx)[-1]
    assert resumed.resume
    assert resumed.session == blocked_launch.session
    prompt = _prompt_of_last_launch(ctx)
    assert ANSWER in prompt
    assert QUESTION in prompt
    assert len(_recorded(ctx)) == 1


def test_resume_from_implementing_delivers_the_answer_to_a_fresh_session(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Catches a target check that refuses the one forced target that reaches the worker.
    _agent_blocked(ctx)
    blocked_launch = _agent_launches(ctx)[-1]
    _cli(ctx, monkeypatch)

    assert (
        cli.main(
            [
                "resume",
                ctx.run.linear_id,
                "--from",
                "implementing",
                "--blocker-resolution",
                ANSWER,
            ]
        )
        == 0
    )

    assert len(_agent_launches(ctx)) == 2
    fresh = _agent_launches(ctx)[-1]
    assert not fresh.resume
    assert fresh.session != blocked_launch.session
    assert ANSWER in _prompt_of_last_launch(ctx)


def test_a_held_resume_keeps_the_answer_for_the_next_resume(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Catches binding the answer to anything a held resume consumes or moves, such as a
    # one-shot flag or the run's attempt counter; a rerun that adds a duplicate row; and
    # a corrected answer losing to the one it replaced.
    _agent_blocked(ctx)
    hold(ctx, "approval")
    launches = len(_agent_launches(ctx))
    _cli(ctx, monkeypatch)

    for text in (SUPERSEDED, ANSWER, ANSWER):
        assert cli.main(["resume", ctx.run.linear_id, "--blocker-resolution", text]) == 0
    ctx.refresh()
    assert ctx.state is State.BLOCKED
    assert len(_agent_launches(ctx)) == launches
    assert [event["instruction"] for event in _recorded(ctx)] == [SUPERSEDED, ANSWER]

    waiting = ctx.store.runtime.settings("run", ctx.run.id)["waiting_invocation"]
    ctx.store.runtime.approve(ctx.run.id, waiting)
    code, _ = cli.dispatch_control(
        "resume",
        ctx.home,
        ctx.registry,
        ctx.routing,
        ctx.store,
        ctx.linear,
        ctx.run,
        context_factory=lambda run: ctx,
    )

    assert code == 0
    assert len(_agent_launches(ctx)) == launches + 1
    prompt = _prompt_of_last_launch(ctx)
    assert ANSWER in prompt
    assert SUPERSEDED not in prompt


def test_an_answer_is_not_carried_to_a_later_block(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Catches an answer read without checking that its blocking transition is the latest.
    _agent_blocked(ctx)
    _fake(ctx).result = dict(GOOD_RESULT, status="blocked", blocked_reason="a second question")
    _cli(ctx, monkeypatch)

    assert cli.main(["resume", ctx.run.linear_id, "--blocker-resolution", ANSWER]) == 0

    assert ANSWER in _prompt_of_last_launch(ctx)
    ctx.refresh()
    assert ctx.state is State.BLOCKED
    latest = ctx.store.transitions(ctx.run.id)[-1]
    assert (latest["rule"], latest["detail"]) == ("agent-blocked", "a second question")
    continuation = recovery.continuation_prompt(ctx)
    assert "a second question" in continuation
    assert ANSWER not in continuation


def test_an_answer_is_dropped_when_the_run_moves_on_without_the_worker(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Catches an answer kept alive only by the launch count: a resume into `verifying`
    # launches no builder, yet the question it answered is no longer the run's.
    _agent_blocked(ctx)
    hold(ctx, "approval")
    _cli(ctx, monkeypatch)
    assert cli.main(["resume", ctx.run.linear_id, "--blocker-resolution", ANSWER]) == 0
    ctx.store.runtime.approve(
        ctx.run.id, ctx.store.runtime.settings("run", ctx.run.id)["waiting_invocation"]
    )

    assert cli.main(["resume", ctx.run.linear_id, "--from", "verifying"]) == 0
    ctx.refresh()
    assert ctx.store.acquire_lease(ctx.run.id, ttl_seconds=60)
    advance(ctx, State.BLOCKED, rule="review-finding", detail="- [high] tests/x.py: vacuous")

    assert blocker_resolution.prompt_section(ctx) == []
    assert ANSWER not in recovery.continuation_prompt(ctx)


def test_an_answer_is_spent_once_a_worker_starts_with_it(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Catches an answer re-rendered after delivery when the worker's next stop is not a
    # new question, such as a block raised while it is still `implementing`.
    _agent_blocked(ctx)
    hold(ctx, "approval")
    _cli(ctx, monkeypatch)
    assert cli.main(["resume", ctx.run.linear_id, "--blocker-resolution", ANSWER]) == 0
    ctx.store.runtime.approve(
        ctx.run.id, ctx.store.runtime.settings("run", ctx.run.id)["waiting_invocation"]
    )
    assert ctx.store.acquire_lease(ctx.run.id, ttl_seconds=60)
    recovery.resume(ctx)
    assert ANSWER in _prompt_of_last_launch(ctx)

    advance(ctx, State.BLOCKED, rule="review-finding", detail="- [high] tests/x.py: vacuous")

    assert blocker_resolution.prompt_section(ctx) == []


def test_an_unacknowledged_spawn_keeps_the_answer(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Catches counting a spawn intent whose exec raised: no worker ran, so the next
    # launch must still carry the answer.
    _agent_blocked(ctx)
    _cli(ctx, monkeypatch)
    fake = _fake(ctx)
    real = fake.exec_detached

    def fail_once(*args: Any) -> None:
        monkeypatch.setattr(fake, "exec_detached", real)
        raise RuntimeError("sbx exec failed")

    monkeypatch.setattr(fake, "exec_detached", fail_once)

    with pytest.raises(RuntimeError):
        cli.main(["resume", ctx.run.linear_id, "--blocker-resolution", ANSWER])

    assert ANSWER in "\n".join(blocker_resolution.prompt_section(ctx))


@pytest.mark.parametrize(("module", "name"), [(execution, "guard"), (workflow_launches, "resume")])
def test_a_resume_that_fails_before_the_worker_starts_keeps_the_answer(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, module: ModuleType, name: str
) -> None:
    # Catches binding the answer to the latest transition alone. `guard` refuses before
    # `blocked -> implementing`; a failed launch refuses after it and records a newer block.
    # Either way no worker has read the answer.
    _agent_blocked(ctx)
    launches = len(_agent_launches(ctx))
    _cli(ctx, monkeypatch)
    real = getattr(module, name)

    def fail_once(*args: Any, **kwargs: Any) -> Any:
        monkeypatch.setattr(module, name, real)
        raise Blocked("launch-preparation-stale", "the sandbox went away")

    monkeypatch.setattr(module, name, fail_once)

    assert cli.main(["resume", ctx.run.linear_id, "--blocker-resolution", ANSWER]) == 2
    assert len(_agent_launches(ctx)) == launches

    assert cli.main(["resume", ctx.run.linear_id]) == 0

    assert len(_agent_launches(ctx)) == launches + 1
    prompt = _prompt_of_last_launch(ctx)
    assert ANSWER in prompt
    assert QUESTION in prompt
    assert len(_recorded(ctx)) == 1
    assert blocker_resolution.prompt_section(ctx) == []


def test_an_answer_queued_for_an_agent_slot_survives_a_stale_launch(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Catches an answer lost when the agent-slot hold, which lands after
    # `blocked -> implementing`, goes stale before launch, and a correction accepted while
    # the queued prompt is already frozen.
    _agent_blocked(ctx)
    launches = len(_agent_launches(ctx))
    ctx.store.runtime.configure("project", ctx.project.name, {"max_active_agents": 1})
    other = ctx.store.insert_run(linear_id="SYN-OTHER", project=ctx.project.name, team="SYN")
    ctx.store.runtime.start_invocation("occupied", other.id, 1, "builder", {})
    jobs = RuntimeJobs(ctx.store)
    assert jobs.schedule_agent("occupied", usd_limit=10, max_attempts=3)
    _cli(ctx, monkeypatch)

    assert cli.main(["resume", ctx.run.linear_id, "--blocker-resolution", ANSWER]) == 0
    ctx.refresh()
    assert ctx.state is State.IMPLEMENTING
    assert cli.main(["resume", ctx.run.linear_id, "--blocker-resolution", SUPERSEDED]) == 2
    ctx.refresh()
    assert ctx.state is State.IMPLEMENTING

    jobs.finish_agent("occupied", status="completed", evidence="exit")
    (ctx.worktree / "operator.txt").write_text("x")
    git(ctx.worktree, "add", "operator.txt")
    git(ctx.worktree, "commit", "-m", "operator change")
    with pytest.raises(Blocked) as stale:
        driver.step(ctx)
    block.record(ctx, stale.value.reason, stale.value.detail)
    assert stale.value.reason == "launch-preparation-stale"
    assert len(_agent_launches(ctx)) == launches

    assert cli.main(["resume", ctx.run.linear_id]) == 0

    prompt = _prompt_of_last_launch(ctx)
    assert ANSWER in prompt
    assert QUESTION in prompt
    assert SUPERSEDED not in prompt


@pytest.mark.parametrize(
    ("block", "extra", "text", "reason"),
    [
        ("review-finding", [], ANSWER, "blocker-resolution-unavailable"),
        ("agent-blocked", ["--from", "verifying"], ANSWER, "blocker-resolution-target-invalid"),
        ("agent-blocked", [], "   \n ", "blocker-resolution-invalid"),
        ("agent-blocked", [], "x" * (16 * 1024 + 1), "blocker-resolution-invalid"),
        ("running", [], ANSWER, "blocker-resolution-unavailable"),
        ("relaunched", [], ANSWER, "blocker-resolution-unavailable"),
    ],
)
def test_a_refused_answer_records_and_launches_nothing(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    block: str,
    extra: list[str],
    text: str,
    reason: str,
) -> None:
    # Catches an answer accepted for a stop the worker did not ask about, for a question a
    # relaunched worker has already moved past, or for a run whose worker is still running, a target that never reaches `continuation_prompt`, an empty
    # or oversized answer, and any refusal that still writes an audit row (its own, or the
    # evidence passed beside it) or moves the run.
    if block == "agent-blocked":
        _agent_blocked(ctx)
    elif block == "running":
        _start_an_attempt(ctx, finish=False)
    elif block == "relaunched":
        _agent_blocked(ctx)
        recovery.resume(ctx)
        advance(ctx, State.BLOCKED, rule="review-finding", detail="- [high] tests/x.py: vacuous")
    else:
        _start_an_attempt(ctx, finish=False)
        advance(ctx, State.BLOCKED, rule="review-finding", detail="- [high] tests/x.py: vacuous")
    evidence = tmp_path / "resolution.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "ticket": ctx.run.linear_id,
                "prerequisite": "host-preflight",
                "status": "resolved",
                "verified_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "verification": {
                    "command": ["uv", "run", "factory", "doctor"],
                    "exit_code": 0,
                    "summary": "doctor passed",
                },
            }
        )
    )
    before = ctx.store.transitions(ctx.run.id)
    events = ctx.store.runtime.events(ctx.run.id)
    launches = len(_agent_launches(ctx))
    _cli(ctx, monkeypatch)

    code = cli.main(
        [
            "resume",
            ctx.run.linear_id,
            *extra,
            "--prerequisite-evidence",
            str(evidence),
            "--blocker-resolution",
            text,
        ]
    )

    assert code == 2
    assert f"[{reason}]" in capsys.readouterr().out
    assert ctx.store.runtime.events(ctx.run.id) == events
    assert len(_agent_launches(ctx)) == launches
    assert ctx.store.transitions(ctx.run.id) == before
    assert not ctx.store.holds_lease(ctx.run.id)
