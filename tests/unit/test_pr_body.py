"""Golden test for the PR body (§13.2) — the evidence layout the deliver step assembles.

The body is built in Python (the control plane is stdlib-only by design), so a golden test
pins the layout: the §13.2 order, the gate table, the skipped-Tier-2 rule name, the red-phase
result, the artifact path and the cost. A change to the layout is a deliberate edit to the
expected string, not a silent drift.
"""

from __future__ import annotations

from typing import Any

from factory.delivery.body import render_pr_body
from factory.delivery.github import pr_number

GATES: list[dict[str, Any]] = [
    {"name": "ruff check", "kind": "lint", "status": "pass", "caveat": None},
    {"name": "mypy", "kind": "type", "status": "pass", "caveat": None},
    {
        "name": "playwright smoke",
        "kind": "e2e",
        "status": "not_applicable",
        "caveat": "no UI in this change",
    },
]

REVIEW = {
    "tier2": "no-trigger",
    "findings": [
        {"severity": "low", "file": "src/app.py", "line": 12, "summary": "a nit"},
    ],
}

REDPHASE = {"status": "pass", "reason": "red"}


def test_render_pr_body_lays_out_the_evidence_in_spec_order() -> None:
    body = render_pr_body(
        ticket="BAC-5",
        title="Extract pages from a PDF and chunk them",
        restatement="Adds page extraction and chunking to the retrieval pipeline.",
        gates=GATES,
        gate_verdict="pass",
        review_summary=REVIEW,
        redphase=REDPHASE,
        out_of_scope=["BAC-3's dependency approval"],
        artifact_path="/Users/james/factory/artifacts/BAC-5/1",
        tokens_in=9600000,
        tokens_out=46000,
        usd=None,
    )

    # §13.2 order: Fixes first, on its own line.
    assert body.splitlines()[0] == "Fixes BAC-5"

    # The gate table carries every row, including the not_applicable one with its caveat.
    assert "| ruff check | lint | pass |" in body
    assert "| playwright smoke | e2e | not_applicable | no UI in this change |" in body
    assert "Verdict: **pass**" in body

    # The skipped-Tier-2 rule is named.
    assert "Tier 2 skipped — rule: `no-trigger`" in body

    # The red-phase replay result is present.
    assert "Status: **pass** — red" in body

    # Out-of-scope, artifact path, cost — the tail of §13.2's order.
    assert "- BAC-3's dependency approval" in body
    assert "/Users/james/factory/artifacts/BAC-5/1" in body
    assert "Tokens: in 9600000, out 46000 — token counts only (no price row)" in body

    # The merge boundary is stated, and the PR is not a draft: §24.8 opens it
    # ready for review so `agent-review.yml` fires on open. The body must not go on
    # claiming a draft the argv no longer asks for.
    assert "Merge is a human's." in body
    assert "draft" not in body.lower()


def test_render_pr_body_names_tier2_ran_when_it_ran() -> None:
    body = render_pr_body(
        ticket="BAC-6",
        title="t",
        restatement="r",
        gates=[],
        gate_verdict="pass",
        review_summary={"tier2": "ran", "findings": []},
        redphase=None,
        out_of_scope=[],
        artifact_path="/a",
        tokens_in=0,
        tokens_out=0,
        usd=0.42,
    )
    assert "Tier 2 (full nine-axis review) ran." in body
    assert "No findings." in body
    assert "$0.42" in body


def test_render_pr_body_reports_a_forced_tier2_as_ran_not_skipped() -> None:
    # `--full-review` forces the Tier 2 fan-out; review-summary records `tier2: "ran:forced"`.
    # A forced fan-out reported as "Tier 2 skipped" — as FRO-6's first completed Tier 2 was —
    # misstates what happened: the review ran, it did not skip. It also labels Tier-2
    # findings as "Tier-1 findings", hiding that the fan-out produced them. The forced value
    # is distinct from both "ran" (a trigger fired) and any skip rule.
    body = render_pr_body(
        ticket="FRO-6",
        title="Search your Projects by name",
        restatement="r",
        gates=[],
        gate_verdict="fail",
        review_summary={
            "tier2": "ran:forced",
            "findings": [
                {"severity": "medium", "file": "src/x.tsx", "line": 16, "summary": "a finding"},
            ],
        },
        redphase={"status": "pass", "reason": "red"},
        out_of_scope=[],
        artifact_path="/a",
        tokens_in=0,
        tokens_out=0,
        usd=None,
    )
    assert "Tier 2 ran (forced with `--full-review`; no trigger rule fired)." in body
    assert "Tier 2 skipped" not in body
    assert "Findings:" in body
    assert "Tier-1 findings:" not in body
    assert "[medium] src/x.tsx:16: a finding" in body


def test_render_pr_body_handles_a_missing_redphase_row() -> None:
    body = render_pr_body(
        ticket="BAC-7",
        title="t",
        restatement="r",
        gates=[],
        gate_verdict="pass",
        review_summary=None,
        redphase=None,
        out_of_scope=[],
        artifact_path="/a",
        tokens_in=0,
        tokens_out=0,
        usd=None,
    )
    assert "Not run (no behaviour change reported)" in body
    assert "_No review summary recorded._" in body


def test_pr_number_extracts_the_number_from_a_gh_url() -> None:
    assert pr_number("https://github.com/jchen1707/python-harness/pull/9") == 9
    assert pr_number("not a url") is None


def test_a_cleared_escalation_is_named_in_the_body() -> None:
    # A PR whose review ran only because a human overruled a §15.3 guard must say so. The
    # section is the difference between "no guard fired" and "a guard fired and was cleared",
    # which are indistinguishable from the Review section alone.
    body = render_pr_body(
        ticket="FRO-11",
        title="t",
        restatement="r",
        gates=[],
        gate_verdict="pass",
        review_summary=REVIEW,
        redphase=REDPHASE,
        escalations=[{"rule": "test-weakening", "note": "the assertions asserted a deleted stub"}],
        out_of_scope=[],
        artifact_path="/a",
        tokens_in=0,
        tokens_out=0,
        usd=None,
    )
    assert "Cleared escalations" in body
    assert "`test-weakening`" in body
    assert "the assertions asserted a deleted stub" in body


def test_an_ordinary_run_has_no_cleared_escalations_section() -> None:
    body = render_pr_body(
        ticket="FRO-11",
        title="t",
        restatement="r",
        gates=[],
        gate_verdict="pass",
        review_summary=REVIEW,
        redphase=REDPHASE,
        out_of_scope=[],
        artifact_path="/a",
        tokens_in=0,
        tokens_out=0,
        usd=None,
    )
    assert "Cleared escalations" not in body


def test_an_accepted_review_finding_records_both_positions() -> None:
    body = render_pr_body(
        ticket="BAC-49",
        title="t",
        restatement="r",
        gates=[],
        gate_verdict="pass",
        review_summary={
            "tier2": "ran",
            "findings": [
                {
                    "severity": "high",
                    "file": "src/app.py",
                    "line": 9,
                    "summary": "requires production hardening",
                }
            ],
        },
        redphase=REDPHASE,
        disputed_findings=[
            {
                "note": "James accepts this as post-POC hardening.",
            }
        ],
        out_of_scope=[],
        artifact_path="/a",
        tokens_in=0,
        tokens_out=0,
        usd=None,
    )
    assert "[high] src/app.py:9: requires production hardening" in body
    assert "Disputed review findings" in body
    assert "remain unresolved" in body
    assert "James accepts this as post-POC hardening." in body
