"""Model selection and the operator's permission for the next agent attempt."""

from __future__ import annotations

from typing import TYPE_CHECKING

from factory.machine import Blocked
from factory.routing import Role

if TYPE_CHECKING:
    from factory.steps import Context

PRESETS = {
    "volume": {
        "planner": ("gpt-5.6-sol", "high"),
        "test_designer": ("gpt-5.6-sol", "high"),
        "builder": ("gpt-5.6-terra", "medium"),
        "reviewer": ("gpt-5.6-sol", "high"),
        "diagnoser": ("gpt-5.6-sol", "high"),
        "synthesiser": ("gpt-5.6-luna", "medium"),
        "documenter": ("gpt-5.6-luna", "medium"),
    },
    "high-confidence": {
        "planner": ("gpt-6-astra", "high"),
        "test_designer": ("gpt-6-astra", "high"),
        "builder": ("gpt-6-astra", "high"),
        "reviewer": ("gpt-5.6-sol", "xhigh"),
        "diagnoser": ("gpt-6-astra", "xhigh"),
        "synthesiser": ("gpt-5.6-luna", "medium"),
        "documenter": ("gpt-5.6-luna", "medium"),
    },
}


def role_for(ctx: Context, name: str) -> Role:
    preset = ctx.store.runtime.effective(ctx.project.name, ctx.run.id).get(
        "model_preset", "existing"
    )
    if preset == "existing":
        return ctx.routing.role("planner" if name in {"diagnoser", "test_designer"} else name)
    from factory.agent.app_server import AppServerAdapter

    if preset not in PRESETS:
        raise Blocked("model-preset-unknown", preset)
    model, effort = PRESETS[preset][name]
    role = Role(name, model, effort, preset=preset)
    if not isinstance(ctx.agent, AppServerAdapter):
        from factory.agent import model_probe

        if name == "reviewer":
            from factory.steps.review import _review_scratch, _sandbox_run_dir

            directory = _sandbox_run_dir(_review_scratch(ctx), ctx.run.id)
            sandbox = ctx.project.review_sandbox
        else:
            directory = ctx.factory_dir
            sandbox = ctx.project.build_sandbox
        evidence = model_probe.validate(ctx.sandbox, sandbox, directory, role, env=ctx.env)
        ctx.store.record_check(
            ctx.run.id,
            ctx.run.attempt,
            f"runtime-model:{name}",
            "pass",
            detail=f"{model} / {effort}",
            artifact=str(evidence),
        )
    return role


def attempt_key(attempt: int, step: str, launch: int = 1) -> str:
    return f"{attempt}:{step}:{launch}"


def guard(ctx: Context, attempt: int, step: str) -> str:
    settings = ctx.store.runtime.effective(ctx.project.name, ctx.run.id)
    previous = settings.get(f"launch:{attempt}:{step}", 0)
    wanted = attempt_key(attempt, step, previous + 1)
    if (
        settings.get("mode", "automatic") == "approval"
        and settings.get("approved_invocation") != wanted
    ):
        raise AgentApprovalRequired(wanted)
    limit = settings.get("concurrency") or ctx.registry.concurrency_for(ctx.project)
    if not ctx.store.runtime.admit(ctx.run.id, ctx.project.name, limit):
        raise ProjectQueued("waiting for a project slot")
    if ctx.store.known_spend(ctx.run.id) >= ctx.routing.usd_per_run:
        raise Blocked(
            "budget-exceeded", "API-equivalent estimated spend reached the configured ceiling"
        )
    if attempt > ctx.registry.defaults.max_total_attempts:
        raise Blocked("attempts-exhausted", "The lifetime attempt limit is exhausted")
    ctx.store.runtime.configure("run", ctx.run.id, {f"launch:{attempt}:{step}": previous + 1})
    ctx.store.runtime.audit("run", ctx.run.id, "agent-launch", {"invocation": wanted})
    return wanted


class AgentApprovalRequired(Exception):
    """Pause agent scheduling without blocking deterministic observation or verification."""


class ProjectQueued(Exception):
    """No project slot is available. Keep the ticket queued without a tracker write."""
