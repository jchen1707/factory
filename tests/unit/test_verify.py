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
