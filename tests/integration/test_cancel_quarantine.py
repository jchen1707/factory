from __future__ import annotations

from pathlib import Path

import pytest

from factory import cli, learning
from factory.delivery import github
from factory.machine import Blocked, State
from factory.steps import Context
from factory.steps import block as block_step
from factory.steps import deliver as deliver_step
from tests.integration.test_phase3 import _to_pr_ready
from tests.integration.test_pipeline import _fake


def test_cancel_after_a_secret_in_artifact_block_quarantines_the_attempt(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "ghp_" + "A" * 36  # synthetic scanner hit, never a real credential
    monkeypatch.setattr(deliver_step.repo, "changed_paths", lambda wt, br: ["src/app/main.py"])
    monkeypatch.setattr(github, "push", lambda wt, b: None)
    monkeypatch.setattr(github, "find_pr", lambda wt, b: None)
    monkeypatch.setattr(
        github, "create_pr", lambda wt, **kw: "https://github.com/jchen1707/python-harness/pull/7"
    )
    monkeypatch.setattr(cli, "SbxAdapter", lambda: _fake(ctx))
    _to_pr_ready(ctx, monkeypatch)
    attempts = ctx.factory_dir / "run"
    leaked = attempts / str(ctx.run.attempt)
    (leaked / "leak.txt").write_text(f"token {secret}\n")
    (leaked / "events.jsonl").write_text("{}\n")
    clean = attempts / "0"
    clean.mkdir()
    (clean / "notes.txt").write_text("nothing secret here\n")

    with pytest.raises(Blocked) as caught:
        deliver_step.run(ctx)
    assert caught.value.reason == "secret-in-artifact"
    block_step.record(ctx, caught.value.reason, caught.value.detail)
    worktree = Path(ctx.run.worktree or "")
    scheduled: list[Path] = []
    monkeypatch.setattr(learning, "schedule", lambda project, vault, path: scheduled.append(path))

    lines = cli._cancel_run(ctx.home, ctx.registry, ctx.store, ctx.linear, ctx.run, "abandon")

    ctx.refresh()
    assert ctx.state is State.CANCELLED
    assert not worktree.exists()
    shared = ctx.home / "artifacts"
    assert not [p for p in shared.rglob("*") if p.is_file() and secret in p.read_text()]
    assert (shared / ctx.run.linear_id / ctx.run.id / "0" / "notes.txt").is_file()
    quarantined = ctx.state_dir / "quarantine" / leaked.name / "leak.txt"
    assert secret in quarantined.read_text()
    assert any("github-token" in line and str(quarantined.parent) in line for line in lines)
    assert not any(line.startswith(f"archived {leaked.name} ") for line in lines)
    assert not [path for path in scheduled if quarantined.parent in path.parents]
