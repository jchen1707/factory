"""One declared skill list, end to end: built, mounted, launched, named in the prompt, attested."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from factory import doctrine
from factory.machine import Blocked, State
from factory.sandbox.base import Workspace
from factory.steps import Context
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import implement as implement_step
from factory.steps import sandbox as sandbox_step
from factory.steps import worktree as worktree_step
from tests.integration.conftest import LAYER_A_SETTINGS, FakeSandbox, advance_state, git
from tests.support import plugin_cache

DECLARATION = """
[sources.mattpocock-skills]
marketplace = "claude-plugins-official"
version     = "1.2.3"
skills      = ["tdd", "code-review"]
"""
TARGET_PLUGIN = "mattpocock-skills@claude-plugins-official"


@pytest.fixture
def declared(ctx: Context, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Context:
    (ctx.home / "config" / "doctrine.toml").write_text(DECLARATION)
    monkeypatch.setattr(
        implement_step, "PLUGIN_CACHE", plugin_cache.mattpocock(tmp_path / "plugin-cache")
    )
    return ctx


def _fake(ctx: Context) -> FakeSandbox:
    assert isinstance(ctx.sandbox, FakeSandbox)
    return ctx.sandbox


def _to_verifying(ctx: Context) -> None:
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)
    advance_state(ctx, until=State.VERIFYING)


def _enable_on_the_target(project_repo: Path) -> None:
    settings = {**LAYER_A_SETTINGS, "enabledPlugins": {TARGET_PLUGIN: True}}
    (project_repo / ".claude" / "settings.json").write_text(json.dumps(settings))
    git(project_repo, "commit", "-qam", "the target enables a plugin of its own")
    git(project_repo, "push", "-q", "origin", "v2")


def test_the_builder_loads_the_declared_skills_and_nothing_the_target_enables(
    declared: Context, project_repo: Path
) -> None:
    ctx = declared
    _enable_on_the_target(project_repo)
    _to_verifying(ctx)

    launch = _fake(ctx).launches[0]
    assert launch is not None
    [plugin_dir] = launch.plugin_dirs
    assert plugin_dir.parent == doctrine.root(ctx.home)
    assert sorted(p.name for p in (plugin_dir / "skills").iterdir()) == ["code-review", "tdd"]
    assert launch.settings == {"enabledPlugins": {TARGET_PLUGIN: False}}
    assert Workspace(doctrine.root(ctx.home), readonly=True) in _fake(ctx).created[0].workspaces
    prompt = launch.prompt.read_text()
    assert "`doctrine:tdd`, `doctrine:code-review`" in prompt
    checks = {row["check_name"]: row["status"] for row in ctx.store.checks(ctx.run.id)}
    assert checks["preflight:doctrine-mounted"] == "pass"
    assert checks["isolation_attestation"] == "pass"


def test_no_declaration_launches_no_plugin_and_names_no_skill(ctx: Context) -> None:
    _to_verifying(ctx)

    launch = _fake(ctx).launches[0]
    assert launch is not None
    assert launch.plugin_dirs == ()
    assert launch.settings is None
    assert "Skill tool" not in launch.prompt.read_text()


def test_a_sandbox_made_before_the_doctrine_mount_is_refused_before_any_launch(
    declared: Context,
) -> None:
    # `ensure` attaches to an existing sandbox as it is, and the CLI drops a `--plugin-dir`
    # it cannot see without a word; the preflight is what notices, before any spend.
    ctx = declared
    claim_step.run(ctx)
    context_step.run(ctx)
    spec = sandbox_step.build_spec(ctx)
    _fake(ctx).created.append(
        dataclasses.replace(
            spec,
            workspaces=tuple(w for w in spec.workspaces if w.path != doctrine.root(ctx.home)),
        )
    )

    with pytest.raises(Blocked) as caught:
        sandbox_step.run(ctx)

    assert caught.value.reason == "enforcement-disabled"
    assert "doctrine-mounted" in caught.value.detail
    assert "predates the doctrine mount" in caught.value.detail
    assert _fake(ctx).launches == []


def test_a_declared_skill_the_cache_lacks_blocks_before_launch(
    declared: Context, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = declared
    monkeypatch.setattr(
        implement_step, "PLUGIN_CACHE", plugin_cache.mattpocock(tmp_path / "thin", ("tdd",))
    )

    with pytest.raises(Blocked) as caught:
        _to_verifying(ctx)

    assert caught.value.reason == "doctrine-invalid"
    assert "has no skill code-review" in caught.value.detail
    assert _fake(ctx).launches == []
