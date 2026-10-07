"""BAC-65: a shared build sandbox without the authority mount is refused before any agent runs."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from factory import authority
from factory.machine import State
from factory.sandbox.base import Completed
from factory.sandbox.sbx import SbxAdapter, SbxError
from factory.steps import Context
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import sandbox as sandbox_step
from tests.integration.conftest import FakeSandbox


@pytest.mark.parametrize("authority_listed", [False, True])
def test_build_sandbox_without_the_authority_mount_is_refused(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, authority_listed: bool
) -> None:
    source = Path(__file__).parents[2] / ".agents/vendor/harness"
    for name in ("hooks/delivery_policy.mjs", "docs/agents/delivery-review.md"):
        target = ctx.project.path / ".agents/vendor/harness" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    config_path = ctx.project.path / "harness.config.json"
    config = json.loads(config_path.read_text())
    config["delivery"] = {
        "default": "core",
        "requirements": {},
        "profiles": {"core": {"required": [], "deferrals": []}},
    }
    config_path.write_text(json.dumps(config))
    claim_step.run(ctx)
    context_step.run(ctx)
    spec = sandbox_step.build_spec(ctx)
    required = f"{authority.mount(ctx)}:ro"
    assert required in [w.as_argument() for w in spec.workspaces]
    listed = [
        w.as_argument() for w in spec.workspaces if authority_listed or w.as_argument() != required
    ]

    adapter = SbxAdapter()

    def run(
        argv: Sequence[str], *, timeout: int | None = None, stdin: str | None = None
    ) -> Completed:
        if list(argv) == ["sbx", "inspect", spec.name]:
            return Completed(tuple(argv), 0, "", "")
        if list(argv) == ["sbx", "inspect", spec.name, "--json"]:
            return Completed(tuple(argv), 0, json.dumps({"workspace": str(ctx.project.path)}), "")
        assert list(argv) == ["sbx", "ls", "--json"], f"unexpected call {argv}"
        listing = {"sandboxes": [{"name": spec.name, "workspaces": listed}]}
        return Completed(tuple(argv), 0, json.dumps(listing), "")

    monkeypatch.setattr(adapter, "_run", run)
    assert isinstance(ctx.sandbox, FakeSandbox)
    monkeypatch.setattr(ctx.sandbox, "ensure", adapter.ensure)
    if authority_listed:
        sandbox_step.run(ctx)
        assert ctx.state is State.SANDBOX_READY
    else:
        with pytest.raises(SbxError, match=re.escape(required)):
            sandbox_step.run(ctx)
        assert ctx.state is State.SANDBOX_CREATING
        assert not ctx.sandbox.sync_calls
