"""BAC-65: a shared build sandbox without the authority mount is refused before any agent runs."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from factory import authority
from factory.machine import Blocked, State
from factory.sandbox import authority_probe
from factory.sandbox.base import Completed
from factory.sandbox.sbx import SbxAdapter, SbxError
from factory.steps import Context
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import sandbox as sandbox_step
from tests.integration.conftest import FakeSandbox


def _capture_authority(ctx: Context) -> None:
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


@pytest.mark.parametrize("authority_listed", [False, True])
def test_build_sandbox_without_the_authority_mount_is_refused(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, authority_listed: bool
) -> None:
    _capture_authority(ctx)
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
        assert _authority_check(ctx)["status"] == "pass"
    else:
        with pytest.raises(SbxError, match=re.escape(required)):
            sandbox_step.run(ctx)
        assert ctx.state is State.SANDBOX_CREATING
        assert not ctx.sandbox.sync_calls


def _authority_check(ctx: Context) -> dict[str, str]:
    [row] = [
        r
        for r in ctx.store.checks(ctx.run.id)
        if r["check_name"] == "preflight:authority-read-only"
    ]
    return dict(row)


@pytest.mark.parametrize(
    ("writes", "named"),
    [
        ({"harness.config.json": "writable"}, "harness.config.json: writable"),
        ({".claude/": "writable"}, ".claude/: writable"),
        # A mode-0444 file on a `:ro` share answers this; it says nothing about the mount.
        ({".claude/settings.json": "EACCES"}, ".claude/settings.json: EACCES"),
    ],
    ids=["writable-file", "writable-directory", "permission-denied"],
)
def test_a_vm_that_does_not_refuse_a_write_to_captured_authority_blocks_before_preflight(
    ctx: Context, writes: dict[str, str], named: str
) -> None:
    _capture_authority(ctx)
    assert isinstance(ctx.sandbox, FakeSandbox)
    ctx.sandbox.authority_writes = writes

    with pytest.raises(Blocked) as raised:
        sandbox_step.run(ctx)

    assert raised.value.reason == "authority-not-read-only"
    assert named in str(raised.value)
    check = _authority_check(ctx)
    assert check["status"] == "fail"
    assert check["detail"] == named
    assert ctx.state is State.SANDBOX_CREATING
    assert not any(argv[-1].endswith("protect_paths.mjs") for _, argv in ctx.sandbox.sync_calls)


def test_the_probe_asks_about_every_captured_file_and_the_directories_holding_them(
    ctx: Context,
) -> None:
    _capture_authority(ctx)
    assert isinstance(ctx.sandbox, FakeSandbox)

    sandbox_step.run(ctx)

    snapshot = ctx.store.runtime.policy(ctx.run.id)
    assert snapshot is not None
    files = [*snapshot["files"], "snapshot.json"]
    assert "harness.config.json" in files
    assert ".agents/vendor/harness/hooks/delivery_policy.mjs" in files
    detail = _authority_check(ctx)["detail"]
    assert detail == f"{len(authority_probe.targets(files))} paths refused writes with EROFS"


def test_a_probe_that_cannot_run_in_the_vm_blocks_as_unverified(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capture_authority(ctx)
    fake = ctx.sandbox
    assert isinstance(fake, FakeSandbox)
    exec_sync = fake.exec_sync

    def no_node(name: str, argv: Sequence[str], **kwargs: object) -> Completed:
        if list(argv) == authority_probe.probe_argv():
            return Completed(tuple(argv), 127, "", "sh: 1: node: not found")
        return exec_sync(name, argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(fake, "exec_sync", no_node)

    with pytest.raises(Blocked) as raised:
        sandbox_step.run(ctx)

    assert raised.value.reason == "authority-unverified"
    assert "exited 127: sh: 1: node: not found" in _authority_check(ctx)["detail"]
