"""Synthetic metadata probes never start a Codex thread or model turn."""

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from factory import execution
from factory.agent import app_server_worker
from factory.agent.base import AgentInvocation
from factory.agent.codex import CodexAdapter
from factory.machine import Blocked
from factory.routing import Role
from factory.sandbox.base import Completed
from factory.sandbox.sbx import SbxError
from factory.steps import Context
from factory.store import Store

MODELS = [
    {"model": "gpt-5.6-terra", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]},
    {"model": "gpt-5.6-sol", "supportedReasoningEfforts": [{"reasoningEffort": "high"}]},
]


@pytest.fixture
def probe_context(tmp_path: Path) -> Any:
    store = Store(tmp_path / "factory.db")
    run = store.insert_run(linear_id="BAC-99", project="example", team="BAC")
    store.runtime.configure("project", "example", {"model_preset": "volume"})
    calls = []

    def exec_sync(name: str, argv: list[str], **kwargs: Any) -> Completed:
        calls.append((name, argv, kwargs))
        assert argv[0] == "python3"
        assert Path(argv[1]).name == "app_server_worker.py"
        assert Path(argv[1]).is_file()
        assert json.loads(Path(argv[2]).read_text()) == {"probe_models": True}
        return Completed(
            tuple(argv), 0, json.dumps({"type": "factory.models", "models": MODELS}), ""
        )

    ctx = SimpleNamespace(
        store=store,
        run=run,
        home=tmp_path,
        project=SimpleNamespace(
            name="example",
            build_sandbox="factory-build-example",
            review_sandbox="factory-review-example",
        ),
        agent=CodexAdapter(),
        sandbox=SimpleNamespace(exec_sync=exec_sync),
        env={"UV_PROJECT_ENVIRONMENT": "/venvs/example/BAC-99"},
        factory_dir=tmp_path / "worktree/.factory",
        routing=SimpleNamespace(role=lambda name: Role(name, "existing-model", "high")),
        calls=calls,
    )
    yield ctx
    store.close()


@pytest.mark.parametrize(
    ("role", "model", "effort"),
    [("builder", "gpt-5.6-terra", "medium"), ("reviewer", "gpt-5.6-sol", "high")],
)
def test_presets_validate_executing_runtime_and_keep_legacy_exec(
    probe_context: Any, role: str, model: str, effort: str
) -> None:
    ctx = probe_context
    adapter = ctx.agent
    assert execution.role_for(cast(Context, ctx), role) == Role(
        role, model, effort, preset="volume"
    )
    assert ctx.agent is adapter
    invocation = cast(
        AgentInvocation,
        SimpleNamespace(
            resume_session=None,
            model=model,
            effort=effort,
            vault_directory="/vault",
            schema_path="/schema.json",
            output_path="/output.json",
            workdir="/worktree",
        ),
    )
    command = adapter.command(invocation)
    assert command[:2] == ["codex", "exec"]
    assert command[command.index("-m") + 1] == model
    assert f"model_reasoning_effort={effort}" in command
    assert len(ctx.calls) == 1
    sandbox, argv, kwargs = ctx.calls[0]
    if role == "reviewer":
        assert sandbox == "factory-review-example"
        assert Path(argv[1]).is_relative_to(ctx.home / "state/review/example" / ctx.run.id)
        assert not ctx.factory_dir.exists()
    else:
        assert sandbox == "factory-build-example"
        assert Path(argv[1]).is_relative_to(ctx.factory_dir)
    assert kwargs["env"] == ctx.env
    assert kwargs["timeout"] <= 60
    assert not ctx.store.runtime.invocations(ctx.run.id)
    assert not ctx.store.costs(ctx.run.id)


def test_existing_routing_does_not_probe(probe_context: Any) -> None:
    probe_context.store.runtime.configure("project", "example", {"model_preset": "existing"})
    assert execution.role_for(cast(Context, probe_context), "builder").model == "existing-model"
    assert not probe_context.calls


def test_unsupported_effort_fails_closed(probe_context: Any) -> None:
    probe_context.store.runtime.configure("project", "example", {"model_preset": "high-confidence"})
    with pytest.raises(Blocked, match="runtime-model-unavailable"):
        execution.role_for(cast(Context, probe_context), "reviewer")


@pytest.mark.parametrize(("code", "stdout"), [(1, ""), (0, "not-json"), (0, "{}")])
def test_unavailable_or_malformed_probe_fails_closed(
    probe_context: Any, code: int, stdout: str
) -> None:
    probe_context.sandbox.exec_sync = lambda name, argv, **kwargs: Completed(
        tuple(argv), code, stdout, ""
    )
    with pytest.raises(Blocked, match="runtime-model-probe-failed"):
        execution.role_for(cast(Context, probe_context), "builder")


def test_sandbox_probe_failure_reports_runtime_metadata_problem(probe_context: Any) -> None:
    def unavailable(*args: Any, **kwargs: Any) -> Completed:
        raise SbxError("sandbox command timed out")

    probe_context.sandbox.exec_sync = unavailable
    with pytest.raises(Blocked, match=r"runtime-model-probe-failed.*factory-build-example"):
        execution.role_for(cast(Context, probe_context), "builder")


def test_probe_pages_catalogue_without_starting_a_thread_or_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Input(io.StringIO):
        def close(self) -> None:
            pass

    sent = Input()
    pages = [
        {"id": 1, "result": {}},
        {"id": 3, "result": {"data": MODELS[:1], "nextCursor": "next-page"}},
        {"id": 4, "result": {"data": MODELS[1:], "nextCursor": None}},
    ]
    process = SimpleNamespace(
        stdin=sent,
        stdout=io.StringIO("".join(json.dumps(page) + "\n" for page in pages)),
        wait=lambda **kwargs: 0,
    )
    argv = []

    def popen(command: list[str], **kwargs: Any) -> Any:
        argv.extend(command)
        return process

    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(app_server_worker.subprocess, "Popen", popen)
    monkeypatch.setattr(app_server_worker, "emit", emitted.append)
    assert app_server_worker.run({"probe_models": True}) == 0
    messages = [json.loads(line) for line in sent.getvalue().splitlines()]
    assert [message["method"] for message in messages] == [
        "initialize",
        "initialized",
        "model/list",
        "model/list",
    ]
    assert messages[-1]["params"]["cursor"] == "next-page"
    assert argv == ["codex", "app-server", "--stdio"]
    assert emitted[-1] == {"type": "factory.models", "models": MODELS}
    assert not any(
        event["type"] in {"factory.usage", "thread.started", "turn.completed"} for event in emitted
    )
