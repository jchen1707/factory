"""Operator-owned capabilities, restrictions and draining through public controls."""

import pytest

from factory import operator_controls
from factory.steps import Context


def test_controls_restrict_and_inherit_without_resetting_other_settings(ctx: Context) -> None:
    configure = operator_controls.configure
    configure(
        ctx.store,
        "project",
        ctx.run.project,
        {
            "delegation_mode": "read-only",
            "max_active_agents": 4,
            "max_children_per_parent": 2,
            "max_delegation_depth": 1,
        },
    )
    configure(ctx.store, "run", ctx.run.id, {"max_active_agents": 2, "mode": "approval"})
    assert ctx.store.runtime.effective(ctx.run.project, ctx.run.id)["max_active_agents"] == 2
    with pytest.raises(ValueError, match="project"):
        configure(ctx.store, "run", ctx.run.id, {"max_active_agents": 5})
    with pytest.raises(ValueError, match="project"):
        configure(ctx.store, "run", ctx.run.id, {"delegation_mode": "isolated-write"})
    configure(ctx.store, "run", ctx.run.id, {"max_active_agents": None})
    assert "max_active_agents" not in ctx.store.runtime.settings("run", ctx.run.id)
    assert ctx.store.runtime.effective(ctx.run.project, ctx.run.id)["max_active_agents"] == 4
    assert ctx.store.runtime.settings("run", ctx.run.id)["mode"] == "approval"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_active_agents", True),
        ("max_children_per_parent", 0),
        ("max_delegation_depth", 2),
        ("delegation_mode", ""),
        ("max_active_agents", "4"),
    ],
)
def test_controls_refuse_invalid_settings_without_writes(
    ctx: Context, field: str, value: object
) -> None:
    with pytest.raises(ValueError, match=r"positive integer|depth one|invalid delegation"):
        operator_controls.configure(ctx.store, "project", ctx.run.project, {field: value})
    assert ctx.store.runtime.settings("project", ctx.run.project) == {}


def test_project_lowering_restricts_retained_run_settings(ctx: Context) -> None:
    operator_controls.configure(
        ctx.store,
        "project",
        ctx.run.project,
        {"max_active_agents": 4, "delegation_mode": "read-only"},
    )
    operator_controls.configure(
        ctx.store, "run", ctx.run.id, {"max_active_agents": 4, "delegation_mode": "read-only"}
    )
    operator_controls.configure(
        ctx.store,
        "project",
        ctx.run.project,
        {"max_active_agents": 1, "delegation_mode": "disabled"},
    )
    effective = ctx.store.runtime.effective(ctx.run.project, ctx.run.id)
    assert effective["max_active_agents"] == 1
    assert effective["delegation_mode"] == "disabled"
    assert ctx.store.runtime.settings("run", ctx.run.id)["max_active_agents"] == 4


def test_runtime_status_preserves_incomplete_cost_and_certification_failure(ctx: Context) -> None:
    from factory.runtime_jobs import RuntimeJobs

    ctx.store.runtime.start_invocation("observed", ctx.run.id, 1, "certification", {})
    ctx.store.runtime.observe("observed", 1, {"estimate": {"usd": 0.25, "complete": True}})
    job = RuntimeJobs(ctx.store).request_certification(ctx.run.id, {"test": "identity"})
    observed = operator_controls.status(ctx.store, ctx.run.project, ctx.run.id)
    assert observed["api_equivalent_estimate_usd"] == 0.25
    assert observed["cost_complete"] is False
    assert observed["pending_certifications"] == 1
    token = RuntimeJobs(ctx.store).claim_certification(job["id"], now=10, duration=10)
    assert token is not None
    RuntimeJobs(ctx.store).finish_certification(
        job["id"], token, now=11, failure="changed generation"
    )
    observed = operator_controls.status(ctx.store, ctx.run.project, ctx.run.id)
    assert observed["certifications"][0]["failure"] == "changed generation"
    ctx.store.runtime.start_invocation("missing", ctx.run.id, 1, "child:unpriced", {})
    observed = operator_controls.status(ctx.store, ctx.run.project, ctx.run.id)
    assert observed["cost_complete"] is False
    assert observed["unpriced_invocations"] == 1
    assert observed["api_equivalent_estimate_usd"] == 0.25


def test_cli_configuration_reports_effective_caps_and_retained_status(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from factory import cli, configuration_cli, registry

    monkeypatch.setattr(cli, "factory_home", lambda: ctx.home)
    monkeypatch.setattr(registry, "load_registry", lambda _: ctx.registry)
    args = cli.build_parser().parse_args(
        [
            "configure",
            "--project",
            ctx.run.project,
            "--delegation-mode",
            "read-only",
            "--max-active-agents",
            "4",
        ]
    )
    assert configuration_cli.configure(args) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["settings"]["delegation_mode"] == "read-only"
    assert response["effective"]["max_active_agents"] == 4

    assert response["runtime"]["cost_complete"] is False
    args = cli.build_parser().parse_args(
        [
            "configure",
            "--ticket",
            ctx.run.linear_id,
            "--max-active-agents",
            "inherit",
        ]
    )
    assert configuration_cli.configure(args) == 0
    response = json.loads(capsys.readouterr().out)
    assert "max_active_agents" not in response["settings"]
    assert response["effective"]["max_active_agents"] == 4


def test_runtime_status_does_not_count_running_children_as_queued(ctx: Context) -> None:
    from factory.delegation import DelegationBroker
    from factory.operator_controls import status
    from factory.runtime_jobs import RuntimeJobs
    from tests.integration.test_workflow_children import prepared
    from tests.unit.test_delegation import task

    prepared(ctx)
    parent = "status-parent"
    ctx.store.runtime.start_invocation(
        parent, ctx.run.id, 1, "implement", {"semantic_role": "builder", "policy_revision": 1}
    )
    jobs = RuntimeJobs(ctx.store)
    assert jobs.schedule_agent(parent, usd_limit=10, max_attempts=5)
    broker = DelegationBroker(ctx.store, parent, ctx.worktree)
    request = broker.request(
        "status-child", task() | {"paths": ["harness.config.json"], "role": "documenter"}
    )
    child = "status-child-invocation"
    ctx.store.runtime.start_invocation(
        child,
        ctx.run.id,
        1,
        "child",
        {"semantic_role": "documenter", "parent_id": parent, "policy_revision": 1},
    )
    broker.bind_child(request["id"], child)
    assert status(ctx.store, ctx.project.name, ctx.run.id)["queued_children"] == 1
    assert jobs.schedule_agent(child, parent_id=parent, usd_limit=10, max_attempts=5)
    observed = status(ctx.store, ctx.project.name, ctx.run.id)
    assert observed["active_agents"] == 2
    assert observed["queued_children"] == 0


def test_runtime_status_keeps_priced_portion_of_incomplete_child_usage(ctx: Context) -> None:
    ctx.store.runtime.start_invocation("partial-child", ctx.run.id, 1, "child", {})
    ctx.store.runtime.observe(
        "partial-child",
        1,
        {"estimate": {"usd": None, "known_usd": 0.125, "complete": False}},
    )
    for run_id in (ctx.run.id, None):
        observed = operator_controls.status(ctx.store, ctx.project.name, run_id)
        assert observed["api_equivalent_estimate_usd"] == ctx.store.known_spend(ctx.run.id)
        assert observed["api_equivalent_estimate_usd"] == 0.125
        assert observed["cost_complete"] is False
        assert observed["unpriced_invocations"] == 1
