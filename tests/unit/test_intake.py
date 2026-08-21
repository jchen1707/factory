"""§21.1 / §7.1 — the intake contract, including the two conditions P0-11 corrected."""

from __future__ import annotations

import pytest

from factory.intake.linear import (
    Issue,
    RepoFacts,
    context_files,
    eligibility_verdict,
    evaluate_eligibility,
)

SPEC = "S" * 250


def _issue(**overrides: object) -> Issue:
    defaults: dict[str, object] = {
        "identifier": "BAC-4",
        "title": "Application skeleton",
        "description": "## Acceptance criteria\n\n- [ ] it works\n",
        "url": "https://linear.app/x/issue/BAC-4",
        "state_name": "Todo",
        "state_type": "unstarted",
        "team_key": "BAC",
        "team_id": "team-uuid",
        "labels": ("ready-for-agent", "Feature"),
        "parent_identifier": "BAC-2",
        "parent_title": "Search internal support documents",
        "parent_description": SPEC,
    }
    defaults.update(overrides)
    return Issue(**defaults)  # type: ignore[arg-type]


def _facts(**overrides: object) -> RepoFacts:
    defaults: dict[str, object] = {
        "tracker_team": "BAC",
        "open_pr_heads": (),
        "base_ref_subjects": (),
        "remote_branches": (),
        "expected_branch": "feat/BAC-4-application-skeleton",
        "known_teams": frozenset({"BAC", "FRO"}),
    }
    defaults.update(overrides)
    return RepoFacts(**defaults)  # type: ignore[arg-type]


def test_bac_4_shaped_ticket_is_eligible() -> None:
    eligible, reason, failures = eligibility_verdict(evaluate_eligibility(_issue(), _facts()))
    assert eligible, failures
    assert reason is None


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"state_name": "Canceled"}, "state-not-todo"),
        ({"state_name": "Backlog"}, "state-not-todo"),
        ({"parent_identifier": None}, "no-parent-spec"),
        ({"parent_description": "too short"}, "empty-spec"),
        ({"description": "no criteria here"}, "no-acceptance-criteria"),
    ],
)
def test_a_ticket_meant_for_the_factory_but_not_ready_blocks(
    override: dict[str, object], reason: str
) -> None:
    eligible, got, _ = eligibility_verdict(evaluate_eligibility(_issue(**override), _facts()))
    assert not eligible
    assert got == reason


@pytest.mark.parametrize(
    "override",
    [
        {"labels": ("Feature",)},
        {"labels": ("ready-for-agent", "needs-info")},
        {"team_key": "ZZZ", "labels": ("ready-for-agent",)},
    ],
)
def test_a_ticket_that_was_never_the_factorys_creates_no_row(override: dict[str, object]) -> None:
    # No block reason means "not eligible, no row created, log once" — the ticket was
    # never meant for the factory, and blocking it would be the factory commenting on
    # somebody else's work.
    eligible, reason, _ = eligibility_verdict(evaluate_eligibility(_issue(**override), _facts()))
    assert not eligible
    assert reason is None


def test_condition_8_matches_the_branch_and_not_a_full_text_search() -> None:
    # P0-11 measured `gh pr list --search FRO-6` returning a PR that mentioned neither
    # identifier. Matching the head branch cannot produce that false duplicate-pr.
    eligible, reason, _ = eligibility_verdict(
        evaluate_eligibility(_issue(), _facts(open_pr_heads=("feat/BAC-4-application-skeleton",)))
    )
    assert not eligible
    assert reason == "duplicate-pr"

    eligible, _, _ = eligibility_verdict(
        evaluate_eligibility(_issue(), _facts(open_pr_heads=("fix/unrelated-thing",)))
    )
    assert eligible


def test_condition_10_blocks_work_already_on_the_base_ref() -> None:
    # FRO-5 was Todo, labelled, and already merged. Nine conditions saw nothing.
    eligible, reason, _ = eligibility_verdict(
        evaluate_eligibility(
            _issue(), _facts(base_ref_subjects=("0d08a3d feat: BAC-4 application skeleton",))
        )
    )
    assert not eligible
    assert reason == "already-implemented"


def test_an_unmerged_remote_branch_does_not_block_intake() -> None:
    # `origin/feat/BAC-4-application-skeleton` exists and is unmerged. It blocks the
    # branch *name* — enforced in repo.add_worktree — but the work is unlanded and may
    # well be worth redoing, so intake warns rather than refusing.
    eligible, _, _ = eligibility_verdict(
        evaluate_eligibility(
            _issue(), _facts(remote_branches=("origin/feat/BAC-4-application-skeleton",))
        )
    )
    assert eligible


def test_context_files_carry_the_slice_boundary() -> None:
    issue = _issue(
        siblings=(("BAC-3", "Todo", "Approve dependencies"), ("BAC-5", "Todo", "Ingestion")),
        comments=(("James", "2026-08-20", "start with the health endpoint"),),
    )
    files = context_files(issue)
    assert set(files) == {"ticket.md", "spec.md", "breakdown.md", "comments.md"}
    assert "BAC-3" in files["breakdown.md"]
    assert SPEC in files["spec.md"]
    assert "health endpoint" in files["comments.md"]
    assert all(body.strip() for body in files.values())


def test_acceptance_criteria_are_extracted_in_order() -> None:
    issue = _issue(
        description="## What to build\n\nprose\n\n## Acceptance criteria\n\n- [ ] one\n- [ ] two\n\n## Blocked by\n\nNone\n"
    )
    assert len(issue.acceptance_criteria) == 2
    assert issue.acceptance_criteria[0].endswith("one")
