"""Unit tests for the verify step's pure cross-check logic (§15.1).

The state-machine transitions are exercised in `tests/integration/test_pipeline.py`
against `FakeSandbox`. The two judgements that decide them — which gates count as
opt-in, and which claims disagree with the report — are pure, so they are tested here
without a context, a sandbox or a store.
"""

from __future__ import annotations

from pathlib import Path

from factory.harness import Gate, HarnessConfig
from factory.steps.verify import _asserted_opt_in_gates, _evidence_mismatch, _gate_argv


def _harness(*gates: Gate) -> HarnessConfig:
    return HarnessConfig(
        root=Path("/repo"),
        name="python-harness",
        team="BAC",
        gates=tuple(gates),
        protected=(),
        gated_paths=(),
        gated_files=(),
        secret_vars=(),
        apps=(),
        tests=(),
        review_agent_dir=".agents/agents",
        review_checklist_dir="docs/agents/subagents",
    )


# -- the per-gate opt-in assertion (§12.1) -------------------------------------


def test_a_claimed_e2e_gate_is_asserted_by_name() -> None:
    harness = _harness(
        Gate("ruff check", "lint", ("uv", "run", "ruff", "check", ".")),
        Gate("playwright smoke", "e2e", ("pnpm", "exec", "playwright")),
    )
    assert _asserted_opt_in_gates(harness, ["ruff check", "playwright smoke"]) == [
        "playwright smoke"
    ]


def test_a_claimed_integration_gate_is_asserted_by_name() -> None:
    harness = _harness(Gate("integration", "integration", ()))
    assert _asserted_opt_in_gates(harness, ["integration"]) == ["integration"]


def test_only_stop_kinds_claimed_asserts_nothing() -> None:
    harness = _harness(Gate("ruff check", "lint", ()), Gate("pytest", "test", ()))
    assert _asserted_opt_in_gates(harness, ["ruff check", "pytest"]) == []


def test_a_claimed_gate_not_in_the_config_asserts_nothing() -> None:
    # `mypy` is claimed but the config declares no such gate, so there is no kind to
    # cross-check and nothing is asserted on its account.
    harness = _harness(Gate("ruff check", "lint", ()))
    assert _asserted_opt_in_gates(harness, ["mypy"]) == []


def test_no_harness_config_asserts_nothing() -> None:
    assert _asserted_opt_in_gates(None, ["playwright smoke"]) == []


def test_two_claims_naming_one_gate_assert_it_once() -> None:
    harness = _harness(Gate("playwright", "e2e", ()))
    argv = _gate_argv(harness, ["playwright — 4 tests", "playwright — rerun, 4 tests"], "")
    assert argv.count("--gate") == 1


# -- the evidence-mismatch cross-check (§15.1) --------------------------------


def test_a_pass_and_a_fail_both_satisfy_the_claim() -> None:
    report = [{"name": "ruff check", "status": "pass"}, {"name": "pytest", "status": "fail"}]
    assert _evidence_mismatch(["ruff check", "pytest"], report) == []


def test_a_claimed_gate_absent_from_the_report_is_a_mismatch() -> None:
    report = [{"name": "ruff check", "status": "pass"}]
    assert _evidence_mismatch(["ruff check", "mypy"], report) == ["mypy"]


def test_a_claimed_gate_marked_unavailable_is_a_mismatch() -> None:
    report = [{"name": "mypy", "status": "unavailable"}]
    assert _evidence_mismatch(["mypy"], report) == ["mypy"]


def test_a_claimed_gate_marked_not_applicable_is_a_mismatch() -> None:
    report = [{"name": "playwright smoke", "status": "not_applicable"}]
    assert _evidence_mismatch(["playwright smoke"], report) == ["playwright smoke"]


def test_a_claimed_gate_marked_skipped_unchanged_is_a_mismatch() -> None:
    report = [{"name": "app-b lint", "status": "skipped_unchanged"}]
    assert _evidence_mismatch(["app-b lint"], report) == ["app-b lint"]


def test_mismatched_gates_are_returned_in_claim_order() -> None:
    report = [{"name": "pytest", "status": "pass"}]
    assert _evidence_mismatch(["mypy", "ruff check", "pytest"], report) == [
        "mypy",
        "ruff check",
    ]


# -- the opt-in lookup and the mismatch check must read a claim the same way ----

#: What a real agent writes, measured from FRO-6's `last-message.json` on 2026-08-22.
#: Free-form: the command it ran and the outcome, never the bare gate name. The schema is
#: `array of string` and the prompt fixes no format, so this shape is the contract.
_REAL_CLAIMS = [
    "eslint — pnpm lint passed",
    "vitest — pnpm test passed (73 tests)",
    "playwright — pnpm test:e2e passed (4 tests)",
    "lighthouse — pnpm lhci exited 0; known performance NaN warning reported",
]


