"""Readiness uses real intake facts against the shipped layer-A contract."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from factory import handoffs
from factory.intake.linear import Issue
from factory.machine import Blocked
from factory.steps import Context


@pytest.fixture
def issue() -> Issue:
    return Issue(
        identifier="TEST-1",
        title="Create and list notes",
        description="## Acceptance criteria\n- [ ] Created notes appear in the list.\n",
        url="https://example.test/TEST-1",
        state_name="Backlog",
        state_type="backlog",
        team_key="TEST",
        team_id="test-team",
        labels=(),
        parent_identifier="TEST-0",
        parent_title="Notes CRUD",
        parent_description="Approved contract: persist notes in SQLite.",
    )


def context(issue: Issue | None, tests: tuple[str, ...] = ("tests/",)) -> Context:
    return cast(
        Context,
        SimpleNamespace(
            issue=issue,
            project=SimpleNamespace(path=Path(__file__).resolve().parents[2]),
            run=SimpleNamespace(id="readiness-test", linear_id="TEST-1"),
            store=SimpleNamespace(runtime=SimpleNamespace(policy=lambda run_id: None)),
            harness=SimpleNamespace(tests=tests),
        ),
    )


def test_real_issue_acceptance_is_available_to_shipped_contract(issue: Issue) -> None:
    assert issue.acceptance_criteria == ["- [ ] Created notes appear in the list."]
    assert handoffs.readiness(context(issue)) is False


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("description", "Create and list notes.", "readiness-missing-acceptance"),
        ("parent_description", "", "readiness-missing-authority"),
        ("blocked_by", (("TEST-2", "started"),), "readiness-dependencies"),
    ],
)
def test_missing_authority_or_open_dependency_still_blocks(
    issue: Issue, field: str, value: Any, reason: str
) -> None:
    with pytest.raises(Blocked, match=reason):
        handoffs.readiness(context(replace(issue, **{field: value})))


def test_completed_dependency_does_not_block(issue: Issue) -> None:
    resolved = replace(issue, blocked_by=(("TEST-2", "completed"),))
    assert handoffs.readiness(context(resolved)) is False


def test_missing_tests_requests_brief(issue: Issue) -> None:
    assert handoffs.readiness(context(issue, tests=())) is True


def test_missing_ticket_blocks() -> None:
    with pytest.raises(Blocked, match="readiness-missing-ticket"):
        handoffs.readiness(context(None))
