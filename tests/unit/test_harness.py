"""§21.1 — reading layer B's config, and the stack cross-check."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from factory.harness import cross_check_stack, load_harness_config, unwired_hooks, vendor_check
from factory.machine import Blocked

HOME = Path(__file__).resolve().parents[2]
HARNESS = HOME.parent / "harness"
PYTHON_HARNESS = Path("/Users/james/python-harness")
FRONTEND_HARNESS = Path("/Users/james/frontend-harness")


def test_parses_the_factorys_own_config() -> None:
    config = load_harness_config(HOME)
    assert config.name == "factory"
    # §24.1: the factory files no ticket, so it carries no team key.
    assert config.team is None
    assert {gate.kind for gate in config.gates} == {"lint", "format", "types", "test"}


@pytest.mark.skipif(not PYTHON_HARNESS.exists(), reason="python-harness is not cloned here")
def test_parses_the_real_python_harness_config() -> None:
    config = load_harness_config(PYTHON_HARNESS)
    assert config.team == "BAC"
    integration = config.gate_of_kind("integration")
    assert integration is not None
    # The two fields the plan says the factory must surface and the Stop hook never
    # prints: `when` and `caveat`.
    assert integration.when
    assert integration.caveat
    cross_check_stack(config, "python")


@pytest.mark.skipif(not FRONTEND_HARNESS.exists(), reason="frontend-harness is not cloned here")
def test_parses_the_real_frontend_harness_config() -> None:
    config = load_harness_config(FRONTEND_HARNESS)
    assert config.team == "FRO"
    cross_check_stack(config, "frontend")


@pytest.mark.skipif(not PYTHON_HARNESS.exists(), reason="python-harness is not cloned here")
def test_a_stack_mismatch_blocks() -> None:
    config = load_harness_config(PYTHON_HARNESS)
    with pytest.raises(Blocked) as caught:
        cross_check_stack(config, "frontend")
    assert caught.value.reason == "stack-mismatch"


def test_a_config_declaring_neither_gates_nor_apps_is_refused(tmp_path: Path) -> None:
    (tmp_path / "harness.config.json").write_text(json.dumps({"name": "empty"}))
    with pytest.raises(Blocked) as caught:
        load_harness_config(tmp_path)
    assert caught.value.reason == "harness-config-declares-nothing"


def test_a_missing_config_blocks(tmp_path: Path) -> None:
    with pytest.raises(Blocked) as caught:
        load_harness_config(tmp_path)
    assert caught.value.reason == "no-harness-config"


def test_an_absent_enabled_key_means_the_gate_runs(tmp_path: Path) -> None:
    # Every config written before layer A 0.9.0 omits the key, and every one of them
    # declared gates that run. The default must say so, or a sync would silently switch
    # a repository's whole Definition of Done off.
    (tmp_path / "harness.config.json").write_text(
        json.dumps({"name": "x", "gates": [{"name": "ruff", "kind": "lint", "run": ["uv"]}]})
    )
    assert load_harness_config(tmp_path).gates[0].enabled is True


def test_enabled_false_is_carried_off_the_config(tmp_path: Path) -> None:
    (tmp_path / "harness.config.json").write_text(
        json.dumps(
            {
                "name": "x",
                "gates": [
                    {"name": "ruff", "kind": "lint", "run": ["uv"]},
                    {
                        "name": "lighthouse",
                        "kind": "integration",
                        "run": ["pnpm"],
                        "enabled": False,
                    },
                ],
            }
        )
    )
    gates = load_harness_config(tmp_path).gates
    assert [g.enabled for g in gates] == [True, False]


@pytest.mark.skipif(not FRONTEND_HARNESS.exists(), reason="frontend-harness is not cloned here")
def test_the_real_frontend_harness_lighthouse_gate_is_switched_off() -> None:
    # Not a tautology against the fixture above: this reads the config the factory will
    # actually hand to a run, and it is the one place the two are checked to agree.
    # If lighthouse is switched back on, this test is the thing that says so.
    config = load_harness_config(FRONTEND_HARNESS)
    lighthouse = next(g for g in config.gates if g.name == "lighthouse")
    assert lighthouse.enabled is False
    # `when` and `caveat` are kept deliberately while it is off — they are the record of
    # what the gate would check and how it lies.
    assert lighthouse.when
    assert lighthouse.caveat


def test_an_absent_tests_key_yields_an_empty_tuple() -> None:
    # Layer A gains `tests` in Phase 2. Until then the red-phase replay must report
    # `unavailable`, which it can only do if this is empty rather than guessed.
    assert load_harness_config(HOME).tests == ()


def test_the_canary_target_is_a_literal_path() -> None:
    protected = load_harness_config(HOME).first_protected_glob()
    assert protected is not None
    assert "*" not in protected.glob


@pytest.mark.skipif(
    not HARNESS.exists() and not os.environ.get("CI"),
    reason="no harness checkout beside this one; CI clones it there, so CI still runs this",
)
def test_the_factorys_own_vendored_tree_is_intact() -> None:
    ok, detail = vendor_check(HOME, HARNESS / "scripts" / "vendor_sync.py")
    assert ok, detail


def _protect(matcher: str | None, command: Mapping[str, object]) -> dict[str, object]:
    group: dict[str, object] = {"hooks": [{"type": "command", **command}]}
    if matcher is not None:
        group["matcher"] = matcher
    return group


VERIFY = {"Stop": [{"hooks": [{"type": "command", "command": "node", "args": ["verify.mjs"]}]}]}
ARGS_FORM = {"command": "node", "args": ["${CLAUDE_PROJECT_DIR}/hooks/protect_paths.mjs"]}


def test_the_factorys_own_settings_wire_the_enforcing_hooks() -> None:
    assert unwired_hooks((HOME / ".claude" / "settings.json").read_text()) == []


@pytest.mark.parametrize(
    "protect",
    [
        _protect("Read|Edit|Write|NotebookEdit|Bash", ARGS_FORM),
        _protect(None, ARGS_FORM),
        _protect("*", {"command": 'node "${CLAUDE_PLUGIN_ROOT}/hooks/protect_paths.mjs"'}),
    ],
    ids=["args-form", "no-matcher", "command-string-form"],
)
def test_a_guard_over_every_writing_tool_and_the_stop_gate_are_wired(
    protect: dict[str, object],
) -> None:
    settings = {"hooks": {"PreToolUse": [protect], **VERIFY}}
    assert unwired_hooks(json.dumps(settings)) == []


@pytest.mark.parametrize(
    ("settings", "problem"),
    [
        (None, ".claude/settings.json is absent"),
        ("{not json", ".claude/settings.json is not JSON"),
        (json.dumps({"hooks": VERIFY}), "PreToolUse does not run protect_paths.mjs"),
        (
            json.dumps({"hooks": {"PreToolUse": [_protect("Bash", ARGS_FORM)]}}),
            "Stop does not run verify.mjs",
        ),
        (
            json.dumps({"hooks": {"PreToolUse": [_protect("Read|Bash", ARGS_FORM)], **VERIFY}}),
            "PreToolUse protect_paths.mjs does not match Edit, Write",
        ),
        (
            json.dumps({"hooks": {"PreToolUse": [_protect("(", ARGS_FORM)], **VERIFY}}),
            "PreToolUse protect_paths.mjs does not match Bash, Edit, Read, Write",
        ),
    ],
    ids=["absent", "not-json", "no-guard", "no-stop-gate", "guard-misses-writes", "bad-regex"],
)
def test_an_unenforced_settings_file_says_what_is_missing(
    settings: str | None, problem: str
) -> None:
    assert any(found.startswith(problem) for found in unwired_hooks(settings))


@pytest.mark.parametrize("app", ["../outside", "/outside", "."])
def test_config_tree_refuses_escaping_or_recursive_apps(tmp_path: Path, app: str) -> None:
    from factory.harness import config_tree

    (tmp_path / "harness.config.json").write_text(json.dumps({"apps": [app]}))
    with pytest.raises(Blocked) as caught:
        config_tree(load_harness_config(tmp_path), tmp_path)
    assert caught.value.reason == "invalid-app-path"


def test_config_tree_refuses_symlink_escape_before_reading_child(tmp_path: Path) -> None:
    from factory.harness import config_tree

    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "harness.config.json").write_text("not even valid JSON")
    (root / "child").symlink_to(outside, target_is_directory=True)
    (root / "harness.config.json").write_text(json.dumps({"apps": ["child"]}))
    with pytest.raises(Blocked) as caught:
        config_tree(load_harness_config(root), root)
    assert caught.value.reason == "invalid-app-path"
