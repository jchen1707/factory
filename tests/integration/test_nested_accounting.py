"""Nested runtime evidence must not masquerade as complete invocation accounting."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from factory import accounting, execution
from factory.machine import Blocked
from factory.steps import Context


def test_linked_child_marks_estimate_incomplete_and_preserves_budget_lower_bound(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ctx.agent, "report", {}, raising=False)
    prices = Path(__file__).parents[2] / "config/prices.toml"
    (ctx.home / "config/prices.toml").write_text(prices.read_text())
    events = ctx.home / "nested-events.jsonl"
    usage = {"input_tokens": 1000, "output_tokens": 100}
    rows = [
        {"type": "thread.started", "thread_id": "parent"},
        {
            "type": "factory.usage",
            "usage": usage,
            "complete": True,
            "thread_total": {},
            "pricing_complete": True,
            "requests": [
                {
                    "model": "gpt-5.6-terra",
                    "usage": usage,
                    "observed_at": 1788756749,
                    "service_tier": "standard",
                    "long_context": False,
                }
            ],
        },
    ]
    child_event = {
        "type": "factory.runtime",
        "observed_at": 1788756752,
        "event": {
            "method": "item/started",
            "params": {
                "threadId": "parent",
                "item": {
                    "type": "subAgentActivity",
                    "kind": "started",
                    "agentThreadId": "child",
                    "agentPath": "/root/child",
                },
            },
        },
    }
    events.write_text("".join(json.dumps(row) + "\n" for row in [*rows, child_event, child_event]))
    identifier = accounting.begin(ctx, 1, ctx.routing.role("builder"), "implement", events)
    accounting.collect(ctx, 1, "implement", events)
    first = ctx.store.runtime.invocation(identifier)
    accounting.collect(ctx, 1, "implement", events)
    assert ctx.store.runtime.invocation(identifier) == first
    assert first is not None
    telemetry = first["telemetry"]
    assert telemetry["estimate"]["complete"] is False
    assert telemetry["estimate"]["usd"] is None
    assert telemetry["estimate"]["known_usd"] == pytest.approx(0.0032)
    assert telemetry["parent_estimate"]["complete"] is True
    assert telemetry["nested_accounting"]["child_thread_ids"] == ["child"]
    assert telemetry["usage"] == usage
    assert ctx.store.spend(ctx.run.id) == (1000, 100, None)
    assert ctx.store.known_spend(ctx.run.id) == pytest.approx(0.0032)
    assert len(ctx.store.costs(ctx.run.id)) == 1
    ctx.routing = replace(ctx.routing, usd_per_run=0.003)
    with pytest.raises(Blocked, match="budget-exceeded"):
        execution.guard(ctx, 2, "implement")


@pytest.mark.parametrize("linked", [True, False])
def test_explicit_parent_link_is_required_for_other_thread_metadata(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, linked: bool
) -> None:
    monkeypatch.setattr(ctx.agent, "report", {}, raising=False)
    events = ctx.home / "ancestry.jsonl"
    rows: list[dict[str, Any]] = [{"type": "thread.started", "thread_id": "parent"}]
    for child in ("z-child", "a-child", "z-child"):
        rows.append(
            {
                "type": "factory.runtime",
                "observed_at": 1788756752,
                "event": {
                    "method": "thread/started",
                    "params": {
                        "thread": {
                            "id": child,
                            "parentThreadId": "parent" if linked else "unrelated",
                        }
                    },
                },
            }
        )
    rows.append(
        {
            "type": "factory.runtime",
            "observed_at": 1788756752,
            "event": {
                "method": "item/completed",
                "params": {
                    "threadId": "unrelated",
                    "item": {"type": "subAgentActivity", "agentThreadId": "unrelated-child"},
                },
            },
        }
    )
    events.write_text("".join(json.dumps(row) + "\n" for row in rows))
    identifier = accounting.begin(ctx, 1, ctx.routing.role("builder"), "implement", events)
    accounting.collect(ctx, 1, "implement", events)
    retained = ctx.store.runtime.invocation(identifier)
    assert retained is not None
    telemetry = retained["telemetry"]
    assert telemetry["estimate"]["complete"] is False  # Missing pricing remains unknown.
    if linked:
        assert telemetry["nested_accounting"]["child_thread_ids"] == ["a-child", "z-child"]
    else:
        assert "nested_accounting" not in telemetry
    assert ctx.store.known_spend(ctx.run.id) == 0
