"""The operations layout preserves actionable holds and navigation to run evidence."""

from __future__ import annotations

import re

import pytest

from factory.machine import State
from factory.steps import Context
from tests.integration.test_console import _client, _to_implementing


def test_blocked_work_precedes_queue_and_keeps_its_reason(ctx: Context) -> None:
    _to_implementing(ctx)
    ctx.store.update_run(ctx.run.id, blocked_reason="missing measured runtime certificate")
    ctx.store.record_transition(
        ctx.run.id,
        from_state=ctx.state,
        to_state=State.BLOCKED,
        actor="auto",
        rule="fixture-block",
    )
    page = _client(ctx).get("/").text
    attention = re.search(r'<article class="attention-item">(.*?)</article>', page, re.DOTALL)
    assert attention is not None
    assert "missing measured runtime certificate" in attention[1]
    assert 'href="/runs/BAC-4"' in attention[1]
    assert page.index(attention[0]) < page.index("<th>ticket</th>")


@pytest.mark.parametrize("route", ["/runs/BAC-4", "/runs/BAC-4/timeline", "/settings/runs/BAC-4"])
def test_each_run_view_links_its_siblings_and_marks_current(ctx: Context, route: str) -> None:
    _to_implementing(ctx)
    client = _client(ctx)
    page = client.get(route).text
    navigation = re.search(r'<nav[^>]*aria-label="Current run"[^>]*>(.*?)</nav>', page, re.DOTALL)
    assert navigation is not None
    for sibling in ("/runs/BAC-4", "/runs/BAC-4/timeline", "/settings/runs/BAC-4"):
        assert f'href="{sibling}"' in navigation[1]
        assert client.get(sibling).status_code == 200
    assert f'href="{route}" aria-current="page"' in navigation[1]
    assert navigation[1].count('aria-current="page"') == 1


def test_approval_attention_uses_observed_wait_not_mode_alone(ctx: Context) -> None:
    _to_implementing(ctx)
    ctx.store.runtime.configure("run", ctx.run.id, {"mode": "approval"})
    client = _client(ctx)
    assert '<article class="attention-item">' not in client.get("/").text
    ctx.store.runtime.start_invocation(
        "waiting-builder", ctx.run.id, 1, "implement", {"model": "fixture"}
    )
    ctx.store.runtime.configure("run", ctx.run.id, {"waiting_invocation": "waiting-builder"})
    page = client.get("/").text
    assert "<span>Awaiting invocation approval</span><strong>1</strong>" in page
    attention = re.search(r'<article class="attention-item">(.*?)</article>', page, re.DOTALL)
    assert attention is not None
    assert 'href="/settings/runs/BAC-4"' in attention[1]
    assert "waiting-builder" in attention[1]


def test_missing_frozen_policy_is_explicit_on_settings(ctx: Context) -> None:
    page = _client(ctx).get("/settings/runs/BAC-4").text
    assert "No frozen policy recorded." in page
    assert "Frozen policy evidence" not in page


def test_incomplete_runtime_cost_is_labeled_as_known_lower_bound(ctx: Context) -> None:
    ctx.store.runtime.start_invocation(
        "partial-cost", ctx.run.id, 1, "implement", {"model": "fixture"}
    )
    ctx.store.runtime.observe(
        "partial-cost", 1, {"estimate": {"usd": None, "known_usd": 0.25, "complete": False}}
    )
    page = _client(ctx).get("/settings/runs/BAC-4").text
    assert "API-equivalent estimated USD: ≥ $0.2500 · incomplete · known lower bound" in page
    ctx.store.runtime.observe("partial-cost", 2, {"estimate": {"usd": 0.5, "complete": True}})
    page = _client(ctx).get("/settings/runs/BAC-4").text
    assert "API-equivalent estimated USD: $0.5000 · complete" in page
    assert "known lower bound" not in page
