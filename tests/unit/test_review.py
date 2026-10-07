"""Unit tests for the review step's pure decisions (§15.2).

The Tier-2 trigger table and the prompt assembly are the parts that must not drift from the
spec; both are pure, so they are tested here without a context, a sandbox or a model call.
The state-machine transitions are exercised in `tests/integration/test_pipeline.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from factory.agent import stream
from factory.harness import HarnessConfig
from factory.machine import Blocked
from factory.steps import review
from factory.steps.review import (
    _axis_prompt,
    _decide_tier2,
    _matches_any,
    _review_scratch,
    _review_spec,
)
from tests.support import claude_stream

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


def test_the_override_runs_tier2_on_a_diff_no_rule_would_have_triggered() -> None:
    # The same diff as the case above, which skips. `--full-review` is an override, not
    # a seventh rule: it does not describe the diff, it says a human wants the fan-out.
    assert (
        _decide_tier2(
            paths=["src/app.py"],
            lines=40,
            protected_hits=[],
            sensitive=("src/app/ai/**",),
            tier1_has_human=False,
            bug_without_test=False,
            forced=True,
        )
        is None
    )


def test_the_override_is_off_unless_it_is_asked_for() -> None:
    # `forced` defaults to False, so every existing caller and every existing case in
    # this table keeps the verdict it had. An override that crept in by default would
    # spend the fan-out's model budget on every run.
    assert (
        _decide_tier2(
            paths=["src/app.py"],
            lines=40,
            protected_hits=[],
            sensitive=(),
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
        tmp_path,
        _harness(".agents/agents", "docs/agents/subagents"),
        "standards-reviewer",
        "origin/v2",
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
        tmp_path, _harness(".agents/agents", "docs/agents/subagents"), "spec-checker", "origin/v2"
    )
    # The frame, plus the two lines that name the target — never a review criterion.
    assert prompt.startswith("frame only body")
    assert "git diff origin/v2...HEAD" in prompt
    assert "Do not run `git show`, `git log -p`" in prompt
    assert "must not enter the review transcript" in prompt
    assert "An empty findings list is a valid" in prompt


def test_axis_prompt_stack_override_wins_over_vendored_frame(tmp_path: Path) -> None:
    own = tmp_path / ".agents/agents"
    _write(own / "standards-reviewer.md", "stack override frame")
    vendored = tmp_path / ".agents/vendor/harness/agents"
    _write(vendored / "standards-reviewer.md", "shared frame")
    h = _harness(".agents/agents", "docs/agents/subagents")
    assert "stack override frame" in _axis_prompt(tmp_path, h, "standards-reviewer", "origin/v2")
    assert "shared frame" not in _axis_prompt(tmp_path, h, "standards-reviewer", "origin/v2")


def test_axis_prompt_throws_when_both_halves_are_absent(tmp_path: Path) -> None:
    with pytest.raises(Blocked) as caught:
        _axis_prompt(
            tmp_path,
            _harness(".agents/agents", "docs/agents/subagents"),
            "standards-reviewer",
            "origin/v2",
        )
    assert caught.value.reason == "review-frame-missing"


def _ctx_for_argv() -> Any:
    """Existing routing with no operator preset override."""
    role = SimpleNamespace(model="gpt-5.6-sol", effort="high")
    return SimpleNamespace(
        routing=SimpleNamespace(role=lambda _name: role),
        project=SimpleNamespace(name="p"),
        run=SimpleNamespace(id="r"),
        store=SimpleNamespace(runtime=SimpleNamespace(effective=lambda *_: {})),
    )


def _ctx_for_spec(tmp_path: Path, *, run_id: str, ticket: str) -> Any:
    """The four things `_review_spec` and `_review_scratch` read off a context."""
    project = SimpleNamespace(
        name="python-harness",
        path=tmp_path / "python-harness",
        review_sandbox="factory-review-python-harness",
        template="",
    )
    return SimpleNamespace(
        home=tmp_path / "factory",
        project=project,
        run=SimpleNamespace(id=run_id, linear_id=ticket),
        worktree=project.path / ".factory/worktrees" / ticket,
        registry=SimpleNamespace(defaults=SimpleNamespace(deny_network=("mcp.linear.app",))),
        store=SimpleNamespace(
            runtime=SimpleNamespace(policy=lambda _: None, settings=lambda *args: {})
        ),
    )


def test_the_review_sandbox_spec_does_not_move_between_runs(tmp_path: Path) -> None:
    """§9.1 fixes a sandbox's workspace set at creation and the reviewer is named once per
    project, so nothing in its spec may carry a run id or a ticket. Two contexts differing
    only in those must produce the identical workspace set.

    BAC-4 measured the failure: the sandbox created on run `1effc543d83a459a`'s own review
    directory was refused for run `73f500d22e894d9a`, which is `_assert_spec_matches` doing
    its job against a spec that should never have varied.
    """
    first = _ctx_for_spec(tmp_path, run_id="1effc543d83a459a", ticket="BAC-4")
    second = _ctx_for_spec(tmp_path, run_id="73f500d22e894d9a", ticket="BAC-9")

    one = _review_spec(first, _review_scratch(first))
    two = _review_spec(second, _review_scratch(second))

    assert [w.as_argument() for w in one.workspaces] == [w.as_argument() for w in two.workspaces]
    # And the shape is what sbx will accept: writable scratch first, code read-only after.
    assert not one.workspaces[0].readonly
    assert one.workspaces[1].readonly
    assert str(one.workspaces[1].path) == str(tmp_path / "python-harness")


# -- the findings file is the host's rendering of the reviewer's answer ------


def _run(lines: list[str], *, exit_code: int | None = 0) -> stream.Run:
    return stream.fold(stream.events(lines), exit_code=exit_code)


def test_findings_are_written_from_the_structured_answer(tmp_path: Path) -> None:
    out = tmp_path / "review-standards.json"
    answer = {"findings": [{"file": "a.py", "line": 3, "severity": "low", "summary": "s"}]}
    run = _run(claude_stream.success(answer).lines)

    findings = review._validated_findings(_ctx_with_schema(tmp_path), run, out, "standards")

    assert findings == answer["findings"]
    assert json.loads(out.read_text()) == answer


def test_a_completed_review_with_no_structured_answer_blames_the_schema(tmp_path: Path) -> None:
    """A schema the model cannot satisfy ends `success` with `structured_output: null`
    (measured `schema-unsatisfiable`), and that is not a review that found nothing."""
    out = tmp_path / "review-spec.json"
    run = _run(claude_stream.success(None, text="no answer").lines)

    with pytest.raises(Blocked) as caught:
        review._validated_findings(_ctx_with_schema(tmp_path), run, out, "spec")

    assert caught.value.reason == "review-schema-invalid"
    assert not out.exists()


def test_an_answer_outside_the_findings_schema_blocks(tmp_path: Path) -> None:
    out = tmp_path / "review-standards.json"
    run = _run(claude_stream.success({"findings": "not a list"}).lines)

    with pytest.raises(Blocked) as caught:
        review._validated_findings(_ctx_with_schema(tmp_path), run, out, "standards")

    assert caught.value.reason == "review-schema-invalid"
    assert out.exists()  # the raw answer is kept as evidence either way


def _ctx_with_schema(tmp_path: Path) -> Any:
    schema = tmp_path / review._FINDINGS_SCHEMA
    schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_text(json.dumps({"type": "object", "properties": {"findings": {"type": "array"}}}))
    return SimpleNamespace(
        store=SimpleNamespace(runtime=SimpleNamespace(policy=lambda _: None)),
        worktree=tmp_path,
        run=SimpleNamespace(id="r"),
    )
