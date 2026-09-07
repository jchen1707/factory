"""Failed resumes must not replace retained counters with an empty baseline."""

import hashlib
import json
from pathlib import Path

import pytest

from factory.agent import app_server, selection
from factory.agent.app_server import COMPATIBILITY_CHECKS, AppServerAdapter
from factory.sandbox.base import Completed
from factory.steps import Context
from tests.unit.test_app_server_worker import (
    counts,
    run_client,
    turn_end,
    turn_response,
    usage_event,
)


@pytest.fixture
def app_server_selection(ctx: Context, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx.store.runtime.configure(
        "run",
        ctx.run.id,
        {
            "agent_adapter": "app-server",
            "app_server_compatibility": str(tmp_path),
        },
    )
    capture = tmp_path / "capture"
    capture.write_text("synthetic compatibility fixture")
    report = {
        "runtime_version": "codex-cli 0.153.4",
        "worker_sha256": hashlib.sha256(
            Path(app_server.__file__).with_name("app_server_worker.py").read_bytes()
        ).hexdigest(),
        "sandbox": ctx.project.build_sandbox,
        "checks": {
            name: {
                "status": "pass",
                "evidence": capture.name,
                "sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
            }
            for name in COMPATIBILITY_CHECKS
        },
    }
    (tmp_path / f"{ctx.project.build_sandbox}.json").write_text(json.dumps(report))
    monkeypatch.setattr(
        ctx.sandbox,
        "exec_sync",
        lambda *args, **kwargs: Completed(("codex", "--version"), 0, "codex-cli 0.153.4\n", ""),
    )


def test_failed_resume_does_not_rebill_prior_thread_tokens(
    ctx: Context, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app_server_selection: None
) -> None:
    before = counts(500, 100, 50)
    for identifier, wire in (("a-original", before), ("b-failed-resume", {})):
        ctx.store.runtime.start_invocation(identifier, ctx.run.id, 1, "implement", {})
        ctx.store.runtime.observe(identifier, 1, {"thread_id": "thread", "thread_total_wire": wire})
    selection.select(ctx)
    assert isinstance(ctx.agent, AppServerAdapter)
    delta = counts(100, 20, 10)
    total = {key: before[key] + value for key, value in delta.items()}
    code, emitted, _ = run_client(
        tmp_path,
        monkeypatch,
        [turn_response(), usage_event(total, delta), turn_end()],
        baseline=ctx.agent.baselines.get("thread"),
        resume=True,
    )
    assert code == 0
    usage = [row for row in emitted if row["type"] == "factory.usage"][-1]
    assert usage["usage"]["input_tokens"] == 100
    assert usage["usage"]["output_tokens"] == 10
    assert usage["complete"] is True


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"inputTokens": -1},
        {"inputTokens": True},
        {"inputTokens": "12"},
        {"totalTokens": 12},
        {"inputTokens": 12},
    ],
)
def test_no_valid_retained_baseline_keeps_resumed_usage_unknown(
    ctx: Context,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_server_selection: None,
    invalid: dict[str, object],
) -> None:
    ctx.store.runtime.start_invocation("failed", ctx.run.id, 1, "implement", {})
    ctx.store.runtime.observe("failed", 1, {"thread_id": "thread", "thread_total_wire": invalid})
    selection.select(ctx)
    assert isinstance(ctx.agent, AppServerAdapter)
    assert "thread" not in ctx.agent.baselines
    code, emitted, _ = run_client(
        tmp_path,
        monkeypatch,
        [turn_response(), usage_event(counts(600, 120, 60), counts(100, 20, 10)), turn_end()],
        baseline=ctx.agent.baselines.get("thread"),
        resume=True,
    )
    assert code == 0
    usage = [row for row in emitted if row["type"] == "factory.usage"][-1]
    assert usage["complete"] is False


def test_latest_valid_baseline_survives_later_empty_attempt(
    ctx: Context, app_server_selection: None
) -> None:
    for identifier, wire in (("a", counts(100)), ("b", counts(200)), ("c", {})):
        ctx.store.runtime.start_invocation(identifier, ctx.run.id, 1, "implement", {})
        ctx.store.runtime.observe(identifier, 1, {"thread_id": "thread", "thread_total_wire": wire})
    selection.select(ctx)
    assert isinstance(ctx.agent, AppServerAdapter)
    assert ctx.agent.baselines == {"thread": counts(200)}


def test_protocol_optional_cache_write_counter_may_be_absent(
    ctx: Context, app_server_selection: None
) -> None:
    wire = counts(200)
    del wire["cacheWriteInputTokens"]
    ctx.store.runtime.start_invocation("prior", ctx.run.id, 1, "implement", {})
    ctx.store.runtime.observe("prior", 1, {"thread_id": "thread", "thread_total_wire": wire})
    selection.select(ctx)
    assert isinstance(ctx.agent, AppServerAdapter)
    assert ctx.agent.baselines == {"thread": wire}
