"""Opt-in real Chrome measurement against isolated integration fixtures.

Set FACTORY_BROWSER_MODULES to a directory containing playwright and @axe-core/playwright,
and FACTORY_BROWSER_OUTPUT to retain screenshots and JSON measurements. No real sandbox,
tracker, vault, operator home or factory database is opened.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
import uvicorn

from factory.console.app import create_app
from factory.console.views import InventoryResult, run_detail
from factory.machine import State
from factory.runtime_jobs import RuntimeJobs
from factory.steps import Context
from factory.store import Store
from tests.integration.test_console import _models_toml, _to_implementing

MODULES = os.environ.get("FACTORY_BROWSER_MODULES")


@pytest.mark.skipif(not MODULES, reason="opt-in Chrome measurement: FACTORY_BROWSER_MODULES unset")
@pytest.mark.parametrize("scenario", ["populated", "empty", "blocked", "approval", "unavailable"])
def test_console_in_real_browser(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario: str
) -> None:
    _models_toml(ctx)
    if scenario != "unavailable":
        _to_implementing(ctx)
    if scenario == "populated":
        _populate_history(ctx)
    if scenario == "approval":
        ctx.store.runtime.start_invocation(
            "fixture-approval", ctx.run.id, 1, "implement", {"model": "fixture"}
        )
        ctx.store.runtime.configure(
            "run", ctx.run.id, {"mode": "approval", "waiting_invocation": "fixture-approval"}
        )
    target = {
        "empty": State.CANCELLED,
        "blocked": State.BLOCKED,
    }.get(scenario)
    if target:
        ctx.store.update_run(ctx.run.id, blocked_reason="fixture: operator decision required")
        ctx.store.record_transition(
            ctx.run.id, from_state=ctx.state, to_state=target, actor="human", rule="fixture"
        )
        ctx.refresh()
    runtimes = (
        []
        if scenario in ("empty", "unavailable")
        else [
            {
                "name": "factory-build-python-harness",
                "status": "running",
                "workspaces": ["/fixture/project", "/fixture/long-workspace-" + "abcdef" * 12],
                "agent": "codex",
                "id": "fixture-runtime",
            }
        ]
    )
    monkeypatch.setattr("factory.cli._sbx_ls_json", lambda: runtimes)
    monkeypatch.setattr(
        "factory.cli._sbx_inventory",
        lambda: (
            InventoryResult([], "timeout", "Fixture inventory timed out")
            if scenario == "unavailable"
            else InventoryResult(runtimes)
        ),
    )
    monkeypatch.setattr("factory.console.app.SSE_INTERVAL_SECONDS", 0.2)
    app = create_app(
        ctx.home,
        registry=ctx.registry,
        routing=ctx.routing,
        store=ctx.store,
        linear=ctx.linear,
        context_factory=lambda run: replace(ctx, run=run),
    )

    @app.post("/__fixture/update/{sequence}")
    def update_fixture(sequence: int) -> dict[str, int]:
        # Test-only route on the isolated app: exercise real Store -> SSE rendering.
        store = Store(ctx.store.path)
        try:
            store.update_run(ctx.run.id, branch=f"fix/fixture-stream-{sequence}")
            detail = run_detail(ctx.home, ctx.registry, ctx.routing, store, ctx.run)
            if detail.events_path:
                with Path(detail.events_path).open("a") as stream:
                    stream.write(f"fixture appended event {sequence}\n")
        finally:
            store.close()
        return {"sequence": sequence}

    output = Path(os.environ.get("FACTORY_BROWSER_OUTPUT", str(tmp_path / "browser")))
    output.mkdir(parents=True, exist_ok=True)
    # Bind before starting the thread, avoiding a free-port discovery race.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started, "isolated console server did not start"
            script = Path(__file__).parents[1] / "browser" / "console.mjs"
            result = subprocess.run(
                [
                    "node",
                    str(script),
                    f"http://127.0.0.1:{listener.getsockname()[1]}",
                    scenario,
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=240,
                check=False,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            report = json.loads((output / f"{scenario}.json").read_text())
            assert len(report["pages"]) == 14
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "isolated console server did not shut down"


def _populate_history(ctx: Context) -> None:
    """Sanitized long identities and retained histories, not production data."""
    projects = dict(ctx.registry.projects)
    for index in range(1, 3):
        name = f"fixture-project-{index}-with-a-long-registry-identity"
        projects[name] = replace(ctx.project, name=name, team=f"FX{index}")
    ctx.registry = replace(ctx.registry, projects=projects)
    names = list(projects)
    for index in range(9):
        run = ctx.store.insert_run(
            linear_id=f"FIXTURE-{index + 10}",
            project=names[index % len(names)],
            team="FIXTURE",
            state=[State.IMPLEMENTING, State.BLOCKED, State.AWAITING_HUMAN][index % 3],
        )
        ctx.store.update_run(run.id, branch="fix/" + "long-unbroken-identity-" * 5)
    ctx.store.record_cost(
        ctx.run.id,
        1,
        "implement",
        model="fixture-model",
        input_tokens=24000,
        output_tokens=1600,
        cached_tokens=12000,
        usd=0.42,
    )
    jobs = RuntimeJobs(ctx.store)
    for index in range(55):
        invocation = f"fixture-invocation-{index:03}-" + "abcdef0123456789" * 4
        ctx.store.runtime.start_invocation(
            invocation,
            ctx.run.id,
            index + 1,
            "implement" if index == 0 else "child",
            {"model": "fixture-model", "sandbox": f"factory-review-fixture-{index}"},
        )
        if index == 0:
            ctx.store.runtime.observe(
                invocation,
                1,
                {
                    "context": {
                        "tokens": 46000,
                        "effective_window": 100000,
                        "observed_at": time.time(),
                    },
                    "usage": {
                        "input_tokens": 24000,
                        "output_tokens": 1600,
                        "cached_input_tokens": 12000,
                    },
                    "usage_complete": False,
                    "estimate": {"known_usd": 0.05, "complete": False},
                },
            )
            ctx.store.runtime.db.execute(
                "INSERT INTO agent_leases(invocation_id,run_id,project,status,parent_id) VALUES (?,?,?,?,?)",
                (invocation, ctx.run.id, ctx.project.name, "active", None),
            )
        if index:
            ctx.store.runtime.db.execute(
                "INSERT INTO agent_leases(invocation_id,run_id,project,status,parent_id) VALUES (?,?,?,?,?)",
                (
                    invocation,
                    ctx.run.id,
                    ctx.project.name,
                    "completed",
                    "fixture-invocation-000-" + "abcdef0123456789" * 4,
                ),
            )
            ctx.store.runtime.db.execute(
                "INSERT INTO delegation_requests(id,parent_id,call_id,run_id,request,status,child_id) VALUES (?,?,?,?,?,?,?)",
                (
                    f"fixture-request-{index}-" + "a" * 64,
                    "fixture-invocation-000-" + "abcdef0123456789" * 4,
                    f"call-{index}",
                    ctx.run.id,
                    "{}",
                    "completed",
                    invocation,
                ),
            )
        if index < 12:
            jobs.request_certification(
                ctx.run.id,
                {"sandbox": f"factory-certification-fixture-{index}", "revision": "a" * 64},
            )
    detail = run_detail(ctx.home, ctx.registry, ctx.routing, ctx.store, ctx.run)
    assert detail.events_path
    context_event = json.dumps(
        {
            "type": "factory.context",
            "tokens": 46000,
            "window": 100000,
            "observed_at": time.time(),
            "semantics_verified": True,
        }
    )
    Path(detail.events_path).write_text(
        context_event
        + "\n"
        + "".join(f"fixture retained event {index:04}\n" for index in range(250))
    )
