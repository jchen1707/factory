"""Pin an adapter per run and validate its executing sandbox before each launch."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from factory.agent.app_server import AppServerAdapter
from factory.machine import Blocked

if TYPE_CHECKING:
    from factory.steps import Context

# TokenUsageBreakdown requires these counters; cacheWriteInputTokens alone defaults to 0.
_BASELINE_REQUIRED = frozenset(
    {"inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens", "totalTokens"}
)


def select(ctx: Context, *, review: bool = False) -> None:
    settings = ctx.store.runtime.settings("run", ctx.run.id)
    adapter = settings.get("agent_adapter")
    if adapter is None:
        project = ctx.store.runtime.settings("project", ctx.project.name)
        # Existing attempts must remain on their original exec transport.
        adapter = "codex-exec" if ctx.run.attempt else project.get("agent_adapter", "codex-exec")
        retained = {"agent_adapter": adapter}
        if adapter == "app-server":
            retained["app_server_compatibility"] = project.get("app_server_compatibility", "")
        ctx.store.runtime.configure("run", ctx.run.id, retained)
        settings |= retained
    if adapter == "codex-exec":
        return
    if adapter != "app-server":
        raise Blocked("agent-adapter-unknown", str(adapter))
    sandbox = ctx.project.review_sandbox if review else ctx.project.build_sandbox
    version = ctx.sandbox.exec_sync(sandbox, ["codex", "--version"], timeout=30)
    if not version.ok or not version.stdout.strip():
        raise Blocked("runtime-version-unavailable", sandbox)
    directory = Path(settings.get("app_server_compatibility", ""))
    baselines = {}
    for invocation in ctx.store.runtime.invocations(ctx.run.id):
        telemetry = invocation.get("telemetry") or {}
        wire = telemetry.get("thread_total_wire")
        # A failed resume can flush an empty observation before receiving usage. It
        # must not erase earlier counters and turn the next resume's history into usage.
        if (
            telemetry.get("thread_id")
            and isinstance(wire, dict)
            and wire.keys() >= _BASELINE_REQUIRED
            and all(type(value) is int and value >= 0 for value in wire.values())
        ):
            baselines[telemetry["thread_id"]] = wire
    ctx.agent = AppServerAdapter(
        directory / f"{sandbox}.json",
        runtime_version=version.stdout.strip(),
        sandbox=sandbox,
        baselines=baselines,
    )