def _frontend_harness() -> HarnessConfig:
    """The four gates `frontend-harness` declares, two of them opt-in."""
    return _harness(
        Gate(name="eslint", kind="lint", run=("pnpm", "lint")),
        Gate(name="vitest", kind="test", run=("pnpm", "test")),
        Gate(name="playwright", kind="e2e", run=("pnpm", "test:e2e")),
        Gate(name="lighthouse", kind="integration", run=("pnpm", "lhci")),
    )


def test_a_free_form_claim_of_an_opt_in_gate_is_resolved_to_its_gate() -> None:
    """The FRO-6 defect. `_claims_opt_in` looked the claim up in a dict by equality while
    `_evidence_mismatch`, ten lines below, matched by containment — so every opt-in
    lookup missed, no opt-in gate was asserted, `playwright` and `lighthouse` came back
    `not_applicable`, and the very same claims then failed the mismatch check for not
    being `pass` or `fail`. The agent had run both, honestly, and was blocked for it."""
    assert _asserted_opt_in_gates(_frontend_harness(), _REAL_CLAIMS) == [
        "playwright",
        "lighthouse",
    ]


def test_claiming_no_opt_in_gate_asserts_nothing() -> None:
    # Asserting a `when` clause nobody claimed would spend an e2e suite on every run.
    assert not _asserted_opt_in_gates(
        _frontend_harness(), ["eslint — pnpm lint passed", "vitest — pnpm test passed"]
    )


def test_claiming_playwright_does_not_assert_lighthouse() -> None:
    """The FRO-7 defect, and the reason `--all` is gone from the argv.

    FRO-7's agent claimed five stop gates and `playwright`, all of which passed. The old
    code answered "some opt-in gate was claimed" with a bool and the caller passed
    `--all`, which asserted *every* opt-in `when` clause — including lighthouse's
    "performance or accessibility budgets are in scope", false for a status filter. That
    gate cannot pass on this machine (its own caveat: the performance category scores
    null against the installed Chrome), so the run blocked with `env-gate-failed` on a
    gate that should never have executed, and no amount of implementing could clear it.
    """
    claims = [
        "eslint — passed",
        "vitest — 78 tests passed",
        "playwright — 5 tests passed",
    ]
    assert _asserted_opt_in_gates(_frontend_harness(), claims) == ["playwright"]

    argv = _gate_argv(_frontend_harness(), claims, "origin/v2")
    assert "--all" not in argv, "the blanket assertion is what forced lighthouse to run"
    assert argv.count("--gate") == 1
    assert "playwright" in argv
    assert "lighthouse" not in argv


def test_the_two_checks_agree_about_which_gate_a_claim_names() -> None:
    # The property that failed: one matcher, or they drift. A claim the opt-in lookup
    # resolves to `playwright` must be the claim the mismatch check resolves to
    # `playwright` too.
    report = [
        {"name": "eslint", "status": "pass"},
        {"name": "vitest", "status": "pass"},
        {"name": "playwright", "status": "pass"},
        {"name": "lighthouse", "status": "pass"},
    ]

    assert _evidence_mismatch(_REAL_CLAIMS, report) == []


def test_longest_gate_name_still_wins() -> None:
    # `uv run pytest -m integration` must not match the bare `pytest` gate first.
    report = [
        {"name": "pytest", "status": "skipped_unchanged"},
        {"name": "pytest -m integration", "status": "pass"},
    ]

    assert _evidence_mismatch(["uv run pytest -m integration — 3 passed"], report) == []


def test_a_claim_the_report_shows_as_failed_is_still_not_a_mismatch() -> None:
    # An honest `fail` in a claim is accepted: this check proves the gate *ran*, and the
    # report's verdict — never the claim's text — decides pass versus fail.
    assert (
        _evidence_mismatch(["vitest — pnpm test failed"], [{"name": "vitest", "status": "fail"}])
        == []
    )


def test_a_claim_for_a_gate_that_never_ran_is_still_a_mismatch() -> None:
    # The check the FRO-6 fix must not weaken. `skipped_unchanged` means the report did
    # run the dispatch and decided this app's files were untouched, so a claim of it is
    # the agent saying it ran something against code its change did not exercise.
    assert _evidence_mismatch(
        ["vitest — pnpm test passed"], [{"name": "vitest", "status": "skipped_unchanged"}]
    ) == ["vitest"]
