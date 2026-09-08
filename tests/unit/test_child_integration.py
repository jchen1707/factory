"""Actual private Git/file integration, with only the VM transport replaced locally."""

import base64
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from factory import child_integration, child_workspaces
from factory.delegation import DelegationBroker
from factory.machine import Blocked
from factory.sandbox import child_source
from factory.sandbox.base import Completed
from factory.steps import Context
from tests.unit.test_delegation import setup, task


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


class LocalSandbox:
    """Execute the source-owned adapter unchanged; no candidate code/model is executed."""

    def generation(self, name: str) -> str:
        return "original"

    def exec_sync(self, name: str, argv: list[str], **kwargs: Any) -> Completed:
        result = subprocess.run(
            argv, capture_output=True, text=True, check=False, input=kwargs.get("stdin")
        )
        return Completed(tuple(argv), result.returncode, result.stdout, result.stderr)


def fixture(tmp_path: Path) -> tuple[Context, dict, dict]:
    store, source = setup(tmp_path)
    store.runtime.configure("project", "synthetic", {"delegation_mode": "isolated-write"})
    git(source, "init")
    git(source, "config", "user.name", "Fixture")
    git(source, "config", "user.email", "fixture@example.test")
    (source / "src/a").write_text("old a")
    (source / "src/b").write_text("old b")
    git(source, "add", ".")
    git(source, "commit", "-m", "base")
    (source / "notes").write_text("untracked parent notes")
    request = DelegationBroker(store, "parent", source).request(
        "write", task() | {"mode": "isolated-write", "paths": ["src"]}
    )
    run = store.run_by_id(request["run_id"])
    assert run is not None
    store.runtime.configure("run", run.id, {"delegation_generation": "original"})
    snapshot = tmp_path / "snapshot"
    shutil.copytree(source, snapshot)
    private = tmp_path / "private"
    shutil.copytree(snapshot, private)
    workspace = {
        "path": str(private),
        "identity": [private.stat().st_dev, private.stat().st_ino],
        "snapshot": {"path": str(snapshot), "tree_digest": child_source.tree_digest(snapshot)},
    }
    ctx = cast(
        Context,
        SimpleNamespace(
            store=store,
            run=run,
            worktree=source,
            project=SimpleNamespace(
                path=source, name="synthetic", build_sandbox="factory-build-fixture"
            ),
            sandbox=LocalSandbox(),
        ),
    )
    return ctx, request, workspace


def publish(ctx: Context, request: dict, workspace: dict) -> None:
    artifact = child_workspaces.artifact(
        workspace,
        request["request"]["task"]["paths"],
        child_source.capture(
            LocalSandbox(), "factory-build-child-fixture", Path(workspace["path"])
        ),
    )
    encoded = json.dumps(artifact, sort_keys=True)
    owner = (ctx.run.id, 1, request["id"], "child-workspace", "artifact")
    ctx.store.intend_effect(*owner)
    ctx.store.confirm_effect(*owner, encoded)
    ctx.store.runtime.db.execute(
        "UPDATE delegation_requests SET status='completed',result=? WHERE id=?",
        (
            json.dumps({"artifact": {"sha256": hashlib.sha256(encoded.encode()).hexdigest()}}),
            request["id"],
        ),
    )


def drain(ctx: Context) -> None:
    ctx.store.runtime.db.execute(
        "UPDATE agent_leases SET status='completed' WHERE invocation_id='parent'"
    )


