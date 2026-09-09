"""Detail hierarchy and action eligibility retain the lifecycle's authority."""

from __future__ import annotations

import re

import pytest

from factory.machine import State
from factory.steps import Context
from tests.integration.test_console import _client, _to_implementing


def test_detail_keeps_current_work_before_closed_retained_evidence(ctx: Context) -> None:
    _to_implementing(ctx)
    page = _client(ctx).get("/runs/BAC-4").text
    assert page.index('id="current-attempt"') < page.index('aria-label="Retained evidence"')
    assert "<h2>Verification and review</h2>" in page
    assert "No gate report recorded." in page
    assert "No review evidence recorded." in page
    for key in ("run-source", "run-tail", "run-artifacts"):
        disclosure = re.search(rf'<details([^>]*)data-key="{key}"([^>]*)>', page)
        assert disclosure is not None
        assert "open" not in disclosure.group(0)
    assert "actor=&quot;human&quot;" not in page
    assert 'class="danger secondary"' in page


@pytest.mark.parametrize(
    ("state", "offered"),
    [
        (State.IMPLEMENTING, {"suspend", "cancel"}),
        (State.SUSPENDED, {"resume", "resume-planning", "cancel"}),
        (State.BLOCKED, {"resume", "resume-planning", "cancel"}),
        (State.RESUMABLE, {"resume", "resume-planning", "cancel", "retry"}),
        (State.AWAITING_HUMAN, {"cancel"}),
        (State.FAILED, {"cancel"}),
    ],
)
def test_detail_actions_follow_shared_state_checks(
    ctx: Context, state: State, offered: set[str]
) -> None:
    _to_implementing(ctx)
    ctx.store.record_transition(ctx.run.id, from_state=None, to_state=state, actor="fixture")
    page = _client(ctx).get("/runs/BAC-4").text
    actions = set(re.findall(r'action="/runs/BAC-4/([^"]+)"', page))
    assert actions == offered
    assert "Unavailable actions" in page


def test_refused_retry_endpoint_does_not_change_active_run(ctx: Context) -> None:
    _to_implementing(ctx)
    before = len(ctx.store.transitions(ctx.run.id))
    response = _client(ctx).post("/runs/BAC-4/retry", follow_redirects=False)
    assert response.status_code == 303
    ctx.refresh()
    assert ctx.state is State.IMPLEMENTING
    assert len(ctx.store.transitions(ctx.run.id)) == before


def test_timeline_leads_with_measured_runtime_and_collapses_full_transition_evidence(
    ctx: Context,
) -> None:
    _to_implementing(ctx)
    page = _client(ctx).get("/runs/BAC-4/timeline").text
    assert page.index('class="waterfall"') < page.index("<h2>Execution history</h2>")
    assert page.index("<h2>Execution history</h2>") < page.index("<h2>Agent activity</h2>")
    assert '<details data-key="timeline-transitions">' in page
    assert 'class="transition-preview"' in page
    assert "No completed tool calls recorded for this attempt." in page
    assert "<h2>Tool calls" not in page
    assert 'data-scroll-key="waterfall"' in page
