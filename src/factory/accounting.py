"""Record every scheduled model invocation before spawn and reconcile retained usage."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factory.agent import stream
from factory.machine import Blocked

if TYPE_CHECKING:
    from factory.routing import Role
    from factory.steps import Context
    from factory.store import Store


def key(ctx: Context, attempt: int, role: str, *, launch: int | None = None) -> str:
    group = "review" if role.startswith("review:") else role
    if launch is None:
        launch = ctx.store.runtime.settings("run", ctx.run.id).get(f"launch:{attempt}:{group}", 1)
    suffix = f":launch-{launch}" if launch > 1 else ""
    return f"{ctx.run.id}:{attempt}:{role}{suffix}"


def begin(
    ctx: Context,
    attempt: int,
    role: Role,
    step: str,
    events: Path,
    *,
    semantic_role: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> str:
    invocation_id = key(ctx, attempt, step)
    old = ctx.store.runtime.invocation(invocation_id)
    if old:
        return invocation_id
    known = ctx.store.known_spend(ctx.run.id)
    if known >= ctx.routing.usd_per_run:
        raise Blocked("budget-exceeded", f"API-equivalent estimate at least ${known:.2f}")
    metadata: dict[str, Any] = {
        **(extra_metadata or {}),
        "semantic_role": semantic_role or role.name,
        "model": role.model,
        "effort": role.effort,
        "preset": role.preset,
        "events": str(events),
        "cost_step": invocation_id.removeprefix(f"{ctx.run.id}:{attempt}:"),
        "policy_revision": (ctx.store.runtime.policy(ctx.run.id) or {}).get("revision"),
    }
    ctx.store.runtime.start_invocation(invocation_id, ctx.run.id, attempt, step, metadata)
    ctx.store.reconcile_cost(
        ctx.run.id,
        attempt,
        metadata["cost_step"],
        model=role.model,
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        usd=None,
    )
    return invocation_id


def collect(
    ctx: Context, attempt: int, step: str, events: Path, *, invocation_id: str | None = None
) -> None:
    """Legacy aggregate usage cannot establish request-level tier or context pricing."""
    invocation_id = invocation_id or key(ctx, attempt, step)
    collect_invocation(ctx.store, invocation_id, events)


def collect_invocation(store: Store, invocation_id: str, events: Path) -> None:
    """Reconcile retained usage without loading workflow, routing or tracker dependencies.

    The cost is the run's own `total_cost_usd`: the CLI prices every model it used at
    list price, which is notional under a subscription and billed under an API key, and
    either way it is the number the budget ceiling is measured in. A stream with no
    `result` event yet has usage but no price, and stays an incomplete estimate.
    """
    invocation = store.runtime.invocation(invocation_id)
    if invocation is None or not events.exists():
        return
    text = events.read_text(errors="replace")
    sequence = sum(1 for line in text.splitlines() if line.strip())
    run = stream.parse(events)
    priced = run.by_model != {}
    usage = _total_usage(run)
    payload: dict[str, Any] = {
        "evidence_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "session_id": run.session,
        "usage": asdict(usage),
        "by_model": {
            model: {"usage": asdict(each.usage), "notional_usd": each.notional_usd}
            for model, each in run.by_model.items()
        },
        "context": {"tokens": run.context_tokens, "effective_window": _window(run)},
        "rate_limit": asdict(run.rate_limit) if run.rate_limit else None,
        "estimate": {
            "complete": priced,
            "usd": run.notional_usd if priced else None,
            "reason": None if priced else "the stream has no result event yet",
        },
    }
    store.runtime.observe(invocation_id, sequence, payload)
    # Reconcile even a duplicate observation: a process may have died after the
    # telemetry commit and before its cost update. Never regress to an older payload.
    retained = store.runtime.invocation(invocation_id)
    if retained is None or retained["sequence"] > sequence:
        return
    payload = retained["telemetry"]
    usage = stream.Usage(**payload["usage"])
    store.reconcile_cost(
        invocation["run_id"],
        invocation["attempt"],
        invocation["metadata"].get("cost_step", invocation["role"]),
        model=invocation["metadata"]["model"],
        input_tokens=usage.input,
        output_tokens=usage.output,
        cached_tokens=usage.cache_read,
        usd=payload["estimate"]["usd"],
    )


def _total_usage(run: stream.Run) -> stream.Usage:
    total = stream.Usage(input=0, cache_read=0, cache_write=0, output=0, thinking=0)
    for each in run.by_model.values():
        used = each.usage
        total = stream.Usage(
            input=total.input + used.input,
            cache_read=total.cache_read + used.cache_read,
            cache_write=total.cache_write + used.cache_write,
            output=total.output + used.output,
            thinking=total.thinking + used.thinking,
        )
    return total


def _window(run: stream.Run) -> int | None:
    """The context window the run's own model reported, or None before a result event."""
    if run.init is None:
        return None
    reported = run.by_model.get(run.init.model)
    return reported.context_window if reported else None


def collect_active(ctx: Context) -> None:
    """Observation is allowed while approval mode prevents another agent launch."""
    for invocation in ctx.store.runtime.invocations(ctx.run.id):
        collect(
            ctx,
            invocation["attempt"],
            invocation["role"],
            Path(invocation["metadata"]["events"]),
            invocation_id=invocation["id"],
        )