def test_integration_preserves_parent_index_notes_and_commits_and_replays(tmp_path: Path) -> None:
    ctx, request, workspace = fixture(tmp_path)
    private = Path(workspace["path"])
    (private / "src/a").write_text("child committed a")
    git(private, "add", "src/a")
    git(private, "commit", "-m", "child commit")
    (private / "src/b").write_text("child dirty b")
    publish(ctx, request, workspace)
    parent_head = git(ctx.worktree, "rev-parse", "HEAD")
    parent_index = git(ctx.worktree, "write-tree")
    child_integration.advance(ctx)
    assert (ctx.worktree / "src/a").read_text() == "old a"  # parent writer still active
    drain(ctx)
    ctx.store.record_check(ctx.run.id, 1, "authority:verify", "pass", detail="1")
    child_integration.advance(ctx)
    assert (ctx.worktree / "src/a").read_text() == "child committed a"
    assert (ctx.worktree / "src/b").read_text() == "child dirty b"
    assert (ctx.worktree / "notes").read_text() == "untracked parent notes"
    assert git(ctx.worktree, "rev-parse", "HEAD^") == parent_head
    assert git(ctx.worktree, "write-tree") != parent_index
    assert git(ctx.worktree, "diff", "--cached") == ""
    assert git(private, "log", "-1", "--format=%s") == "child commit"
    assert ctx.store.checks(ctx.run.id)[-1]["status"] == "stale"
    count = len(ctx.store.effects(ctx.run.id))
    child_integration.advance(ctx)
    assert len(ctx.store.effects(ctx.run.id)) == count
    ctx.store.close()


def test_conflict_is_preflighted_before_any_child_edit(tmp_path: Path) -> None:
    ctx, request, workspace = fixture(tmp_path)
    private = Path(workspace["path"])
    (private / "src/a").write_text("new a")
    (private / "src/b").write_text("new b")
    publish(ctx, request, workspace)
    (ctx.worktree / "src/b").write_text("human edit")
    drain(ctx)
    with pytest.raises(Blocked, match="child-integration-conflict"):
        child_integration.advance(ctx)
    assert (ctx.worktree / "src/a").read_text() == "old a"
    assert (ctx.worktree / "src/b").read_text() == "human edit"
    ctx.store.close()


def test_scope_violation_never_becomes_an_artifact(tmp_path: Path) -> None:
    ctx, request, workspace = fixture(tmp_path)
    (Path(workspace["path"]) / "notes").write_text("child changed parent note")
    with pytest.raises(ValueError, match="outside declared scope"):
        publish(ctx, request, workspace)
    assert (ctx.worktree / "notes").read_text() == "untracked parent notes"
    ctx.store.close()


def test_sibling_writable_scopes_are_disjoint(tmp_path: Path) -> None:
    ctx, _, _ = fixture(tmp_path)
    broker = DelegationBroker(ctx.store, "parent", ctx.worktree)
    with pytest.raises(ValueError, match="overlap"):
        broker.request("overlap", task() | {"mode": "isolated-write", "paths": ["src/a"]})
    assert (
        broker.request("other", task() | {"mode": "isolated-write", "paths": ["notes"]})["status"]
        == "pending"
    )
    ctx.store.close()


@pytest.mark.parametrize("changed", ["authority", "base", "generation", "symlink"])
def test_changed_integration_inputs_refuse(tmp_path: Path, changed: str) -> None:
    ctx, request, workspace = fixture(tmp_path)
    (Path(workspace["path"]) / "src/a").write_text("child a")
    publish(ctx, request, workspace)
    drain(ctx)
    if changed == "authority":
        snapshot = ctx.store.runtime.policy(ctx.run.id)
        assert snapshot
        (Path(snapshot["root"]) / "surprise").write_text("changed")
    elif changed == "base":
        git(ctx.worktree, "commit", "--allow-empty", "-m", "new base")
    elif changed == "generation":
        ctx.store.runtime.configure("run", ctx.run.id, {"delegation_generation": "recreated"})
    else:
        (ctx.worktree / "src/a").unlink()
        (ctx.worktree / "src/a").symlink_to(ctx.worktree / "notes")
    with pytest.raises(Blocked):
        child_integration.advance(ctx)
    assert (ctx.worktree / "notes").read_text() == "untracked parent notes"
    ctx.store.close()


