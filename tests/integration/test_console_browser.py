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
from factory.machine import State
from factory.steps import Context
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
    if scenario == "approval":
        ctx.store.runtime.start_invocation(
            "fixture-approval", ctx.run.id, 1, "implement", {"model": "fixture"}
        )
        ctx.store.runtime.configure("run", ctx.run.id, {"waiting_invocation": "fixture-approval"})
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
        else [{"name": "factory-build-python-harness", "state": "running", "ports": []}]
    )
    monkeypatch.setattr("factory.cli._sbx_ls_json", lambda: runtimes)
    app = create_app(
        ctx.home,
        registry=ctx.registry,
        routing=ctx.routing,
        store=ctx.store,
        linear=ctx.linear,
        context_factory=lambda run: replace(ctx, run=run),
    )
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
