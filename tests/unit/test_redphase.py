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
    out = "tests/test_foo.py::test_new FAILED ... AssertionError: assert 1 == 2"
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
        "tests/test_foo.py::test_new FAILED AssertionError: assert True\n"
        "tests/test_other.py::ERROR ImportError: No module named 'x'"
    )
    assert _classify(_completed(1, out), ["tests/test_foo.py"])[0] == "red"


def test_a_failure_with_no_readable_kind_is_inconclusive() -> None:
    # Conservative: do not claim a red phase the gate did not describe. Inconclusive reports,
    # it does not block, so a misread here is a noisy PR body rather than a wrong transition.
    assert _classify(_completed(1, "some opaque error"), ["tests/test_foo.py"])[0] == (
        "inconclusive"
    )


def test_a_nonzero_exit_with_a_failed_token_is_red() -> None:
    out = "FAILED tests/test_foo.py::test_new - assert 0 == 1"
    assert _classify(_completed(1, out), ["tests/test_foo.py"])[0] == "red"


# -- the weakening guard's assertion patterns --------------------------------


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