def test_file_application_reconciles_crash_after_write(tmp_path: Path) -> None:
    from factory.sandbox.child_apply import apply

    (tmp_path / "file").write_text("old")
    change = {
        "before": {"path": "file", "mode": 0o644, "data": base64.b64encode(b"old").decode()},
        "after": {"path": "file", "mode": 0o644, "data": base64.b64encode(b"new").decode()},
    }
    assert apply(tmp_path, change) == "applied"
    assert apply(tmp_path, change) == "already-applied"
    (tmp_path / "file").write_text("human edit after crash")
    with pytest.raises(ValueError, match="conflicts"):
        apply(tmp_path, change)
    assert (tmp_path / "file").read_text() == "human edit after crash"


def test_large_edit_uses_bounded_stdin_instead_of_process_arguments(tmp_path: Path) -> None:
    ctx, request, workspace = fixture(tmp_path)
    (Path(workspace["path"]) / "src/a").write_text("a" * (1024 * 1024))
    publish(ctx, request, workspace)
    drain(ctx)
    child_integration.advance(ctx)
    assert (ctx.worktree / "src/a").stat().st_size == 1024 * 1024
    ctx.store.close()


@pytest.mark.parametrize("phase", ["before-head", "after-head", "after-index"])
def test_commit_recovery_preserves_unrelated_staging_and_does_not_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    from factory.sandbox import child_commit

    ctx, _, workspace = fixture(tmp_path)
    artifact = child_workspaces.artifact(
        workspace,
        ["src"],
        child_source.capture(
            LocalSandbox(), "factory-build-child-fixture", Path(workspace["path"])
        ),
    )
    (ctx.worktree / "src/a").write_text("child integrated")
    old = {"path": "src/a", "mode": 0o644, "data": base64.b64encode(b"old a").decode()}
    new = old | {"data": base64.b64encode(b"child integrated").decode()}
    git(ctx.worktree, "add", "notes")
    payload = {
        "base": artifact["base"],
        "changes": [{"before": old, "after": new}],
        "time": 1788840703,
        "message": "Integrate bounded child\n",
    }
    original_run = subprocess.run
    original_replace = child_commit.os.replace

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if phase == "before-head" and "update-ref" in argv:
            raise OSError("simulated process loss before HEAD")
        return original_run(argv, **kwargs)

    def replace(source: Path, target: Path) -> None:
        if Path(source).name == "index.lock":
            if phase == "after-head":
                raise OSError("simulated process loss after HEAD")
            original_replace(source, target)
            if phase == "after-index":
                raise OSError("simulated process loss after index")
        else:
            original_replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", run)
        patch.setattr(child_commit.os, "replace", replace)
        with pytest.raises(OSError, match="simulated"):
            child_commit.commit(ctx.worktree, payload)
    head = child_commit.commit(ctx.worktree, payload)
    assert child_commit.commit(ctx.worktree, payload) == head
    assert git(ctx.worktree, "rev-list", "--count", payload["base"] + "..HEAD") == "1"
    assert git(ctx.worktree, "rev-parse", "HEAD^") == payload["base"]
    assert git(ctx.worktree, "show", "HEAD:src/a") == "child integrated"
    assert git(ctx.worktree, "diff", "--cached", "--name-only") == "notes"
    assert (ctx.worktree / "notes").read_text() == "untracked parent notes"
    assert not (ctx.worktree / ".git/index.lock").exists()
    ctx.store.close()


def test_unowned_git_lock_is_never_removed(tmp_path: Path) -> None:
    from factory.sandbox.child_commit import commit

    ctx, _, workspace = fixture(tmp_path)
    base = child_workspaces.artifact(
        workspace,
        ["src"],
        child_source.capture(
            LocalSandbox(), "factory-build-child-fixture", Path(workspace["path"])
        ),
    )["base"]
    lock = ctx.worktree / ".git/index.lock"
    lock.write_text("other Git owner")
    with pytest.raises(ValueError, match="unowned parent index lock"):
        commit(
            ctx.worktree, {"base": base, "changes": [], "time": 1788840703, "message": "fixture\n"}
        )
    assert lock.read_text() == "other Git owner"
    assert git(ctx.worktree, "rev-parse", "HEAD") == base
    ctx.store.close()


