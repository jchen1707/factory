"""Unit tests for the verify step's pure cross-check logic (§15.1).

The state-machine transitions are exercised in `tests/integration/test_pipeline.py`
against `FakeSandbox`. The two judgements that decide them — which gates count as
opt-in, and which claims disagree with the report — are pure, so they are tested here
without a context, a sandbox or a store.
"""

from __future__ import annotations

from pathlib import Path

from factory.harness import Gate, HarnessConfig
from factory.steps.verify import _claims_opt_in, _evidence_mismatch


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


# -- the --all decision (§12.1) ------------------------------------------------


def test_a_claimed_e2e_gate_asserts_all() -> None:
    harness = _harness(
        Gate("ruff check", "lint", ("uv", "run", "ruff", "check", ".")),
        Gate("playwright smoke", "e2e", ("pnpm", "exec", "playwright")),
    )
    assert _claims_opt_in(harness, ["ruff check", "playwright smoke"]) is True


def test_a_claimed_integration_gate_asserts_all() -> None:
    harness = _harness(Gate("integration", "integration", ()))
    assert _claims_opt_in(harness, ["integration"]) is True


def test_only_stop_kinds_claimed_does_not_assert_all() -> None:
    harness = _harness(Gate("ruff check", "lint", ()), Gate("pytest", "test", ()))
    assert _claims_opt_in(harness, ["ruff check", "pytest"]) is False


def test_a_claimed_gate_not_in_the_config_does_not_assert_all() -> None:
    # `mypy` is claimed but the config declares no such gate, so there is no kind to
    # cross-check and `--all` is not passed on its account.
    harness = _harness(Gate("ruff check", "lint", ()))
    assert _claims_opt_in(harness, ["mypy"]) is False


def test_no_harness_config_does_not_assert_all() -> None:
    assert _claims_opt_in(None, ["playwright smoke"]) is False


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


def test_a_free_form_claim_of_an_opt_in_gate_asks_for_all() -> None:
    """The FRO-6 defect. `_claims_opt_in` looked the claim up in a dict by equality while
    `_evidence_mismatch`, ten lines below, matched by containment — so every opt-in
    lookup missed, `--all` was never passed, `playwright` and `lighthouse` came back
    `not_applicable`, and the very same claims then failed the mismatch check for not
    being `pass` or `fail`. The agent had run both, honestly, and was blocked for it."""
    assert _claims_opt_in(_frontend_harness(), _REAL_CLAIMS)


def test_claiming_no_opt_in_gate_leaves_all_off() -> None:
    # `--all` is opt-in and asserting a `when` clause nobody claimed would spend an e2e
    # suite on every run.
    assert not _claims_opt_in(
        _frontend_harness(), ["eslint — pnpm lint passed", "vitest — pnpm test passed"]
    )


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
