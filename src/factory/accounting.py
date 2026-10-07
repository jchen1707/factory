"""Record every scheduled model invocation before spawn and reconcile retained usage."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from types import MappingProxyType
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
    invocation_id = invocation_id or key(ctx, attempt, step)
    collect_invocation(ctx.store, invocation_id, events)


def collect_invocation(store: Store, invocation_id: str, events: Path) -> None:
    """Reconcile retained usage without loading workflow, routing or tracker dependencies.

    The CLI prices every model it used at list price: notional under a subscription,
    billed under an API key, the ceiling's number either way. Until the `result` event
    lands there is neither price nor per-model usage, so the estimate stays incomplete.

    A resumed session reports the whole session's cost and usage, not this launch's
    (measured: the `--resume` of a $0.0165 session ended at $0.0177). The row for this
    attempt is the difference from the session's last priced launch, so the run's spend
    is summed once however many times the ladder resumed.

    An interrupted launch prices nothing, and no lower bound is taken from its
    assistant lines: each carries the request's input and cache counts but only the
    opening output count (measured: 3 and 1 against a final 640), and pricing them
    would need the price table the cutover deleted. Its spend is not lost when the
    ladder resumes the session, because the next result's cumulative includes the
    killed request (measured: 5091 cache-write tokens of the killed launch inside the
    resume's 5163), and the baseline difference charges it to that launch. A launch
    that is never resumed is bounded by its own `--max-budget-usd` and the attempt
    ceiling; under a subscription the windows the launch guard reads count it either way.
    """
    invocation = store.runtime.invocation(invocation_id)
    if invocation is None or not events.exists():
        return
    text = events.read_text(errors="replace")
    # Only complete lines count toward the sequence: a torn last line that completes on
    # the next read must advance it, or that read's cost would never be retained.
    sequence = sum(1 for line in text.splitlines() if _parses(line))
    run = stream.parse(events)
    priced = run.by_model != {}
    cumulative_usage = _total_usage(run)
    before_usage, before_usd = _session_baseline(store, invocation, run.session)
    usage = cumulative_usage - before_usage
    payload: dict[str, Any] = {
        "evidence_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "session_id": run.session,
        "usage": asdict(usage),
        "cumulative": {"usage": asdict(cumulative_usage), "usd": run.notional_usd},
        "by_model": {
            model: {"usage": asdict(each.usage), "notional_usd": each.notional_usd}
            for model, each in run.by_model.items()
        },
        "context": {"tokens": run.context_tokens, "effective_window": _window(run)},
        "rate_limit": _limits_json(run),
        "estimate": {
            "complete": priced,
            "usd": run.notional_usd - before_usd if priced else None,
            "reason": None if priced else "the stream has no priced result event",
        },
    }
    store.runtime.observe(invocation_id, sequence, payload)
    # Reconcile even a duplicate observation: a process may have died after the
    # telemetry commit and before its cost update. Never regress to an older payload.
    retained = store.runtime.invocation(invocation_id)
    if retained is None or retained["sequence"] > sequence:
        return
    payload = retained["telemetry"]
    if set(payload.get("usage") or {}) != {field.name for field in fields(stream.Usage)}:
        # A record retained before the cutover carries the Codex usage shape; its stream
        # is unreadable here and its cost row stays what that collector wrote.
        return
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


#: No report observed longer ago than the longest window can still describe one.
_LONGEST_WINDOW_SECONDS = 7 * 86400


@dataclass(frozen=True)
class Limits:
    """The subscription's windows as every retained report, from any run, describes them.

    One subscription serves every run, so this is global. Utilisation only grows until a
    window resets, so for each window the report with the latest reset, then the highest
    utilisation, is current, whatever order the reports were collected in.
    `refused_until` is the latest reset reported by a launch that ended on a 429.
    """

    windows: Mapping[str, stream.Window]
    refused_until: int | None


def limits(store: Store, now: float) -> Limits:
    reports = store.runtime.rate_limit_reports(since=now - _LONGEST_WINDOW_SECONDS)
    seen: dict[str, list[stream.Window]] = {}
    for report in reports:
        for name, raw in report["windows"].items():
            seen.setdefault(name, []).append(stream.Window(**raw))
    return Limits(
        windows=MappingProxyType(
            {
                name: max(each, key=lambda window: (window.resets_at, window.utilization))
                for name, each in seen.items()
            }
        ),
        refused_until=max(
            (r["resets_at"] for r in reports if r["refused"] and r["resets_at"] is not None),
            default=None,
        ),
    )


def _limits_json(run: stream.Run) -> dict[str, Any] | None:
    if run.rate_limit is None:
        return None
    return {
        "status": run.rate_limit.status,
        "resets_at": run.rate_limit.resets_at,
        "windows": {name: asdict(window) for name, window in run.rate_limit.windows.items()},
        "refused": isinstance(run.outcome, stream.Failed)
        and run.outcome.kind is stream.FailureKind.RATE_LIMITED,
    }


def _parses(line: str) -> bool:
    try:
        json.loads(line)
    except ValueError:
        return False
    return True


_NO_USAGE = stream.Usage(input=0, cache_read=0, cache_write=0, output=0, thinking=0)


def _total_usage(run: stream.Run) -> stream.Usage:
    return sum((each.usage for each in run.by_model.values()), _NO_USAGE)


def _session_baseline(
    store: Store, invocation: dict[str, Any], session: str | None
) -> tuple[stream.Usage, float]:
    """What the session had already cost before this launch: the latest priced
    cumulative an earlier invocation of the same session retained, or nothing."""
    if session is None:
        return _NO_USAGE, 0.0
    earlier = [
        other["telemetry"]["cumulative"]
        for other in store.runtime.invocations(invocation["run_id"])
        if other["id"] != invocation["id"]
        and other["started_at"] <= invocation["started_at"]
        and (other.get("metadata") or {}).get("expected", {}).get("session") == session
        and (other.get("telemetry") or {}).get("cumulative")
    ]
    if not earlier:
        return _NO_USAGE, 0.0
    latest = max(earlier, key=lambda cumulative: float(cumulative["usd"]))
    return stream.Usage(**latest["usage"]), float(latest["usd"])


def _window(run: stream.Run) -> int | None:
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
