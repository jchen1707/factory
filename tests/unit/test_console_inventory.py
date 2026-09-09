from pathlib import Path
from typing import Any

import pytest

from factory.console import views
from factory.store import Store


def test_current_inventory_preserves_multiple_workspaces(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    row = views.runtimes(
        [{"name": "factory-build-a", "status": "stopped", "workspaces": ["/a", "/b"]}], store
    )[0]
    assert row.state == "stopped"
    assert row.workspace == "/a, /b"
    assert row.runs_using == []


def test_missing_inventory_fields_are_explicit(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    row = views.runtimes([{"name": "codex-owned", "workspace": {"bad": True}}], store)[0]
    assert row.state == "Unavailable"
    assert row.workspace == "Unavailable"
    assert row.operator_owned


def test_usage_distinguishes_missing_placeholder_and_observed_zero(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    assert views.run_usage(store, run).usage_status == "unknown"
    store.runtime.start_invocation("invoke", run.id, 1, "builder", {"cost_step": "builder"})
    store.record_cost(
        run.id, 1, "builder", model="m", input_tokens=0, output_tokens=0, cached_tokens=0, usd=None
    )
    assert views.run_usage(store, run).usage_status == "unknown"
    store.runtime.observe(
        "invoke",
        1,
        {
            "usage": {"input_tokens": 0},
            "usage_complete": True,
            "estimate": {"complete": True, "usd": 0},
        },
    )
    store.reconcile_cost(
        run.id, 1, "builder", model="m", input_tokens=0, output_tokens=0, cached_tokens=0, usd=0
    )
    usage = views.run_usage(store, run)
    assert usage.usage_status == usage.spend_status == "complete"
    assert usage.known_spend_usd == 0


def test_partial_child_usage_keeps_priced_lower_bound_without_double_count(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    for name, complete, cost in [("parent", True, 2), ("child", False, None)]:
        store.runtime.start_invocation(name, run.id, 1, name, {"cost_step": name})
        store.runtime.observe(
            name,
            1,
            {
                "usage": {"input_tokens": 10},
                "usage_complete": complete,
                "estimate": {
                    "complete": complete,
                    "usd": cost,
                    "known_usd": 2 if complete else 0.5,
                },
            },
        )
        store.record_cost(
            run.id, 1, name, model="m", input_tokens=10, output_tokens=2, cached_tokens=3, usd=cost
        )
    usage = views.run_usage(store, run)
    assert (usage.tokens_in, usage.tokens_out, usage.tokens_cached) == (20, 4, 6)
    assert usage.known_spend_usd == 2.5
    assert usage.usage_status == usage.spend_status == "partial"


def test_recorded_custom_sandbox_and_historical_use(tmp_path: Path) -> None:
    from factory.machine import State

    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="SYN-1", project="renamed", team="SYN")
    store.start_attempt(
        run.id,
        run.attempt,
        State.APPROVED,
        sandbox="factory-build-custom-2",
        artifact_dir="/fixture",
    )
    rows = views.runtimes(
        [{"name": "factory-build-custom-2"}, {"name": "factory-build-renamed"}], store
    )
    assert rows[0].runs_using == ["SYN-1"]
    assert rows[1].runs_using == []
    store.runtime.db.execute("UPDATE attempts SET ended_at=1 WHERE run_id=?", (run.id,))
    row = views.runtimes([{"name": "factory-build-custom-2"}], store)[0]
    assert row.runs_using == []
    assert row.associations[0].source == "attempt"


def test_structured_inventory_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from factory import cli

    def response(output: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["sbx"], 0, output, "")

    for output, status in [
        ("[]", "available"),
        ('{"sandboxes": []}', "available"),
        ("{}", "invalid"),
        ('{"sandboxes": null}', "invalid"),
        ("invalid", "invalid"),
    ]:
        monkeypatch.setattr(
            cli.subprocess, "run", lambda *args, output=output, **kwargs: response(output)
        )
        assert cli._sbx_inventory().status == status

    def timeout(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(["sbx"], 30)

    monkeypatch.setattr(cli.subprocess, "run", timeout)
    assert cli._sbx_inventory().status == "timeout"

    def missing(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("sbx")

    monkeypatch.setattr(cli.subprocess, "run", missing)
    assert cli._sbx_inventory().status == "missing"


def test_child_and_certification_associations_are_retained_without_certifying_name(
    tmp_path: Path,
) -> None:
    import json

    from factory.runtime_jobs import RuntimeJobs

    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    store.runtime.start_invocation("child", run.id, 1, "child:1", {"parent_id": "parent"})
    store.runtime.db.execute(
        "INSERT INTO agent_leases(invocation_id,run_id,project,status) VALUES (?,?,?,'active')",
        ("child", run.id, run.project),
    )
    store.intend_effect(run.id, 1, "child", "agent-launch", "spawn")
    store.runtime.db.execute(
        "UPDATE effects SET external_id=? WHERE run_id=?",
        (json.dumps({"handle": {"sandbox": "factory-review-child-unique"}}), run.id),
    )
    job = RuntimeJobs(store).request_certification(
        run.id, {"sandbox": "factory-build-certified", "generation": "old"}
    )
    store.runtime.db.execute(
        "UPDATE runtime_certifications SET status='passed',lease_until=1 WHERE id=?", (job["id"],)
    )
    child, certified = views.runtimes(
        [
            {"name": "factory-review-child-unique"},
            {"name": "factory-build-certified", "status": "running"},
        ],
        store,
    )
    assert child.runs_using == ["SYN-1"]
    assert child.associations[0].reference == "child"
    assert certified.recorded_compatibility[0]["identity"]["generation"] == "old"
    assert certified.recorded_compatibility[0]["fingerprint"] == job["fingerprint"]
    assert certified.runs_using == []
    assert certified.associations[0].source == "certification passed"
    assert certified.compatibility == "Unavailable — current runtime identity unobserved"
    assert "expired" not in certified.compatibility


def test_failed_inventory_does_not_expose_command_output(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from factory import cli

    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["sbx"], 1, "", "credential-like private response"
        ),
    )
    result = cli._sbx_inventory()
    assert result.status == "failed"
    assert result.reason == "sbx inventory command failed"
    assert cli._sbx_ls_json() == []


def test_fresh_context_counts_only_active_invocations_and_not_cumulative_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    monkeypatch.setattr(views.time, "time", lambda: 1000)
    for name, lease, observed in [
        ("builder", "active", 990),
        ("child", "active", 999),
        ("stale", "active", 500),
        ("dead", "completed", 999),
    ]:
        store.runtime.start_invocation(name, run.id, 1, name, {})
        store.runtime.observe(
            name,
            1,
            {
                "usage": {"input_tokens": 999999},
                "context": {"tokens": 50, "effective_window": 100, "observed_at": observed},
            },
        )
        store.runtime.db.execute(
            "INSERT INTO agent_leases(invocation_id,run_id,project,status) VALUES (?,?,?,?)",
            (name, run.id, run.project, lease),
        )
    assert views._invocation_context_counts(store, run) == (3, 2)


def test_title_comes_only_from_persisted_ticket_heading(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    assert views._ticket_title(tmp_path, run) is None
    context = tmp_path / "state" / "runs" / run.id / "context"
    context.mkdir(parents=True)
    (context / "ticket.md").write_text("# SYN-1 — Persisted title\n\nDescription")
    assert views._ticket_title(tmp_path, run) == "Persisted title"
    (context / "ticket.md").write_text("# OTHER-1 — Wrong ticket")
    assert views._ticket_title(tmp_path, run) is None


def test_layout_uses_recorded_spec_with_historical_scope(tmp_path: Path) -> None:
    import json

    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="SYN-1", project="synthetic", team="SYN")
    owner = (run.id, 1, "parent", "delegation-mailbox", "mounts")
    store.intend_effect(*owner)
    store.confirm_effect(
        *owner, json.dumps({"spec": {"name": "factory-build-recorded", "clone": True}})
    )
    row = views.runtimes([{"name": "factory-build-recorded"}], store)[0]
    assert row.layout == "clone · recorded SYN-1 attempt 1"
