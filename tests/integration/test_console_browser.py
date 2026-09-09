"""Opt-in real Chrome measurement against isolated integration fixtures.

Set FACTORY_BROWSER_MODULES to a directory containing playwright and @axe-core/playwright,
and FACTORY_BROWSER_OUTPUT to retain screenshots and JSON measurements. No real sandbox,
tracker, vault, operator home or factory database is opened.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime, tzinfo
from itertools import count
from pathlib import Path

import pytest
import uvicorn

from factory.console import views as console_views
from factory.console.app import create_app
from factory.console.views import InventoryResult, run_detail, run_timeline, runs_board
from factory.machine import State
from factory.operator_controls import status
from factory.runtime_jobs import RuntimeJobs
from factory.steps import Context
from factory.store import Store
from tests.integration.test_console import _models_toml, _to_implementing

MODULES = os.environ.get("FACTORY_BROWSER_MODULES")
FIXTURE_NOW = 1788950760.0


@pytest.fixture(autouse=True)
def deterministic_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze wall time and identities before ctx setup; monotonic server time stays real."""
    sequence = count(1)
    monkeypatch.setattr(time, "time", lambda: FIXTURE_NOW)
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=next(sequence)))
    monkeypatch.setattr("factory.store.new_run_id", lambda: f"fixture{next(sequence):09d}")

    class FixtureDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> FixtureDatetime:
            return cls.fromtimestamp(FIXTURE_NOW, UTC)

    monkeypatch.setattr("factory.console.assets.datetime", FixtureDatetime)


@pytest.mark.skipif(not MODULES, reason="opt-in Chrome measurement: FACTORY_BROWSER_MODULES unset")
@pytest.mark.parametrize(
    "scenario", ["populated", "stress", "empty", "blocked", "approval", "unavailable"]
)
@pytest.mark.parametrize("viewport", ["desktop", "narrow"])
def test_console_in_real_browser(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario: str, viewport: str
) -> None:
    _models_toml(ctx)
    if scenario != "unavailable":
        _to_implementing(ctx)
    if scenario in ("populated", "stress"):
        _populate_history(ctx, stress=scenario == "stress")
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
                "workspaces": ["/fixture/project"]
                + (["/fixture/long-workspace-" + "abcdef" * 12] if scenario == "stress" else []),
                "agent": "codex",
                "id": "fixture-runtime",
            }
        ]
    )
    if scenario in ("populated", "stress"):
        runtimes.extend(
            [
                {
                    "name": "factory-review-fixture-certification-0",
                    "status": "stopped",
                    "workspaces": [],
                    "agent": "codex",
                },
                {
                    "name": "factory-review-fixture-certification-1",
                    "status": "running",
                    "workspaces": ["/fixture/review"],
                    "agent": "claude",
                },
                {
                    "name": "codex-james",
                    "status": "running",
                    "workspaces": ["/fixture/operator"],
                    "agent": "codex",
                },
            ]
        )
    for heartbeat in ctx.home.rglob("heartbeat"):
        os.utime(heartbeat, (FIXTURE_NOW - 8, FIXTURE_NOW - 8))
    if ctx.run.worktree:
        for heartbeat in Path(ctx.run.worktree).rglob("heartbeat"):
            os.utime(heartbeat, (FIXTURE_NOW - 8, FIXTURE_NOW - 8))
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
    manifest = {
        "schema": 2,
        "source": {
            "commit": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).parents[2],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip(),
            "tracked_diff_sha256": hashlib.sha256(
                subprocess.run(
                    [
                        "git",
                        "diff",
                        "HEAD",
                        "--",
                        "src",
                        "tests/browser",
                        "tests/integration/test_console_browser.py",
                    ],
                    cwd=Path(__file__).parents[2],
                    capture_output=True,
                    check=True,
                ).stdout
            ).hexdigest(),
        },
        "scenario": scenario,
        "viewport": viewport,
        "clock": FIXTURE_NOW,
        "isolation": "Fresh ctx/database/app per viewport; captures precede all mutation tests",
        "runs": [asdict(row) for row in runs_board(ctx.home, ctx.registry, ctx.routing, ctx.store)],
        "detail": asdict(run_detail(ctx.home, ctx.registry, ctx.routing, ctx.store, ctx.run)),
        "timeline": asdict(run_timeline(ctx.home, ctx.registry, ctx.routing, ctx.store, ctx.run)),
        "roles": {name: asdict(role) for name, role in ctx.routing.roles.items()},
        "runtimes": [
            asdict(row)
            for row in console_views.runtimes(runtimes, ctx.store, registry=ctx.registry)
        ],
        "projects": [
            {
                "name": project.name,
                "concurrency": ctx.registry.concurrency_for(project),
                "occupied_slots": ctx.store.runtime.db.execute(
                    "SELECT COUNT(*) FROM project_slots WHERE project=?", (project.name,)
                ).fetchone()[0],
                "active_agents": ctx.store.runtime.db.execute(
                    "SELECT COUNT(*) FROM agent_leases WHERE project=? AND status='active'",
                    (project.name,),
                ).fetchone()[0],
                "waiting": f"{status(ctx.store, project.name)['queued_children']} children · {status(ctx.store, project.name)['pending_certifications']} certifications",
                "effective_agent_limit": status(ctx.store, project.name)["effective"][
                    "max_active_agents"
                ],
                "settings": ctx.store.runtime.settings("project", project.name),
            }
            for project in ctx.registry.projects.values()
        ],
        "settings": ctx.store.runtime.effective(ctx.run.project, ctx.run.id),
        "invocations": ctx.store.runtime.invocations(ctx.run.id),
        "config": asdict(console_views.config_view(ctx.routing, ctx.registry)),
    }
    # Retained artifacts contain only synthetic data; normalize temporary host paths.
    manifest_json = json.dumps(manifest, default=str, indent=2).replace(str(tmp_path), "/fixture")
    (output / f"{scenario}-{viewport}-fixture.json").write_text(manifest_json + "\n")
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
                    viewport,
                ],
                capture_output=True,
                text=True,
                timeout=240,
                check=False,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            report = json.loads((output / f"{scenario}-{viewport}.json").read_text())
            assert len(report["pages"]) == 7
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "isolated console server did not shut down"


