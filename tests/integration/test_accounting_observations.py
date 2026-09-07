"""Collector context and known costs remain honest across runtime disruptions."""

import json
from pathlib import Path
from typing import Any

import pytest

from factory import accounting
from factory.steps import Context


def runtime(method: str, **params: Any) -> dict[str, Any]:
    return {
        "type": "factory.runtime",
        "observed_at": 100,
        "event": {"method": method, "params": {"threadId": "parent", **params}},
    }


def usage(total: int, last: int, turn: str) -> dict[str, Any]:
    return runtime(
        "thread/tokenUsage/updated",
        turnId=turn,
        tokenUsage={
            "total": {"inputTokens": total},
            "last": {"inputTokens": last},
            "modelContextWindow": 1000,
        },
    )


def collect(ctx: Context, monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> dict:
    monkeypatch.setattr(ctx.agent, "report", {}, raising=False)
    events = ctx.home / "observation-events.jsonl"
    events.write_text("".join(json.dumps(row) + "\n" for row in rows))
    identifier = accounting.begin(ctx, 1, ctx.routing.role("builder"), "implement", events)
    accounting.collect(ctx, 1, "implement", events)
    retained = ctx.store.runtime.invocation(identifier)
    assert retained is not None
    return retained["telemetry"]


def test_compaction_usage_cannot_restore_a_fresh_context_reading(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        {"type": "thread.started", "thread_id": "parent"},
        usage(810, 810, "work"),
        runtime("item/completed", turnId="compact", item={"type": "contextCompaction", "id": "c"}),
        usage(910, 100, "compact"),
        runtime("turn/completed", turn={"id": "compact", "status": "completed"}),
    ]
    telemetry = collect(ctx, monkeypatch, rows)
    assert telemetry["context"]["tokens"] is None
    assert "compaction" in telemetry["context"]["unavailable"]
    assert telemetry["thread_total"]["input_tokens"] == 910
    rows.append(usage(1110, 200, "next-work"))
    assert collect(ctx, monkeypatch, rows)["context"]["tokens"] == 200


def test_bad_parent_usage_retains_priced_lower_bound_without_claiming_complete_context(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    prices = Path(__file__).parents[2] / "config/prices.toml"
    (ctx.home / "config/prices.toml").write_text(prices.read_text())
    measured = {"input_tokens": 1000, "output_tokens": 100}
    bad = usage(1001, 1, "work")
    bad["event"]["params"]["tokenUsage"]["total"]["inputTokens"] = "bad"
    rows = [
        {"type": "thread.started", "thread_id": "parent"},
        usage(1000, 1000, "work"),
        bad,
        {
            "type": "factory.usage",
            "usage": measured,
            "complete": False,
            "thread_total": {"inputTokens": 1000},
            "pricing_complete": False,
            "requests": [
                {
                    "model": "gpt-5.6-terra",
                    "usage": measured,
                    "observed_at": 1788756749,
                    "service_tier": "standard",
                    "long_context": False,
                }
            ],
        },
    ]
    telemetry = collect(ctx, monkeypatch, rows)
    assert telemetry["context"]["tokens"] is None
    assert "invalid" in telemetry["context"]["unavailable"]
    assert telemetry["estimate"]["complete"] is False
    assert telemetry["estimate"]["known_usd"] == pytest.approx(0.0032)
    assert ctx.store.known_spend(ctx.run.id) == pytest.approx(0.0032)
    assert telemetry["thread_total"]["input_tokens"] == 1000
    assert collect(ctx, monkeypatch, rows) == telemetry


def test_worker_invalidation_survives_a_disconnect_before_compaction_finishes(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    telemetry = collect(
        ctx,
        monkeypatch,
        [
            {"type": "thread.started", "thread_id": "parent"},
            usage(810, 810, "work"),
            {"type": "factory.context.invalidated", "reason": "compaction requested"},
        ],
    )
    assert telemetry["context"]["tokens"] is None
    assert telemetry["context"]["unavailable"] == "compaction requested"


@pytest.mark.parametrize("turn_id", [None, "", 7, {}])
def test_compaction_without_valid_turn_identity_cannot_restore_context(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, turn_id: Any
) -> None:
    telemetry = collect(
        ctx,
        monkeypatch,
        [
            {"type": "thread.started", "thread_id": "parent"},
            usage(810, 810, "work"),
            runtime(
                "item/completed",
                turnId=turn_id,
                item={"type": "contextCompaction", "id": "c"},
            ),
            usage(910, 100, "possibly-compact"),
        ],
    )
    assert telemetry["context"]["tokens"] is None
    assert "compaction" in telemetry["context"]["unavailable"]
    assert telemetry["thread_total"]["input_tokens"] == 910


def test_retained_requests_can_be_priced_when_price_book_arrives(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    price_path = ctx.home / "config/prices.toml"
    price_path.write_text("")
    rows = [
        {
            "type": "factory.usage",
            "usage": {"input_tokens": 1000, "output_tokens": 100},
            "complete": True,
            "thread_total": {"inputTokens": 1000},
            "pricing_complete": True,
            "requests": [
                {
                    "model": "gpt-5.6-terra",
                    "usage": {"input_tokens": 1000, "output_tokens": 100},
                    "observed_at": 1788756749,
                    "service_tier": "standard",
                    "long_context": False,
                }
            ],
        }
    ]
    initial = collect(ctx, monkeypatch, rows)
    assert initial["estimate"]["complete"] is False
    price_path.write_text((Path(__file__).parents[2] / "config/prices.toml").read_text())
    priced = collect(ctx, monkeypatch, rows)
    assert priced["estimate"]["complete"] is True
    assert ctx.store.known_spend(ctx.run.id) == pytest.approx(0.0032)
    assert {k: v for k, v in priced.items() if k != "estimate"} == {
        k: v for k, v in initial.items() if k != "estimate"
    }
    assert collect(ctx, monkeypatch, rows) == priced
    assert collect(ctx, monkeypatch, []) == priced
    assert ctx.store.known_spend(ctx.run.id) == pytest.approx(0.0032)


def test_repricing_refuses_changed_request_evidence_at_same_sequence(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    (ctx.home / "config/prices.toml").write_text(
        (Path(__file__).parents[2] / "config/prices.toml").read_text()
    )
    request = {
        "model": "gpt-5.6-terra",
        "usage": {"input_tokens": 1000, "output_tokens": 100},
        "observed_at": 1788756749,
        "service_tier": "standard",
        "long_context": False,
    }
    rows = [
        {
            "type": "factory.usage",
            "usage": request["usage"],
            "complete": True,
            "thread_total": {"inputTokens": 1000},
            "pricing_complete": True,
            "requests": [request],
        }
    ]
    priced = collect(ctx, monkeypatch, rows)
    request["long_context"] = True
    assert collect(ctx, monkeypatch, rows) == priced
    assert ctx.store.known_spend(ctx.run.id) == pytest.approx(0.0032)
