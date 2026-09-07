"""Record every scheduled model invocation before spawn and reconcile retained usage."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factory.agent.base import Usage
from factory.agent.codex import parse_events
from factory.agent.telemetry import CurrentContext, ThreadTelemetry
from factory.machine import Blocked
from factory.pricing import PriceBook, RequestUsage

if TYPE_CHECKING:
    from factory.routing import Role
    from factory.steps import Context


def key(ctx: Context, attempt: int, role: str) -> str:
    group = "review" if role.startswith("review:") else role
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
        "adapter": "app-server" if hasattr(ctx.agent, "report") else "codex-exec",
        "runtime_compatibility": getattr(ctx.agent, "report", None),
        "events": str(events),
        "cost_step": invocation_id.removeprefix(f"{ctx.run.id}:{attempt}:"),
        "service_tier": "standard" if hasattr(ctx.agent, "report") else None,
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
    invocation = ctx.store.runtime.invocation(invocation_id)
    if invocation is None or not events.exists():
        return
    text = events.read_text(errors="replace")
    # Account for complete lines even when a failed process left a partial final line.
    valid = []
    for line in text.splitlines():
        try:
            json.loads(line)
        except json.JSONDecodeError:
            continue
        valid.append(line)
    transcript = parse_events("\n".join(valid))
    payload: dict[str, Any] = {
        "thread_id": transcript.session_id,
        "usage": asdict(transcript.usage),
        "context": {"unavailable": "legacy exec has no current-window measurement"},
        "estimate": {
            "complete": False,
            "usd": None,
            "reason": "request service tier and context band unavailable",
        },
    }
    normalized = [json.loads(line) for line in valid]
    observations = [event for event in normalized if event.get("type") == "factory.usage"]
    if invocation["metadata"]["adapter"] == "app-server":
        telemetry = ThreadTelemetry(
            transcript.session_id or "", invocation["metadata"]["model"], semantics_verified=True
        )
        for sequence, event in enumerate(normalized):
            if event.get("type") == "factory.runtime":
                telemetry.observe(
                    event["event"], sequence=sequence, observed_at=event["observed_at"]
                )
            elif event.get("type") == "factory.context.invalidated":
                telemetry.context = CurrentContext(
                    unavailable=str(event.get("reason", "context invalidated"))
                )
        payload["context"] = asdict(telemetry.context)
        payload["compactions"] = telemetry.compactions
        payload["model_changes"] = telemetry.model_changes
        payload["current_model"] = telemetry.model
        payload["thread_total"] = asdict(telemetry.usage)
        if observations:
            observed = observations[-1]
            payload["usage"] = observed["usage"]
            payload["usage_complete"] = observed["complete"]
            payload["thread_total_wire"] = observed["thread_total"]
            prices = PriceBook.load(ctx.home / "config/prices.toml")
            estimates = [
                prices.estimate(
                    RequestUsage(
                        r["model"],
                        Usage(**r["usage"]),
                        datetime.fromtimestamp(r["observed_at"], UTC).date(),
                        r["service_tier"],
                        r["long_context"],
                    )
                )
                for r in observed["requests"]
            ]
            complete = observed["pricing_complete"] and all(e.complete for e in estimates)
            known = sum(e.usd or 0 for e in estimates)
            payload["estimate"] = {
                "complete": complete,
                "usd": known if complete else None,
                "known_usd": known,
                "requests": [asdict(e) for e in estimates],
                "reason": None if complete else "request pricing evidence incomplete",
            }
        if telemetry.invalid_events:
            payload["runtime_observation_errors"] = telemetry.invalid_events
            payload["usage_complete"] = False
            payload["estimate"] = {
                **payload["estimate"],
                "complete": False,
                "usd": None,
                "reason": "invalid runtime observations; retained priced lower bound only",
            }
        children = _linked_children(normalized, transcript.session_id)
        if children:
            reason = "linked child threads are not included in parent accounting"
            payload["nested_accounting"] = {
                "complete": False,
                "parent_thread_id": transcript.session_id,
                "child_thread_ids": children,
                "reason": reason,
            }
            payload["parent_estimate"] = payload["estimate"]
            payload["estimate"] = {
                **payload["estimate"],
                "complete": False,
                "usd": None,
                "reason": reason,
            }
    ctx.store.runtime.observe(invocation_id, len(valid), payload)
    # Reconcile even a duplicate observation: a process may have died after the
    # telemetry commit and before its cost update. Never regress to an older payload.
    retained = ctx.store.runtime.invocation(invocation_id)
    if retained is None or retained["sequence"] > len(valid):
        return
    payload = retained["telemetry"]
    model = invocation["metadata"]["model"]
    usage = Usage(**(payload.get("usage") or {}))
    ctx.store.reconcile_cost(
        ctx.run.id,
        attempt,
        invocation["metadata"].get("cost_step", step),
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_input_tokens,
        usd=payload["estimate"]["usd"],
    )


def _linked_children(events: list[dict[str, Any]], parent: str | None) -> list[str]:
    """Positive runtime ancestry only; unrelated threads do not establish nesting."""
    children: set[str] = set()
    if not parent:
        return []
    for row in events:
        if row.get("type") != "factory.runtime":
            continue
        event = row.get("event", {})
        if not isinstance(event, dict):
            continue
        params = event.get("params", {})
        if not isinstance(params, dict):
            continue
        child = None
        if event.get("method") in {"item/started", "item/completed"}:
            item = params.get("item", {})
            if not isinstance(item, dict):
                continue
            if params.get("threadId") == parent and item.get("type") == "subAgentActivity":
                child = item.get("agentThreadId")
        elif event.get("method") == "thread/started":
            thread = params.get("thread", {})
            if not isinstance(thread, dict):
                continue
            if thread.get("parentThreadId") == parent:
                child = thread.get("id")
        if isinstance(child, str) and child and child != parent:
            children.add(child)
    return sorted(children)


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
