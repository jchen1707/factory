"""Golden test for the PR body (§13.2) — the evidence layout the deliver step assembles.

The body is built in Python (the control plane is stdlib-only by design), so a golden test
pins the layout: the §13.2 order, the gate table, the skipped-Tier-2 rule name, the red-phase
result, the artifact path and the cost. A change to the layout is a deliberate edit to the
expected string, not a silent drift.
"""

from __future__ import annotations

from typing import Any

from factory.delivery.github import pr_number, render_pr_body

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

    # The draft boundary is stated.
    assert "draft" in body


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
    assert "Tier-1 found no defects." in body
    assert "$0.42" in body


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
