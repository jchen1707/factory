"""Record every scheduled model invocation before spawn and reconcile retained usage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factory.agent.base import Usage
from factory.agent.codex import parse_events
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
    """Reconcile retained usage without loading workflow, routing or tracker dependencies."""
    invocation = store.runtime.invocation(invocation_id)
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
        "evidence_sha256": hashlib.sha256("\n".join(valid).encode()).hexdigest(),
        "thread_id": transcript.session_id,
        "usage": asdict(transcript.usage),
        "context": {"unavailable": "legacy exec has no current-window measurement"},
        "estimate": {
            "complete": False,
            "usd": None,
            "reason": "request service tier and context band unavailable",
        },
    }
    store.runtime.observe(invocation_id, len(valid), payload)
    # Reconcile even a duplicate observation: a process may have died after the
    # telemetry commit and before its cost update. Never regress to an older payload.
    retained = store.runtime.invocation(invocation_id)
    if retained is None or retained["sequence"] > len(valid):
        return
    payload = retained["telemetry"]
    model = invocation["metadata"]["model"]
    usage = Usage(**(payload.get("usage") or {}))
    store.reconcile_cost(
        invocation["run_id"],
        invocation["attempt"],
        invocation["metadata"].get("cost_step", invocation["role"]),
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_input_tokens,
        usd=payload["estimate"]["usd"],
    )


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
