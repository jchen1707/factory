"""Unit tests for the red-phase replay's pure classifier and weakening guard (§15.3).

The state-machine transitions are exercised in `tests/integration/test_pipeline.py` against
`FakeSandbox`; the two judgements that decide them — which failure kind is a real red phase
and which removed line weakens an assertion — are pure, so they are tested here without a
context, a sandbox or a store.
"""

from __future__ import annotations

from factory.sandbox.base import Completed
from factory.steps.redphase import _ASSERTION_PATTERNS, _classify, _hunks_for


def _completed(returncode: int, output: str = "") -> Completed:
    return Completed(("uv", "run", "pytest"), returncode, output, "")


# -- _classify (§15.3's outcome table) -----------------------------------------


def test_a_passing_gate_is_green_test_proves_nothing() -> None:
    assert _classify(_completed(0, "all tests passed"), ["tests/test_foo.py"]) == (
        "green",
        "all tests passed",
    )


def test_an_assertion_failure_in_a_new_test_is_a_real_red_phase() -> None:
    out = "tests/test_foo.py::test_new FAILED\nAssertionError: assert 1 == 2"
    assert _classify(_completed(1, out), ["tests/test_foo.py"])[0] == "red"


def test_a_collection_error_is_inconclusive_not_red() -> None:
    # The new test imports a module the implementation adds; at the base ref it is absent, so
    # the test cannot even be collected. That is the seam-not-separable case, not a red phase.
    out = "tests/test_foo.py::ERROR ... ModuleNotFoundError: No module named 'ingestion'"
    assert _classify(_completed(1, out), ["tests/test_foo.py"])[0] == "inconclusive"


def test_an_import_error_during_collection_is_inconclusive() -> None:
    out = "ImportError: cannot import name 'extract' from 'retrieval'"
    assert _classify(_completed(1, out), ["tests/test_foo.py"])[0] == "inconclusive"


def test_a_mixed_failure_with_an_assertion_dominates_to_red() -> None:
    # A real assertion failure in the new test plus an unrelated collection error elsewhere:
    # the assertion failure is the red phase, and the partial impl is the cause of both.
    out = (
        "tests/test_foo.py::test_new FAILED\nAssertionError: assert True\n"
        "tests/test_other.py::ERROR ImportError: No module named 'x'"
    )
    assert _classify(_completed(1, out), ["tests/test_foo.py"])[0] == "red"


def test_a_failure_with_no_readable_kind_is_inconclusive() -> None:
    # Conservative: do not claim a red phase the gate did not describe. Inconclusive reports,
    # it does not block, so a misread here is a noisy PR body rather than a wrong transition.
    assert _classify(_completed(1, "some opaque error"), ["tests/test_foo.py"])[0] == (
        "inconclusive"
    )


def test_a_gate_that_never_started_is_inconclusive_not_red() -> None:
    """The exact output measured on 2026-08-22 in a `--clone` scratch worktree.

    `pnpm test` with no `node_modules` exits 1 and prints "ELIFECYCLE Test failed", which
    the assertion list matches on the bare word `failed`. Before `_RUNNER_MISSING_SIGNS`
    this came back `red` — a real red phase claimed for a run in which no test executed at
    all, which is the worst verdict this module can produce and the one hardest to notice
    afterwards: it looks exactly like success.
    """
    out = (
        "> frontend-harness@0.0.0 test /Users/james/frontend-harness/.factory/"
        "worktrees/scratch-probe\n> vitest run\n\n"
        "sh: 1: vitest: not found\n"
        " ELIFECYCLE  Test failed. See above for more details.\n"
        " WARN   Local package.json exists, but node_modules missing, did you mean to install?"
    )
    assert _classify(_completed(1, out), ["src/foo.test.ts"])[0] == "inconclusive"


def test_a_missing_python_runner_is_inconclusive_too() -> None:
    out = "/bin/sh: 1: pytest: command not found"
    assert _classify(_completed(1, out), ["tests/test_foo.py"])[0] == "inconclusive"


def test_a_real_assertion_failure_is_still_red_when_the_runner_started() -> None:
    """The other direction: the new signatures must not swallow a genuine red phase. A
    vitest assertion failure mentions files that were "not found" by the assertion, never
    a runner that was."""
    out = (
        "FAIL src/search.test.ts > excludes internal documents\n"
        "AssertionError: expected [ 'internal.md' ] to deep equal []\n"
        "Tests  1 failed | 4 passed\n"
        " ELIFECYCLE  Test failed. See above for more details."
    )
    assert _classify(_completed(1, out), ["src/search.test.ts"])[0] == "red"


def test_a_nonzero_exit_with_a_failed_token_is_red() -> None:
    out = "FAILED tests/test_foo.py::test_new - assert 0 == 1"
    assert _classify(_completed(1, out), ["tests/test_foo.py"])[0] == "red"


# -- the weakening guard's assertion patterns --------------------------------


# §22 F29 — an existing assertion deleted from a test file is flagged, and `review`
# routes the run to `awaiting_human` with the hunk quoted.
def test_a_removed_assert_line_is_flagged_as_weakening() -> None:
    line = "-        assert result == 42"
    assert any(pat.search(line) for pat in _ASSERTION_PATTERNS)