def _populate_history(ctx: Context, *, stress: bool = False) -> None:
    """Sanitized long identities and retained histories, not production data."""
    projects = dict(ctx.registry.projects)
    for index in range(1, 3):
        name = (
            f"fixture-project-{index}-with-a-long-registry-identity"
            if stress
            else ["frontend-harness", "nemoclaw-dev"][index - 1]
        )
        projects[name] = replace(ctx.project, name=name, team=f"FX{index}")
    ctx.registry = replace(ctx.registry, projects=projects)
    names = list(projects)
    for index in range(9 if stress else 2):
        run = ctx.store.insert_run(
            linear_id=f"FIXTURE-{index + 10}",
            project=names[index % len(names)],
            team="FIXTURE",
            state=[State.BLOCKED, State.AWAITING_HUMAN, State.IMPLEMENTING][index % 3],
        )
        ctx.store.update_run(
            run.id,
            branch="fix/" + ("long-unbroken-identity-" * 5 if stress else "fixture-change"),
            blocked_reason="Gate report incomplete" if index % 3 == 0 else None,
        )
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
    for index in range(55 if stress else 3):
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
            job = jobs.request_certification(
                ctx.run.id,
                {"sandbox": f"factory-review-fixture-certification-{index}", "revision": "a" * 64},
            )
            if index < 2:
                ctx.store.runtime.db.execute(
                    "UPDATE runtime_certifications SET status=?,evidence=?,failure=? WHERE id=?",
                    (
                        "completed" if index == 0 else "failed",
                        json.dumps(
                            {
                                "compatible": index == 0,
                                "probe": "fixture",
                                "identity": job["identity"],
                            }
                        ),
                        None if index == 0 else "hook probe incomplete",
                        job["id"],
                    ),
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

    _populate_evidence(ctx)


def _populate_evidence(ctx: Context) -> None:
    """Actual persisted evidence shapes, with explicit missing/timed tool observations."""
    detail = run_detail(ctx.home, ctx.registry, ctx.routing, ctx.store, ctx.run)
    assert detail.events_path
    attempt = Path(detail.events_path).parent
    events = [
        {"type": "item.started", "item": {"id": "fixture-read", "type": "command_execution"}},
        {
            "type": "item.completed",
            "item": {
                "id": "fixture-read",
                "type": "command_execution",
                "command": "cat src/app/settings.py",
                "exit_code": 0,
            },
        },
        {"type": "item.started", "item": {"id": "fixture-test", "type": "command_execution"}},
        {
            "type": "item.completed",
            "item": {
                "id": "fixture-test",
                "type": "command_execution",
                "command": "pytest tests/test_settings.py",
                "exit_code": 0,
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "fixture-untimed",
                "type": "command_execution",
                "command": "git diff --stat",
                "exit_code": 0,
            },
        },
    ]
    old_lines = Path(detail.events_path).read_text().splitlines()
    Path(detail.events_path).write_text(
        "\n".join([*old_lines, *(json.dumps(event) for event in events)]) + "\n"
    )
    timings = [FIXTURE_NOW - 8] * len(old_lines) + [
        FIXTURE_NOW - 7,
        FIXTURE_NOW - 6.8,
        FIXTURE_NOW - 6,
        FIXTURE_NOW - 2,
        FIXTURE_NOW - 1,
    ]
    (attempt / "events.timings.jsonl").write_text(
        "\n".join(json.dumps({"observed_at": t}) for t in timings) + "\n"
    )
    (attempt / "gates.json").write_text(
        json.dumps(
            {
                "verdict": "pass",
                "gates": [
                    {"name": "lint", "status": "pass", "caveat": None},
                    {
                        "name": "unit tests",
                        "status": "pass",
                        "caveat": "Sandbox integration checks are separate evidence",
                    },
                ],
            }
        )
    )
    (attempt / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "name": "source-snapshot.json",
                        "path": "source-snapshot.json",
                        "sha256": "7bf2d01" + "a" * 57,
                    }
                ]
            }
        )
    )
    (attempt / "source-snapshot.json").write_text('{"fixture": true}\n')
    review = ctx.home / "state" / "runs" / ctx.run.id / "review"
    review.mkdir(parents=True, exist_ok=True)
    (review / "review-summary.json").write_text(
        json.dumps(
            {
                "tier2": "ran:forced",
                "findings": [
                    {
                        "severity": "low",
                        "file": "src/app/settings.py",
                        "line": 14,
                        "summary": "Clarify the default timeout label",
                    },
                    {
                        "severity": "high",
                        "file": "src/app/settings.py",
                        "line": 28,
                        "summary": "Preserve source when recovery is interrupted",
                    },
                ],
            }
        )
    )
    # Real transition rows, measured durations rather than an invented CSS waterfall.
    transitions = ctx.store.transitions(ctx.run.id)
    start = FIXTURE_NOW - 300
    ctx.store.runtime.db.execute("UPDATE runs SET created_at=? WHERE id=?", (start, ctx.run.id))
    for index, transition in enumerate(transitions):
        ctx.store.runtime.db.execute(
            "UPDATE transitions SET at=? WHERE rowid=?",
            (start + (index + 1) * 20, transition["id"]),
        )
    ctx.refresh()
