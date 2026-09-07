"""Git status columns survive handoff collection in bind and clone layouts."""

import json

import pytest

from factory import handoffs, repo
from factory.steps import Context
from tests.integration.test_clone import _fake, _to_worktree_ready


@pytest.mark.parametrize("fixture", ["ctx", "clone_ctx"])
def test_handoff_preserves_unstaged_status_column_and_work(
    request: pytest.FixtureRequest,
    fixture: str,
) -> None:
    ctx: Context = request.getfixturevalue(fixture)
    _to_worktree_ready(ctx)
    work = (
        _fake(ctx).clone_dir(ctx.project.build_sandbox)
        if ctx.project.requires_clone
        else ctx.worktree
    )
    assert work is not None
    (work / "README.md").write_text("# Unstaged work to preserve\n")
    status = repo._git_raw(work, "status", "--porcelain")
    assert status.startswith(" M README.md\n")
    before_diff = repo._git_raw(work, "diff", "HEAD")
    head = repo._git(work, "rev-parse", "HEAD")
    target = ctx.factory_dir / "handoff.json"

    handoffs.write(ctx, target)

    payload = json.loads(target.read_text())
    assert payload["dirty_work"] == status.splitlines()
    assert payload["commit"] == head
    assert repo._git_raw(work, "diff", "HEAD") == before_diff
