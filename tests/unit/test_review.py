"""Unit tests for the review step's pure decisions (§15.2).

The Tier-2 trigger table and the prompt assembly are the parts that must not drift from the
spec; both are pure, so they are tested here without a context, a sandbox or a model call.
The state-machine transitions are exercised in `tests/integration/test_pipeline.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.harness import HarnessConfig
from factory.machine import Blocked
from factory.steps.review import _axis_prompt, _decide_tier2, _matches_any

# -- _decide_tier2 (§15.2's trigger table) -------------------------------------


def test_a_small_clean_change_skips_tier2_and_names_the_rule() -> None:
    assert (
        _decide_tier2(
            paths=["src/app.py"],
            lines=40,
            protected_hits=[],
            sensitive=("src/app/ai/**",),
            tier1_has_human=False,
            bug_without_test=False,
        )
        == "no-trigger"
    )


def test_ten_or_more_files_triggers_tier2() -> None:
    assert (
        _decide_tier2(
            paths=[f"src/f{i}.py" for i in range(10)],
            lines=10,
            protected_hits=[],
            sensitive=(),
            tier1_has_human=False,
            bug_without_test=False,
        )
        is None
    )


def test_four_hundred_changed_lines_triggers_tier2() -> None:
    assert (
        _decide_tier2(
            paths=["src/app.py"],
            lines=400,
            protected_hits=[],
            sensitive=(),
            tier1_has_human=False,
            bug_without_test=False,
        )
        is None
    )


def test_a_touch_of_a_protected_path_triggers_tier2() -> None:
    assert (
        _decide_tier2(
            paths=["harness.config.json"],
            lines=2,
            protected_hits=["harness.config.json"],
            sensitive=(),
            tier1_has_human=False,
            bug_without_test=False,
        )
        is None
    )


def test_a_touch_of_a_sensitive_dir_triggers_tier2() -> None:
    assert (
        _decide_tier2(
            paths=["src/app/ai/retrieval.py"],
            lines=20,
            protected_hits=[],
            sensitive=("src/app/ai/**",),
            tier1_has_human=False,
            bug_without_test=False,
        )
        is None
    )


def test_a_tier1_critical_or_high_finding_triggers_tier2() -> None:
    assert (
        _decide_tier2(
            paths=["src/app.py"],
            lines=20,
            protected_hits=[],
            sensitive=(),
            tier1_has_human=True,
            bug_without_test=False,
        )
        is None
    )


def test_a_bug_labelled_fix_with_no_test_triggers_tier2() -> None:
    assert (
        _decide_tier2(
            paths=["src/app.py"],
            lines=20,
            protected_hits=[],
            sensitive=(),
            tier1_has_human=False,
            bug_without_test=True,
        )
        is None
    )


# -- _matches_any (the sensitive-dir glob) ------------------------------------


def test_matches_any_handles_the_directory_crossing_star_star() -> None:
    assert _matches_any("src/app/ai/retrieval/ingestion.py", ("src/app/ai/**",))
    assert not _matches_any("src/app/service.py", ("src/app/ai/**",))
    assert _matches_any("src/web/routes/index.tsx", ("src/**/routes/**",))


# -- _axis_prompt (mirrors full-review.js axisPrompt) -------------------------


def _harness(agent_dir: str, checklist_dir: str) -> HarnessConfig:
    return HarnessConfig(
        root=Path("/repo"),
        name="python-harness",
        team="BAC",
        gates=(),
        protected=(),
        gated_paths=(),
        gated_files=(),
        secret_vars=(),
        apps=(),
        tests=(),
        review_agent_dir=agent_dir,
        review_checklist_dir=checklist_dir,
    )


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_axis_prompt_concatenates_frame_and_checklist(tmp_path: Path) -> None:
    vendored = tmp_path / ".agents/vendor/harness/agents"
    _write(vendored / "standards-reviewer.md", "---\nname: x\n---\n# Frame\n\nframe body")
    checklist = tmp_path / "docs/agents/subagents"
    _write(checklist / "standards-reviewer.md", "checklist body")
    prompt = _axis_prompt(
        tmp_path, _harness(".agents/agents", "docs/agents/subagents"), "standards-reviewer"
    )
    assert "frame body" in prompt
    assert "checklist body" in prompt
    # the separator axisPrompt adds between frame and checklist
    assert "what to look for, in this repo's terms" in prompt


def test_axis_prompt_returns_frame_only_when_no_checklist(tmp_path: Path) -> None:
    # spec-checker carries its whole review in the frame and has no checklist.
    vendored = tmp_path / ".agents/vendor/harness/agents"
    _write(vendored / "spec-checker.md", "frame only body")
    prompt = _axis_prompt(
        tmp_path, _harness(".agents/agents", "docs/agents/subagents"), "spec-checker"
    )
    assert prompt == "frame only body"


def test_axis_prompt_stack_override_wins_over_vendored_frame(tmp_path: Path) -> None:
    own = tmp_path / ".agents/agents"
    _write(own / "standards-reviewer.md", "stack override frame")
    vendored = tmp_path / ".agents/vendor/harness/agents"
    _write(vendored / "standards-reviewer.md", "shared frame")
    h = _harness(".agents/agents", "docs/agents/subagents")
    assert "stack override frame" in _axis_prompt(tmp_path, h, "standards-reviewer")
    assert "shared frame" not in _axis_prompt(tmp_path, h, "standards-reviewer")


def test_axis_prompt_throws_when_both_halves_are_absent(tmp_path: Path) -> None:
    with pytest.raises(Blocked) as caught:
        _axis_prompt(
            tmp_path, _harness(".agents/agents", "docs/agents/subagents"), "standards-reviewer"
        )
    assert caught.value.reason == "review-frame-missing"
