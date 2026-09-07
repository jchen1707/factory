"""Control-plane regressions, using retained evidence and isolated SQLite databases."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from factory import accounting, authority, execution, handoffs
from factory.machine import Blocked
from factory.steps import Context


@pytest.fixture(autouse=True)
def shared_delivery_contract(ctx: Context) -> None:
    import shutil

    source = Path(__file__).parents[3] / "harness/plugins/harness"
    for name in ("hooks/delivery_policy.mjs", "docs/agents/delivery-review.md"):
        target = ctx.project.path / ".agents/vendor/harness" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)


def test_review_retry_requires_new_approval_and_preserves_both_invocations(ctx: Context) -> None:
    ctx.store.runtime.configure("run", ctx.run.id, {"mode": "approval"})
    with pytest.raises(execution.AgentApprovalRequired, match="1:review:1"):
        execution.guard(ctx, 1, "review")
    ctx.store.runtime.approve(ctx.run.id, "1:review:1")
    execution.guard(ctx, 1, "review")
    first = accounting.begin(ctx, 1, ctx.routing.role("reviewer"), "review:spec", ctx.home / "a")
    with pytest.raises(execution.AgentApprovalRequired, match="1:review:2"):
        execution.guard(ctx, 1, "review")
    ctx.store.runtime.approve(ctx.run.id, "1:review:2")
    execution.guard(ctx, 1, "review")
    second = accounting.begin(ctx, 1, ctx.routing.role("reviewer"), "review:spec", ctx.home / "b")
    assert first != second
    assert len(ctx.store.runtime.invocations(ctx.run.id)) == 2
    assert len(ctx.store.costs(ctx.run.id)) == 2


def test_accounting_replay_repairs_interrupted_cost_reconciliation(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = ctx.home / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10},
            }
        )
        + "\n"
    )
    accounting.begin(ctx, 1, ctx.routing.role("builder"), "implement", events)
    original = ctx.store.reconcile_cost
    with monkeypatch.context() as patch:

        def crash(*args: object, **kwargs: object) -> None:
            raise RuntimeError("process died after observation")

        patch.setattr(ctx.store, "reconcile_cost", crash)
        with pytest.raises(RuntimeError, match="process died"):
            accounting.collect(ctx, 1, "implement", events)
    assert ctx.store.reconcile_cost == original
    accounting.collect(ctx, 1, "implement", events)
    accounting.collect(ctx, 1, "implement", events)
    assert ctx.store.spend(ctx.run.id) == (100, 10, None)
    assert len(ctx.store.costs(ctx.run.id)) == 1


def test_app_server_accounting_prices_failed_usage_once_and_preserves_unknown_fragments(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ctx.agent, "report", {}, raising=False)
    prices = Path(__file__).parents[2] / "config/prices.toml"
    (ctx.home / "config/prices.toml").write_text(prices.read_text())
    events = ctx.home / "events.jsonl"
    usage = {
        "input_tokens": 1000,
        "cached_input_tokens": 400,
        "cache_write_input_tokens": 100,
        "output_tokens": 200,
        "reasoning_output_tokens": 10,
    }
    normalized = {
        "type": "factory.usage",
        "usage": usage,
        "complete": True,
        "thread_total": {},
        "pricing_complete": True,
        "requests": [
            {
                "model": "gpt-5.6-sol",
                "usage": usage,
                "observed_at": 1788652800,
                "service_tier": "standard",
                "long_context": False,
            }
        ],
    }
    events.write_text(
        json.dumps({"type": "turn.failed", "error": {"message": "failed"}})
        + "\n"
        + json.dumps(normalized)
        + "\n"
    )
    invocation = accounting.begin(ctx, 1, ctx.routing.role("builder"), "implement", events)
    accounting.collect(ctx, 1, "implement", events)
    accounting.collect(ctx, 1, "implement", events)
    expected = (500 * 4 + 400 * 0.4 + 100 * 5 + 200 * 20) / 1_000_000
    assert ctx.store.spend(ctx.run.id) == (1000, 200, pytest.approx(expected))
    assert len(ctx.store.costs(ctx.run.id)) == 1
    normalized["pricing_complete"] = False
    with events.open("a") as stream:
        stream.write(json.dumps(normalized) + "\n")
    accounting.collect(ctx, 1, "implement", events)
    assert ctx.store.spend(ctx.run.id)[2] is None
    assert ctx.store.known_spend(ctx.run.id) == pytest.approx(expected)
    retained = ctx.store.runtime.invocation(invocation)
    assert retained is not None
    assert retained["telemetry"]["estimate"]["complete"] is False


def test_interrupted_worker_stream_retains_usage_baseline_and_enforces_known_spend(
    ctx: Context, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory.agent import app_server_worker
    from tests.unit.test_app_server_worker import (
        counts,
        run_client,
        turn_end,
        turn_response,
        usage_event,
    )

    monkeypatch.setattr(ctx.agent, "report", {}, raising=False)
    monkeypatch.setattr(app_server_worker.time, "time", lambda: 1788652800)
    prices = Path(__file__).parents[2] / "config/prices.toml"
    (ctx.home / "config/prices.toml").write_text(prices.read_text())
    before = counts(500, 100, 50)
    delta = counts(100, 20, 10)
    total = {key: before[key] + value for key, value in delta.items()}
    code, emitted, _ = run_client(
        tmp_path,
        monkeypatch,
        [turn_response(), usage_event(total, delta), turn_end()],
        baseline=before,
        resume=True,
    )
    assert code == 0
    completion = next(
        index
        for index, event in enumerate(emitted)
        if event["type"] == "factory.runtime" and event["event"].get("method") == "turn/completed"
    )
    # Only bytes flushed before completion survive abrupt termination. There is no
    # final worker observation or completed-turn aggregate in the retained stream.
    events = ctx.home / "interrupted-events.jsonl"
    events.write_text("".join(json.dumps(event) + "\n" for event in emitted[:completion]))
    invocation = accounting.begin(ctx, 1, ctx.routing.role("builder"), "implement", events)
    accounting.collect(ctx, 1, "implement", events)
    accounting.collect(ctx, 1, "implement", events)
    expected = (77 * 4 + 20 * 0.4 + 3 * 5 + 10 * 20) / 1_000_000
    assert ctx.store.spend(ctx.run.id) == (100, 10, None)
    assert ctx.store.known_spend(ctx.run.id) == pytest.approx(expected)
    assert len(ctx.store.costs(ctx.run.id)) == 1
    retained = ctx.store.runtime.invocation(invocation)
    assert retained is not None
    assert retained["telemetry"]["thread_total_wire"] == total
    assert retained["telemetry"]["usage_complete"] is False
    assert retained["telemetry"]["estimate"]["complete"] is False
    ctx.routing = replace(ctx.routing, usd_per_run=expected / 2)
    with pytest.raises(Blocked, match="budget-exceeded"):
        execution.guard(ctx, 2, "implement")


def test_authority_snapshot_reconciles_publication_before_database_commit(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = ctx.project.path / "harness.config.json"
    config = json.loads(config_path.read_text())
    config["delivery"] = {
        "default": "core",
        "requirements": {},
        "profiles": {"core": {"required": [], "deferrals": []}},
    }
    config_path.write_text(json.dumps(config))
    original = ctx.store.runtime.snapshot_policy
    with monkeypatch.context() as patch:

        def crash(*args: object, **kwargs: object) -> None:
            raise RuntimeError("died before database commit")

        patch.setattr(ctx.store.runtime, "snapshot_policy", crash)
        with pytest.raises(RuntimeError, match="died before"):
            authority.snapshot(ctx)
    assert ctx.store.runtime.snapshot_policy == original
    config["delivery"]["default"] = "prototype"
    config["delivery"]["profiles"]["prototype"] = {"required": [], "deferrals": []}
    config_path.write_text(json.dumps(config))
    result = authority.snapshot(ctx)
    assert result is not None
    assert result["profile"] == "core"
    assert result["revision"] == 1
    assert authority.snapshot(ctx) == result


def test_rephrasing_diagnosis_cannot_start_a_new_failure_episode(ctx: Context) -> None:
    ctx.run = replace(ctx.run, worktree=str(ctx.project.path))
    path = ctx.factory_dir / "run/1/gates.json"
    path.parent.mkdir(parents=True)
    report = {
        "verdict": "fail",
        "gates": [
            {"name": "acceptance", "status": "fail", "exit": 1, "outputTail": "login rejected"}
        ],
    }
    path.write_text(json.dumps(report))
    handoffs.record_failure(ctx, path, report)
    diagnosis = {
        "classification": "code",
        "status": "repair",
        "summary": "Fix login",
        "reproduction_evidence": "run/1/gates.json",
        "acceptance_behavior": "login rejected",
    }
    handoffs.authorize_repair(ctx, diagnosis, 2)
    diagnosis["acceptance_behavior"] = "password login is rejected"
    with pytest.raises(Blocked, match="failure-without-new-evidence"):
        handoffs.authorize_repair(ctx, diagnosis, 3)
    # Changing the retained artifact after host collection cannot authorize a repair.
    path.write_text(json.dumps(report) + "\n")
    with pytest.raises(Blocked, match="evidence changed"):
        handoffs.authorize_repair(ctx, diagnosis, 3)


@pytest.mark.parametrize("location", ["../escaped", "/escaped-authority"])
def test_authority_refuses_nonrelative_review_paths(ctx: Context, location: str) -> None:
    path = ctx.project.path / "harness.config.json"
    config = json.loads(path.read_text())
    config["delivery"] = {
        "default": "core",
        "requirements": {},
        "profiles": {"core": {"required": [], "deferrals": []}},
    }
    config.setdefault("review", {})["agentDir"] = location
    path.write_text(json.dumps(config))
    with pytest.raises(Blocked, match="authority-path"):
        authority.snapshot(ctx)
    assert ctx.store.runtime.policy(ctx.run.id) is None


def test_authority_refuses_nested_symlink_before_copy(ctx: Context, tmp_path: Path) -> None:
    path = ctx.project.path / "harness.config.json"
    config = json.loads(path.read_text())
    config["delivery"] = {
        "default": "core",
        "requirements": {},
        "profiles": {"core": {"required": [], "deferrals": []}},
    }
    config.setdefault("review", {})["agentDir"] = "review-input"
    path.write_text(json.dumps(config))
    external = tmp_path / "external-authority"
    external.mkdir()
    (external / "secret").write_text("outside project")
    inputs = ctx.project.path / "review-input"
    inputs.mkdir()
    (inputs / "nested").symlink_to(external, target_is_directory=True)
    with pytest.raises(Blocked, match="authority-path"):
        authority.snapshot(ctx)
    assert ctx.store.runtime.policy(ctx.run.id) is None


def test_authority_detects_modified_or_added_snapshot_files(ctx: Context) -> None:
    path = ctx.project.path / "harness.config.json"
    config = json.loads(path.read_text())
    config["delivery"] = {
        "default": "core",
        "requirements": {},
        "profiles": {"core": {"required": [], "deferrals": []}},
    }
    path.write_text(json.dumps(config))
    result = authority.snapshot(ctx)
    assert result is not None
    root = Path(result["root"])
    assert authority.current(ctx) == root
    injected = root / "extra.md"
    injected.write_text("new instructions")
    with pytest.raises(Blocked, match="authority-integrity"):
        authority.current(ctx)
    injected.unlink()
    (root / "harness.config.json").write_text("{}")
    with pytest.raises(Blocked, match="authority-integrity"):
        authority.snapshot(ctx)


def test_concurrency_raise_requires_measurements_and_lowering_drains(ctx: Context) -> None:
    from factory import operator_controls

    previous = ctx.registry.concurrency_for(ctx.project)
    with pytest.raises(Blocked, match="isolation-validation-required"):
        operator_controls.configure(
            ctx.store,
            "project",
            ctx.project.name,
            {"concurrency": previous + 1},
            registry=ctx.registry,
        )
    assert ctx.store.runtime.settings("project", ctx.project.name) == {}
    operator_controls.configure(
        ctx.store,
        "project",
        ctx.project.name,
        {"concurrency": 1},
        registry=ctx.registry,
    )
    assert ctx.store.run_by_id(ctx.run.id) == ctx.run


def test_per_run_isolation_requires_measured_evidence(ctx: Context, tmp_path: Path) -> None:
    import hashlib

    from factory import isolation, operator_controls

    capture = tmp_path / "capture.txt"
    capture.write_text("synthetic fixture; never deployment evidence")
    check = {
        "status": "pass",
        "evidence": capture.name,
        "sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
    }
    manifest = tmp_path / "isolation.json"
    manifest.write_text(
        json.dumps(
            {
                "project": ctx.project.name,
                "layout": "bind",
                "checks": dict.fromkeys(
                    (
                        "two_runs",
                        "dependencies",
                        "temporary_files",
                        "databases",
                        "ports",
                        "cancellation",
                    ),
                    check,
                ),
            }
        )
    )
    operator_controls.configure(
        ctx.store,
        "project",
        ctx.project.name,
        {
            "isolation": "per-run",
            "isolation_measurement": str(manifest),
            "concurrency": 2,
        },
        registry=ctx.registry,
    )
    prepared = isolation.prepare(ctx.project, ctx.run, ctx.store)
    assert prepared.build_sandbox.endswith(ctx.run.id)
    assert prepared.review_sandbox.endswith(ctx.run.id)
    capture.write_text("changed evidence")
    with pytest.raises(Blocked, match="isolation-validation-required"):
        operator_controls.configure(
            ctx.store,
            "project",
            ctx.project.name,
            {
                "concurrency": 3,
            },
            registry=ctx.registry,
        )


def test_runtime_selection_keeps_legacy_runs_and_pins_new_runs(ctx: Context) -> None:
    from factory.agent import selection
    from factory.agent.codex import CodexAdapter

    ctx.store.runtime.configure("project", ctx.project.name, {"agent_adapter": "app-server"})
    ctx.run = replace(ctx.run, attempt=1)
    selection.select(ctx)
    assert type(ctx.agent) is CodexAdapter
    assert ctx.store.runtime.settings("run", ctx.run.id)["agent_adapter"] == "codex-exec"


def test_runtime_selection_validates_executing_version_and_retains_baseline(
    ctx: Context,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    from factory.agent import selection
    from factory.agent.app_server import COMPATIBILITY_CHECKS, AppServerAdapter
    from factory.sandbox.base import Completed

    ctx.store.runtime.configure(
        "project",
        ctx.project.name,
        {
            "agent_adapter": "app-server",
            "app_server_compatibility": str(tmp_path),
        },
    )
    capture = tmp_path / "capture"
    capture.write_text("synthetic protocol fixture, not activation evidence")
    report = {
        "runtime_version": "codex-cli 0.153.4",
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
    manifest = tmp_path / f"{ctx.project.build_sandbox}.json"
    manifest.write_text(json.dumps(report))
    monkeypatch.setattr(
        ctx.sandbox,
        "exec_sync",
        lambda *args, **kwargs: Completed(
            ("codex", "--version"),
            0,
            "codex-cli 0.153.4\n",
            "",
        ),
    )
    selection.select(ctx)
    assert isinstance(ctx.agent, AppServerAdapter)
    ctx.store.runtime.start_invocation("prior", ctx.run.id, 1, "implement", {})
    ctx.store.runtime.observe(
        "prior", 1, {"thread_id": "thread", "thread_total_wire": {"inputTokens": 12}}
    )
    selection.select(ctx)
    assert ctx.agent.baselines == {"thread": {"inputTokens": 12}}
    report["runtime_version"] = "older"
    manifest.write_text(json.dumps(report))
    with pytest.raises(Blocked, match="compatibility"):
        selection.select(ctx)


def test_historical_disputes_and_stale_authority_cannot_authorize_repairs(ctx: Context) -> None:
    fixture = Path(__file__).parents[1] / "fixtures/historical-workflow-evaluations.json"
    for case in json.loads(fixture.read_text())["cases"]:
        with pytest.raises(Blocked, match=f"diagnosis-{case['classification']}"):
            handoffs.authorize_repair(ctx, case | {"summary": case["scenario"]}, 1)
    assert ctx.store.runtime.db.execute("SELECT COUNT(*) FROM failure_episodes").fetchone()[0] == 0


def test_evaluation_preserves_unknown_cost_and_counts_explicit_approvals(ctx: Context) -> None:
    from factory.evaluation import summarize

    ctx.store.runtime.approve(ctx.run.id, "1:implement:1")
    metrics = summarize(ctx.store, ctx.project.name)
    assert metrics["human_interventions"] == 1
    assert metrics["cost_complete"] is False
    assert metrics["estimated_usd_per_accepted_change"] is None
    assert metrics["accepted_changes"] == 0


def test_isolated_delivery_requires_verification_against_current_base(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from factory import integration_base, repo
    from factory.sandbox.base import Completed

    ctx.run = replace(ctx.run, worktree=str(ctx.project.path))
    ctx.store.runtime.configure("run", ctx.run.id, {"isolation": "per-run"})
    monkeypatch.setattr(repo, "fetch", lambda *args: None)
    monkeypatch.setattr(repo, "head_sha", lambda *args: "base-one")
    monkeypatch.setattr(ctx.sandbox, "exec_sync", lambda *args, **kwargs: Completed((), 0, "", ""))
    integration_base.before_verification(ctx)
    integration_base.before_delivery(ctx)
    monkeypatch.setattr(repo, "head_sha", lambda *args: "base-two")
    with pytest.raises(Blocked, match="integration-evidence-stale"):
        integration_base.before_delivery(ctx)
    monkeypatch.setattr(ctx.sandbox, "exec_sync", lambda *args, **kwargs: Completed((), 1, "", ""))
    with pytest.raises(Blocked, match="integration-base-refresh-required"):
        integration_base.before_verification(ctx)
    assert ctx.store.runtime.settings("run", ctx.run.id)["verification_base"] == "base-one"


def test_per_run_writable_protocol_mounts_do_not_include_another_run(ctx: Context) -> None:
    from factory.steps.review import _review_scratch

    first = ctx.run
    ctx.store.runtime.configure("run", first.id, {"isolation": "per-run"})
    clone_mount, review_mount = ctx.clone_mount, _review_scratch(ctx)
    ctx.run = replace(first, id="another-run")
    ctx.store.runtime.configure("run", ctx.run.id, {"isolation": "per-run"})
    assert not ctx.clone_mount.is_relative_to(clone_mount)
    assert not _review_scratch(ctx).is_relative_to(review_mount)


def test_snapshot_never_executes_a_target_supplied_policy_interpreter(ctx: Context) -> None:
    config_path = ctx.project.path / "harness.config.json"
    config = json.loads(config_path.read_text())
    config["delivery"] = {
        "default": "core",
        "requirements": {},
        "profiles": {"core": {"required": [], "deferrals": []}},
    }
    config_path.write_text(json.dumps(config))
    interpreter = ctx.project.path / ".agents/vendor/harness/hooks/delivery_policy.mjs"
    interpreter.write_text("throw new Error('target code must never run on the host');")
    with pytest.raises(Blocked, match="delivery-interpreter-mismatch"):
        authority.snapshot(ctx)
    assert ctx.store.runtime.policy(ctx.run.id) is None


def test_repeated_failure_metric_counts_failures_even_when_next_repair_is_refused(
    ctx: Context,
) -> None:
    from factory.evaluation import summarize

    for attempt in (1, 2):
        ctx.store.record_check(
            ctx.run.id,
            attempt,
            "failure-reproduction",
            "fail",
            detail=json.dumps({"episode": "same-failure"}),
        )
    metrics = summarize(ctx.store)
    assert metrics["failure_episodes"] == 1
    assert metrics["repeated_failure_episodes"] == 1


def test_invocation_retains_selected_preset_when_operator_changes_future_routing(
    ctx: Context,
) -> None:
    from factory.routing import Role

    selected = Role("builder", "gpt-5.6-terra", "medium", preset="volume")
    ctx.store.runtime.configure("project", ctx.project.name, {"model_preset": "high-confidence"})
    invocation = accounting.begin(ctx, 1, selected, "implement", ctx.home / "selected-events")
    retained = ctx.store.runtime.invocation(invocation)
    assert retained is not None
    assert retained["metadata"]["preset"] == "volume"
    assert retained["metadata"]["model"] == "gpt-5.6-terra"