def test_new_writable_file_remains_admissible_while_queued(tmp_path: Path) -> None:
    store, source = setup(tmp_path)
    store.runtime.configure("project", "synthetic", {"delegation_mode": "isolated-write"})
    broker = DelegationBroker(store, "parent", source)
    request = broker.request("create", task() | {"mode": "isolated-write", "paths": ["src/new.py"]})
    broker.authorize_preparation(request["id"])
    store.runtime.configure("run", request["run_id"], {"delegation_mode": "read-only"})
    with pytest.raises(ValueError, match="no longer enabled"):
        broker.authorize_preparation(request["id"])
    store.close()


def test_index_writer_finishing_before_lock_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from factory.sandbox import child_commit

    ctx, _, _ = fixture(tmp_path)
    base = git(ctx.worktree, "rev-parse", "HEAD")
    old = {"path": "src/a", "mode": 0o644, "data": base64.b64encode(b"old a").decode()}
    new = old | {"data": base64.b64encode(b"child integrated").decode()}
    (ctx.worktree / "src/a").write_text("child integrated")
    original = child_commit.os.link
    staged = False

    def link(source: Path, target: Path) -> None:
        nonlocal staged
        if Path(target).name == "index.lock" and not staged:
            staged = True
            git(ctx.worktree, "add", "notes")
        original(source, target)

    monkeypatch.setattr(child_commit.os, "link", link)
    child_commit.commit(
        ctx.worktree,
        {
            "base": base,
            "changes": [{"before": old, "after": new}],
            "time": 1788840703,
            "message": "Integrate bounded child\n",
        },
    )
    assert staged
    assert git(ctx.worktree, "diff", "--cached", "--name-only") == "notes"
    ctx.store.close()


def test_host_artifact_collection_never_interprets_candidate_git_metadata(tmp_path: Path) -> None:
    ctx, _, workspace = fixture(tmp_path)
    private = Path(workspace["path"])
    (private / "src/a").write_text("real exported child bytes")
    exported = child_source.capture(LocalSandbox(), "factory-build-child-fixture", private)
    # These are now hostile and would redirect/fail any subsequent host-side Git.
    (private / ".git/commondir").write_text("/outside-private-child\n")
    (private / ".git/config").write_text("not valid Git configuration")
    artifact = child_workspaces.artifact(workspace, ["src"], exported)
    assert (
        artifact["changes"][0]["after"]["data"]
        == base64.b64encode(b"real exported child bytes").decode()
    )
    ctx.store.close()


@pytest.mark.parametrize("change", ["unchanged", "edited", "deleted", "added"])
def test_child_preserves_safe_baseline_aliases_but_cannot_integrate_link_edits(
    tmp_path: Path,
    change: str,
) -> None:
    ctx, _, workspace = fixture(tmp_path)
    baseline = Path(workspace["snapshot"]["path"])
    private = Path(workspace["path"])
    for root in (baseline, private):
        (root / "alias").symlink_to("src")
    workspace["snapshot"]["tree_digest"] = child_source.tree_digest(baseline)
    (private / "src/a").write_text("child edit")
    if change == "edited":
        (private / "alias").unlink()
        (private / "alias").symlink_to("src/a")
    elif change == "deleted":
        (private / "alias").unlink()
    elif change == "added":
        (private / "new-alias").symlink_to("src/a")
    exported = child_source.capture(LocalSandbox(), "factory-build-child-fixture", private)
    if change == "unchanged":
        result = child_workspaces.artifact(workspace, ["src"], exported)
        assert [item["after"]["path"] for item in result["changes"]] == ["src/a"]
    else:
        with pytest.raises(ValueError, match="symlink edits"):
            child_workspaces.artifact(workspace, ["src", "alias", "new-alias"], exported)
    ctx.store.close()
