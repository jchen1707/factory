"""Model selection and the operator's permission for the next agent attempt."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from factory import accounting
from factory.machine import Blocked
from factory.routing import Role

if TYPE_CHECKING:
    from factory.steps import Context

PRESETS = {
    "volume": {
        "planner": ("claude-opus-5-5", "high"),
        "test_designer": ("claude-opus-5-5", "high"),
        "builder": ("claude-sonnet-5-5", "medium"),
        "reviewer": ("claude-opus-5-5", "high"),
        "diagnoser": ("claude-opus-5-5", "high"),
        "synthesiser": ("claude-sonnet-5-5", "medium"),
        "documenter": ("claude-sonnet-5-5", "medium"),
    },
    "high-confidence": {
        "planner": ("claude-fable-5-1", "high"),
        "test_designer": ("claude-fable-5-1", "high"),
        "builder": ("claude-opus-5-5", "high"),
        "reviewer": ("claude-fable-5-1", "xhigh"),
        "diagnoser": ("claude-fable-5-1", "xhigh"),
        "synthesiser": ("claude-sonnet-5-5", "medium"),
        "documenter": ("claude-sonnet-5-5", "medium"),
    },
}


def role_for(ctx: Context, name: str) -> Role:
    preset = ctx.store.runtime.effective(ctx.project.name, ctx.run.id).get(
        "model_preset", "existing"
    )
    if preset == "existing":
        return ctx.routing.role("planner" if name in {"diagnoser", "test_designer"} else name)
    if preset not in PRESETS:
        raise Blocked("model-preset-unknown", preset)
    model, effort = PRESETS[preset][name]
    return Role(name, model, effort, preset=preset, max_turns=ctx.routing.max_turns)


def attempt_key(attempt: int, step: str, launch: int = 1) -> str:
    return f"{attempt}:{step}:{launch}"


def guard(ctx: Context, attempt: int, step: str, *, invocation_role: str | None = None) -> str:
    from factory import workflow_launches
    from factory.runtime_jobs import RuntimeJobs

    workflow_launches.reconcile(ctx)
    if any(
        row["run_id"] == ctx.run.id
        for row in RuntimeJobs(ctx.store).active_agents(ctx.project.name)
    ):
        raise ProjectQueued("waiting for prior agent terminal reconciliation")
    now = time.time()
    held = rate_limit_hold(accounting.limits(ctx.store, now), ctx.routing.hold_at, now)
    if held is not None:
        raise ProjectQueued(held)
    settings = ctx.store.runtime.effective(ctx.project.name, ctx.run.id)
    previous = settings.get(f"launch:{attempt}:{step}", 0)
    wanted = attempt_key(attempt, step, previous + 1)
    exact = wanted
    if invocation_role is not None:
        from factory.accounting import key

        exact = key(ctx, attempt, invocation_role, launch=previous + 1)
    if settings.get("mode", "automatic") == "approval" and settings.get(
        "approved_invocation"
    ) not in {wanted, exact}:
        ctx.store.runtime.configure("run", ctx.run.id, {"waiting_invocation": exact})
        raise AgentApprovalRequired(exact)
    limit = settings.get("concurrency") or ctx.registry.concurrency_for(ctx.project)
    if not ctx.store.runtime.admit(ctx.run.id, ctx.project.name, limit):
        raise ProjectQueued("waiting for a project slot")
    if ctx.store.known_spend(ctx.run.id) >= ctx.routing.usd_per_run:
        raise Blocked(
            "budget-exceeded", "API-equivalent estimated spend reached the configured ceiling"
        )
    if attempt > ctx.registry.defaults.max_total_attempts:
        raise Blocked("attempts-exhausted", "The lifetime attempt limit is exhausted")
    if invocation_role is not None and settings.get("approved_invocation") == wanted:
        ctx.store.runtime.configure("run", ctx.run.id, {"approved_invocation": exact})
    ctx.store.runtime.configure("run", ctx.run.id, {f"launch:{attempt}:{step}": previous + 1})
    ctx.store.runtime.audit("run", ctx.run.id, "agent-launch", {"invocation": wanted})
    return wanted


def rate_limit_hold(
    limits: accounting.Limits, hold_at: Mapping[str, float], now: float
) -> str | None:
    """Why the subscription should take no new launch yet, or `None`.

    A report is only as current as its reset: once a window's `resets_at` passes, its
    utilisation describes a window that no longer exists, so the hold lifts without a
    newer report. That matters because a hold starts no launch that could bring one.
    """
    for name, threshold in hold_at.items():
        window = limits.windows.get(name)
        if window is not None and window.utilization >= threshold and window.resets_at > now:
            return (
                f"subscription {name} window at {window.utilization:.0%} "
                f"(hold at {threshold:.0%}) until {_utc(window.resets_at)}"
            )
    if limits.refused_until is not None and limits.refused_until > now:
        return f"subscription refused a launch (429) until {_utc(limits.refused_until)}"
    return None


def _utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M UTC")


class AgentApprovalRequired(Exception):
    """Pause agent scheduling without blocking deterministic observation or verification."""


class ProjectQueued(Exception):
    """No project slot is available. Keep the ticket queued without a tracker write."""
