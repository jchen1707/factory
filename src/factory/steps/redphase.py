"""The red-phase replay and the test-weakening guard — §15.3.

`/tdd` is how the work gets done well; this module is how the factory *knows* the red step
happened. A gate cannot tell a real test from a rubber stamp — `verify.mjs` sees a green
`pytest` either way — so the replay enforces the **property** the ritual exists to produce:
*a test that fails without the implementation*. That is mechanically checkable after the fact
and cannot be faked by ordering, by narration, or by a self-report.

Run at the **reviewing** entry (called by `review.py` before the Tier-1 review), when the
implement result reports `behaviour_changed = true`. §15.3 says "run in `verifying`", but the
machine makes `verifying -> awaiting_human` illegal while §15.3's `inconclusive: escalate`
option routes to `awaiting_human`; the transition table is the reachability authority (it has
been corrected before for exactly this kind of gap), so the replay runs in `reviewing`, where
`blocked`, `awaiting_human` and "proceed with the review" are all legal. `verify.py` is
unchanged.

Two outcomes are **not configurable and always block**, because they are true by construction
in every codebase: a test that passes at the base ref proves nothing about the new behaviour,
and a behaviour change with no test at all. Only the inconclusive row is a judgement, and it
**reports** rather than blocks by default — blocking would punish the agent for a property of
the codebase (a seam that does not separate) rather than for anything it did.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from factory import repo
from factory.artifacts import AttemptDir
from factory.machine import Blocked, State
from factory.sandbox.base import Completed
from factory.steps import Context
from factory.steps import clone as clone_step

__all__ = ["ReplayOutcome", "replay", "weakening_guard"]

STEP = "redphase"

#: What `replay` returns when it does not raise. `review.py` owns the state-machine
#: transition; this module owns the check. `Blocked` is raised for the two non-configurable
#: outcomes and for `inconclusive: block`; `awaiting_human` is returned for
#: `inconclusive: escalate` so the caller can route to the human queue.
ReplayOutcome = str  # "proceed" | "awaiting_human"

#: §15.3's inconclusive-row failure signatures. A gate that fails before it can run a test
#: — an import error, a collection error, a missing fixture the implementation introduced —
#: is inconclusive rather than a red phase: the test may be fine and the seam simply not
#: separable. Matched against the gate's combined output, case-insensitive.
_COLLECTION_ERROR_SIGNS: tuple[str, ...] = (
    "importerror",
    "modulenotfounderror",
    "no module named",
    "collection error",
    "cannot collect",
    "errors during collection",
    "collection_failure",
    "::error",
)

#: The gate never started. Measured 2026-08-22 in a `--clone` scratch worktree: `pnpm test`
#: with no `node_modules` exits 1 and prints "ELIFECYCLE Test failed", which the assertion
#: list below matches on the bare word `failed` — so a run where **no test executed at all**
#: was classified as a real red phase, the single worst verdict this module can produce.
#:
#: Checked before either list and dominating both, because it is not a competing signal: if
#: the runner could not start, nothing else in the output is evidence about a test. The
#: python equivalents live in the collection list; these are the ones a node stack produces.
_RUNNER_MISSING_SIGNS: tuple[str, ...] = (
    "node_modules missing",
    "cannot find module",
    "err_module_not_found",
    "command not found",
    ": not found",
    "executable not found",
)

#: The complementary signature — the test *ran* and failed an assertion. That is the red
#: phase: the test reached its assertion and the base-ref code did not satisfy it.
_ASSERTION_FAILURE_SIGNS: tuple[str, ...] = (
    "assertionerror",
    "assert ",
    "failed",
    "::failed",
    "expect(",
    ".equal(",
    ".tobetruthy",
    ".tobe(",
)

#: The test-weakening guard. A hunk in an *existing* test file that removes an assertion line
#: is the other half of the same dishonesty: making the test pass by changing the test. It is
#: a judgement call, so it escalates rather than blocks. The patterns are the shapes an
#: assertion takes in both stacks; a removed line matching one is flagged for James to read.
_ASSERTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"^\s*-\s*.*\bassert\b",
        r"^\s*-\s*.*\bself\.assert",
        r"^\s*-\s*.*\bexpect\(",
        r"^\s*-\s*.*\.(toBe|toEqual|toBeTrue|toBeFalsy|toContain|toHaveLength)\(",
        r"^\s*-\s*.*\.(equal|deepEqual|strictEqual|ok|throws)\(",
    )
)


def replay(ctx: Context) -> ReplayOutcome:
    """The red-phase replay. Called by `review.py` at the REVIEWING entry.

    Raises `Blocked` for the two non-configurable outcomes (`test-proves-nothing`,
    `behaviour-change-without-test`) and for `inconclusive: block`. Returns `"proceed"` for
    the real red phase and for the reported inconclusive / unavailable cases; returns
    `"awaiting_human"` for `inconclusive: escalate`, so the caller routes to the human queue.
    """
    behaviour_changed = _behaviour_changed(ctx)
    if not behaviour_changed:
        # No behaviour change → no red phase to require. A docs-only or config change has
        # nothing to prove about runtime behaviour, and running the replay would block a
        # clean change on `behaviour-change-without-test`.
        return "proceed"

    harness = ctx.harness
    if harness is None or not harness.tests:
        # §15.3: the `tests` key absent means the check could not be assembled. Never a pass,
        # never a block — it reports `unavailable` and the run continues. The `inconclusive`
        # policy does not apply: the check did not run, it did not fail inconclusively.
        _record(ctx, "unavailable", reason="harness.config.json declares no `tests` pathspec")
        return "proceed"

    test_gate = harness.gate_of_kind("test")
    if test_gate is None or not test_gate.run:
        _record(ctx, "unavailable", reason="no `kind: test` gate declared in harness.config.json")
        return "proceed"

    worktree = ctx.worktree
    base_ref = ctx.run.base_ref or ctx.project.base_ref
    # The one step of the clone path that cannot follow the branch home. Everything else
    # from `reviewing` onward reads the host worktree `clone.fetch_back` made, but this
    # *runs the repository's test command*, and for the project that needs `--clone` at
    # all that command needs the VM-local `node_modules`. So the scratch checkout stays
    # inside the clone, where the toolchain is. The patch below is still computed on the
    # host — the two trees agree, because one was fetched from the other.
    in_clone = ctx.project.requires_clone
    scratch = clone_step.scratch_path(ctx) if in_clone else _scratch_path(ctx)

    if in_clone:
        clone_step.scratch_add(ctx, scratch, base_ref)
    else:
        repo.add_detached_worktree(ctx.project.path, scratch, base_ref)
    try:
        patch = repo.diff_pathspec(worktree, base_ref, list(harness.tests))
        test_files = repo.added_modified_paths(worktree, base_ref, list(harness.tests))
        if not patch or not test_files:
            # behaviour_changed = true but the change touched no test file in the declared
            # set. The headline case the `test-reviewer` frame names first, and the one a
            # knob must never silence.
            _record(
                ctx,
                "fail",
                reason="behaviour-change-without-test",
                detail=f"behaviour_changed but no test file in {list(harness.tests)} changed",
            )
            raise Blocked(
                "behaviour-change-without-test",
                "the agent reported behaviour_changed but the diff touches no test file in "
                f"the declared `tests` pathspecs {list(harness.tests)}. A behaviour change "
                "needs a test that would catch its regression.",
            )

        if in_clone:
            clone_step.scratch_apply(ctx, scratch, patch)
        else:
            repo.apply_patch(scratch, patch)
        completed = ctx.sandbox.exec_sync(
            ctx.project.build_sandbox,
            list(test_gate.run),
            workdir=str(scratch),
            env=ctx.env,
            timeout=ctx.timeout_for(State.VERIFYING),
        )
        outcome, detail = _classify(completed, test_files)
        _record(ctx, _status_for(outcome), reason=outcome, detail=detail[:2000])
        if outcome == "red":
            return "proceed"  # the red phase is real; proceed to the review
        if outcome == "green":
            raise Blocked(
                "test-proves-nothing",
                "the new tests pass at the base ref without the implementation. They prove "
                "nothing about the new behaviour — this is exactly a test made to pass for "
                f"show. Gate output:\n{detail[:1000]}",
            )
        # inconclusive — the one judgement
        return _inconclusive_outcome(ctx, detail)
    finally:
        if in_clone:
            clone_step.scratch_remove(ctx, scratch)
        else:
            repo.remove_worktree(ctx.project.path, scratch, force=True)


#: `check_name` for a human's clearance of a §15.3 escalation. The `reason` column carries
#: the escalation's own rule — `test-weakening` or `redphase-inconclusive` — so one run can
#: have cleared one of them and still be stopped by the other. The row is the durable half
#: of the judgement: `factory accept` writes it, `review.start` reads it, and the PR body
#: names it, because a PR whose review ran only because a human overruled a guard should
#: say so rather than look like a run that never tripped one.
ESCALATION_ACCEPTED = "escalation_accepted"

#: The two escalations `factory accept` can clear, and the rule each is recorded under.
TEST_WEAKENING = "test-weakening"
REDPHASE_INCONCLUSIVE = "redphase-inconclusive"


def accepted(ctx: Context, rule: str) -> tuple[bool, str]:
    """`(cleared, note)` — has a human cleared `rule` for this run?

    Scoped to the run, not the attempt: the acceptance is a judgement about a diff, and a
    diff that survives into another attempt is the same diff. A re-implement that changes
    the tests again produces different hunks, and the guard flags those on their own.
    """
    for row in ctx.store.checks(ctx.run.id):
        if row["check_name"] == ESCALATION_ACCEPTED and row["reason"] == rule:
            return True, str(row["detail"] or "")
    return False, ""


def weakening_guard(ctx: Context) -> list[str]:
    """The test-weakening guard (§15.3's companion check). Returns the offending hunks, or
    empty. A diff that deletes or relaxes assertions in *existing* test files is the other
    half of making a test pass by changing the test; it is a judgement call, so the caller
    escalates to `awaiting_human` rather than blocking.

    New test files are excluded: a new test has no prior assertions to weaken, and the
    replay already covers whether it catches the regression.
    """
    if ctx.harness is None or not ctx.harness.tests:
        return []
    tests = list(ctx.harness.tests)
    worktree = ctx.worktree
    base_ref = ctx.run.base_ref or ctx.project.base_ref
    existing = _existing_test_files(worktree, base_ref, tests)
    if not existing:
        return []
    patch = repo.diff_pathspec(worktree, base_ref, tests)
    offending: list[str] = []
    for hunk in _hunks_for(patch, existing):
        for line in hunk.splitlines():
            if line.startswith("-") and any(pat.search(line) for pat in _ASSERTION_PATTERNS):
                offending.append(line[1:].strip())
    return sorted(set(offending))


# --------------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------------


def _classify(completed: Completed, test_files: list[str]) -> tuple[str, str]:
    """`(outcome, detail)` where outcome is `red` | `green` | `inconclusive`.

    - `green`: exit 0. The tests pass at the base ref — proves nothing (test-proves-nothing).
    - `red`: the gate failed on an assertion in a new/changed test. The red phase is real.
    - `inconclusive`: the gate never started (the runner is not installed), or it failed
      before it could run a test (import / collection error), or the failure kind could not
      be read. The one judgement; reported, not blocked.

    This is the layer P0-14 calibrates: the inconclusive default is `report`, so a
    misclassification here is a noisy PR body, not a wrong transition. The two mechanical
    rows (`green`, no-tests) are decided before this is reached and are not heuristics.
    """
    output = f"{completed.stdout}\n{completed.stderr}".lower()
    detail = completed.stdout.strip() or completed.stderr.strip()
    if completed.returncode == 0:
        return "green", detail
    if any(sign in output for sign in _RUNNER_MISSING_SIGNS):
        # The runner never started, so there is no test result to read either way. This is
        # checked ahead of the two lists rather than alongside them: a lifecycle error says
        # "Test failed", and letting that compete with the assertion signal is how a run
        # with zero executed tests came back as a real red phase.
        return "inconclusive", detail
    has_collection = any(sign in output for sign in _COLLECTION_ERROR_SIGNS)
    has_assertion = any(sign in output for sign in _ASSERTION_FAILURE_SIGNS)
    if has_assertion and not has_collection:
        return "red", detail
    if has_collection and not has_assertion:
        return "inconclusive", detail
    if has_assertion and has_collection:
        # A real assertion failure dominates a collection error elsewhere: a partial impl
        # can both fail a new assertion and break collection of an unrelated test.
        return "red", detail
    # Could not read the failure kind. Conservative: do not claim a red phase we did not see.
    return "inconclusive", detail


def _status_for(outcome: str) -> str:
    return {"red": "pass", "green": "fail", "inconclusive": "warn"}[outcome]


# --------------------------------------------------------------------------------
# the inconclusive judgement
# --------------------------------------------------------------------------------


def _inconclusive_outcome(ctx: Context, detail: str) -> ReplayOutcome:
    """Apply the per-project `redphase.inconclusive` policy to an inconclusive replay.

    `report` (default) returns `"proceed"` — the PR body carries the inconclusive result so a
    usually-inconclusive replay is visible, not rounded to green. `escalate` returns
    `"awaiting_human"` so the caller routes to the human queue (`reviewing -> awaiting_human`
    is legal). `block` raises `Blocked`.
    """
    mode = ctx.registry.defaults.redphase.inconclusive
    ctx.log(
        "redphase.inconclusive",
        level="warning",
        mode=mode,
        detail=detail[:500],
    )
    if mode == "block":
        raise Blocked(
            "redphase-inconclusive",
            "the red-phase replay was inconclusive and `redphase.inconclusive` is `block`. "
            f"Detail:\n{detail[:1000]}",
        )
    if mode == "escalate":
        return "awaiting_human"
    return "proceed"


# --------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------


def _behaviour_changed(ctx: Context) -> bool:
    """Read `behaviour_changed` from the implement result, the same evidence verify saw."""
    attempt_dir = AttemptDir(ctx.factory_dir / "run" / str(ctx.run.attempt))
    if not attempt_dir.last_message.exists():
        # No implement result is a schema-invalid shape verify would already have caught;
        # treat as no behaviour change rather than crashing the review before it starts.
        return False
    try:
        payload = json.loads(attempt_dir.last_message.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return bool(payload.get("behaviour_changed"))


def _scratch_path(ctx: Context) -> Path:
    return ctx.project.worktree_path(
        ctx.registry.defaults.worktree_subdir, f"scratch-{ctx.run.id}-{ctx.run.attempt}"
    )


def _existing_test_files(worktree: Path, base_ref: str, tests: list[str]) -> set[str]:
    """Test files that existed at the base ref — the weakening guard's scope.

    A new test file has no prior assertions to weaken; the replay covers whether it catches
    the regression. Only modifications to files the base ref already knew about can weaken an
    existing assertion.
    """
    return set(repo.paths_at_ref(worktree, base_ref, tests))


def _hunks_for(patch: str, files: set[str]) -> list[str]:
    """The diff hunks touching `files`, split on `diff --git` headers."""
    if not patch:
        return []
    hunks: list[str] = []
    current: list[str] = []
    target = False
    for line in patch.splitlines():
        if line.startswith("diff --git"):
            if current and target:
                hunks.append("\n".join(current))
            current = [line]
            # `diff --git a/tests/x.py b/tests/x.py` → the path is the second pair.
            target = any(f in line for f in files)
        else:
            current.append(line)
    if current and target:
        hunks.append("\n".join(current))
    return hunks


def _record(
    ctx: Context,
    status: str,
    *,
    reason: str = "",
    detail: str = "",
) -> None:
    """One `checks` row per replay (§15.3): status, reason, the gate output tail."""
    ctx.store.record_check(
        ctx.run.id,
        ctx.run.attempt,
        "redphase",
        status,
        reason=reason or None,
        detail=detail[:2000] or None,
    )