def test_a_removed_self_assert_is_flagged() -> None:
    line = "-        self.assertEqual(len(items), 3)"
    assert any(pat.search(line) for pat in _ASSERTION_PATTERNS)


def test_a_removed_jest_expect_is_flagged() -> None:
    line = "-    expect(button).toBeEnabled()"
    assert any(pat.search(line) for pat in _ASSERTION_PATTERNS)


def test_a_kept_assertion_line_is_not_flagged() -> None:
    # A context line (leading space) or an added line is not a weakening.
    assert not any(pat.search("        assert result == 42") for pat in _ASSERTION_PATTERNS)
    assert not any(pat.search("+        assert result == 42") for pat in _ASSERTION_PATTERNS)


def test_a_removed_line_with_no_assertion_vocabulary_is_not_flagged() -> None:
    # The guard is deliberately conservative — it escalates to a human, never blocks, so a
    # removed comment that happens to contain the word `assert` is flagged for James to
    # glance at. A removed line with no assertion vocabulary at all is the one that must
    # stay silent, or every test-file edit would raise a false alarm.
    line = "-        # a note about the fixture"
    assert not any(pat.search(line) for pat in _ASSERTION_PATTERNS)


# -- _hunks_for (the weakening guard's diff slice) ----------------------------


def test_hunks_for_returns_only_hunks_touching_the_named_files() -> None:
    patch = (
        "diff --git a/tests/test_foo.py b/tests/test_foo.py\n"
        "@@ -1,3 +1,2 @@\n"
        "-        assert result == 42\n"
        "diff --git a/src/app.py b/src/app.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    hunks = _hunks_for(patch, {"tests/test_foo.py"})
    assert len(hunks) == 1
    assert "assert result == 42" in hunks[0]
    assert "src/app.py" not in hunks[0]


def test_hunks_for_returns_empty_when_the_patch_touches_no_named_file() -> None:
    patch = "diff --git a/src/app.py b/src/app.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
    assert _hunks_for(patch, {"tests/test_foo.py"}) == []


def test_unittest_import_error_with_failed_summary_is_inconclusive() -> None:
    out = (
        "ERROR: test_service_0 (unittest.loader._FailedTest.test_service_0)\n"
        "ImportError: cannot import name 'scale' from 'service_0'\n"
        "Ran 2 tests in 0.000s\nFAILED (errors=1)"
    )
    assert _classify(_completed(1, out), ["tests/test_service_0.py"])[0] == "inconclusive"


def test_missing_attribute_before_unittest_comparison_is_inconclusive() -> None:
    out = (
        "ERROR: test_scale (test_service_1.Baseline.test_scale)\n"
        "    self.assertEqual(service_1.scale(value), expected)\n"
        "AttributeError: module 'service_1' has no attribute 'scale'\n"
        "Ran 3 tests in 0.000s\nFAILED (errors=3)"
    )
    assert _classify(_completed(1, out), ["tests/test_service_1.py"])[0] == "inconclusive"


def test_failed_summary_alone_does_not_prove_an_assertion_executed() -> None:
    for out in ("FAILED", "tests/test_service.py::FAILED", "Tests 1 failed | 2 passed"):
        assert _classify(_completed(1, out), ["tests/test_service.py"])[0] == "inconclusive"


def test_unittest_assertion_failure_still_proves_red_phase() -> None:
    out = (
        "FAIL: test_scale (test_service_1.Baseline.test_scale)\n"
        "    self.assertEqual(service_1.scale(value), expected)\n"
        "AssertionError: 4 != 12\nRan 3 tests in 0.000s\nFAILED (failures=1)"
    )
    assert _classify(_completed(1, out), ["tests/test_service_1.py"])[0] == "red"


def test_pytest_attribute_error_before_assertion_is_inconclusive() -> None:
    # Exact host pytest excerpt retained in source-excerpt-before.json.
    out = (
        "    def test_scale():\n"
        "        service = object()\n"
        ">       assert service.scale(2) == 6\n"
        "               ^^^^^^^^^^^^^\n"
        "E       AttributeError: 'object' object has no attribute 'scale'\n"
        "FAILED test_missing.py::test_scale\n"
    )
    assert _classify(_completed(1, out), ["test_missing.py"])[0] == "inconclusive"


def test_javascript_expect_source_is_not_assertion_failure_evidence() -> None:
    out = (
        "FAIL src/service.test.ts > scales\n"
        "TypeError: service.scale is not a function\n"
        "  12 | expect(service.scale(2)).toBe(6)\n"
        "Tests 1 failed\n"
    )
    assert _classify(_completed(1, out), ["src/service.test.ts"])[0] == "inconclusive"


def test_pytest_comparison_diagnostic_is_red_without_assertionerror_name() -> None:
    out = ">       assert scale(2) == 6\nE       assert 2 == 6\nFAILED test_scale.py::test_scale"
    assert _classify(_completed(1, out), ["test_scale.py"])[0] == "red"
