"""Clone source export through the sandbox boundary and real, private Git repositories."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from factory.steps import Context


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_clone_snapshot_preserves_private_commits_index_and_dirty_source(tmp_path: Path) -> None:
    from factory.sandbox.child_source import capture, materialize

    source = tmp_path / "private-clone"
    source.mkdir()
    git(source, "init")
    git(source, "config", "user.name", "Fixture")
    git(source, "config", "user.email", "fixture@example.test")
    (source / "tracked.txt").write_text("committed\n")
    (source / ".gitignore").write_text("node_modules/\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "private VM commit")
    head = git(source, "rev-parse", "HEAD")
    (source / "tracked.txt").write_text("staged\n")
    git(source, "add", "tracked.txt")
    (source / "tracked.txt").write_text("unstaged\n")
    (source / "note.txt").write_text("untracked parent note\n")
    (source / "node_modules").mkdir()
    (source / "node_modules/private").write_text("VM environment\n")
    before = git(source, "status", "--porcelain")

    # The same isolated observer script used by sbx executes against a distinct clone.
    from factory.sandbox.base import Completed

    class LocalSandbox:
        def exec_sync(self, name: str, argv: list[str], **kwargs: object) -> Completed:
            result = subprocess.run(argv, capture_output=True, text=True, check=False)
            return Completed(tuple(argv), result.returncode, result.stdout, result.stderr)

    exported = capture(LocalSandbox(), "factory-build-source-fixture", source)
    target = tmp_path / "snapshot"
    materialize(exported, target)
    assert git(target, "rev-parse", "HEAD") == head
    assert git(target, "show", "HEAD:tracked.txt") == "committed"
    assert git(target, "show", ":tracked.txt") == "staged"
    assert (target / "tracked.txt").read_text() == "unstaged\n"
    assert (target / "note.txt").read_text() == "untracked parent note\n"
    assert not (target / "node_modules").exists()
    assert git(target, "status", "--porcelain") == before
    assert git(source, "status", "--porcelain") == before
    assert (source / "node_modules/private").read_text() == "VM environment\n"


def test_source_observer_refuses_changes_during_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory.sandbox import source_observer

    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.test")
    (tmp_path / "file").write_text("original")
    git(tmp_path, "add", "file")
    git(tmp_path, "commit", "-m", "base")
    original = subprocess.run

    def changing(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:

        result = original(argv, **kwargs)
        if "bundle" in argv and "create" in argv:
            (tmp_path / "file").write_text("changed while exporting")
        return result

    monkeypatch.setattr(subprocess, "run", changing)
    with pytest.raises(ValueError, match="source changed"):
        source_observer.main(tmp_path)
    assert (tmp_path / "file").read_text() == "changed while exporting"


def test_source_export_preserves_deleted_directories(tmp_path: Path) -> None:
    import shutil

    from factory.sandbox.source_observer import inventory

    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.test")
    (tmp_path / "removed").mkdir()
    (tmp_path / "removed/file").write_text("before")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    shutil.rmtree(tmp_path / "removed")
    assert inventory(tmp_path)["files"] == [{"path": "removed/file", "mode": None, "data": None}]


def test_retained_snapshot_recovers_publication_and_refuses_tampering(
    ctx: Context, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from factory import child_snapshots
    from factory.machine import Blocked
    from factory.sandbox.base import Completed
    from factory.store import Store
    from tests.integration.test_workflow_children import prepared

    prepared(ctx)
    vm = tmp_path / "vm-clone"
    subprocess.run(["git", "clone", str(ctx.worktree), str(vm)], check=True, capture_output=True)
    (vm / "private-note").write_text("VM only\n")
    ctx.project = replace(ctx.project, requires_clone=True)
    ctx.store.runtime.configure("run", ctx.run.id, {"delegation_generation": "original"})
    monkeypatch.setattr(
        type(ctx.sandbox), "generation", lambda self, name: "original", raising=False
    )

    def execute(self: object, name: str, argv: list[str], **kwargs: Any) -> Completed:
        argv = [str(vm) if arg == str(ctx.worktree) else arg for arg in argv]
        result = subprocess.run(argv, check=False, capture_output=True, text=True)
        return Completed(tuple(argv), result.returncode, result.stdout, result.stderr)

    monkeypatch.setattr(type(ctx.sandbox), "exec_sync", execute)
    parent = {"id": "parent", "attempt": 1}
    request = {"id": "owned-request"}
    root = ctx.home / "state/children/owned-request"
    monkeypatch.setenv("FACTORY_CRASH_AT", "child-source:published")
    with pytest.raises(SystemExit, match="child-source:published"):
        child_snapshots.prepare(ctx, parent, request, root)
    published = json.loads((root / "snapshot.json").read_text())
    monkeypatch.delenv("FACTORY_CRASH_AT")
    db = ctx.store.path
    ctx.store.close()
    ctx.store = Store(db)
    recovered = child_snapshots.prepare(ctx, parent, request, root)
    assert recovered == published
    assert (Path(recovered["path"]) / "private-note").read_text() == "VM only\n"
    assert not (ctx.worktree / "private-note").exists()
    (Path(recovered["path"]) / "private-note").write_text("tampered")
    with pytest.raises(Blocked, match="child-source-stale"):
        child_snapshots.prepare(ctx, parent, request, root)
    assert (vm / "private-note").read_text() == "VM only\n"


def test_snapshot_digest_cannot_confuse_file_contents_with_another_file(tmp_path: Path) -> None:
    from factory.sandbox.child_source import tree_digest

    original = tmp_path / "original"
    replaced = tmp_path / "replaced"
    original.mkdir()
    replaced.mkdir()
    (original / "a").write_bytes(b"b\x00420\x00evil")
    (replaced / "a").write_bytes(b"")
    (replaced / "b").write_bytes(b"evil")
    assert tree_digest(original) != tree_digest(replaced)


def test_stack_relative_aliases_round_trip_without_expansion(tmp_path: Path) -> None:
    from factory.sandbox import child_source
    from tests.unit.test_child_integration import LocalSandbox

    root = tmp_path / "stack"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.test")
    (root / ".agents/agents").mkdir(parents=True)
    (root / ".agents/agents/reviewer.md").write_text("trusted review contract")
    (root / ".agents/start.py").write_text("print('fixture')\n")
    (root / ".claude").mkdir()
    (root / ".claude/agents").symlink_to("../.agents/agents")
    (root / ".claude/start.py").symlink_to("../.agents/start.py")
    git(root, "add", ".")
    git(root, "commit", "-m", "stack aliases")
    exported = child_source.capture(LocalSandbox(), "factory-build-stack", root)
    assert {
        entry["path"] for entry in exported["source"]["files"] if entry["mode"] == 0o120000
    } == {".claude/agents", ".claude/start.py"}
    destination = tmp_path / "copy"
    child_source.materialize(exported, destination)
    assert (destination / ".claude/agents").is_symlink()
    assert (destination / ".claude/agents").readlink() == Path("../.agents/agents")
    assert (destination / ".claude/agents/reviewer.md").read_text() == "trusted review contract"
    assert git(destination, "ls-files", "--stage", ".claude/agents").startswith("120000 ")
    assert git(destination, "status", "--porcelain") == ""
    digest = child_source.tree_digest(destination)
    (destination / ".claude/start.py").unlink()
    (destination / ".claude/start.py").symlink_to("../.agents/agents/reviewer.md")
    assert child_source.tree_digest(destination) != digest


@pytest.mark.parametrize(
    "target",
    [
        "/etc/passwd",
        "../../outside",
        "../.git/config",
        "../missing",
        "./../.agents/file",
        "../.agents/../.agents/file",
        "cycle",
    ],
)
def test_source_links_refuse_escape_missing_and_noncanonical_targets(
    tmp_path: Path, target: str
) -> None:
    from factory.sandbox.source_observer import inventory

    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.test")
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents/file").write_text("inside")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/cycle").symlink_to(target)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "link fixture")
    with pytest.raises(ValueError, match="link"):
        inventory(tmp_path)


def test_observer_refuses_link_mutation_during_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factory.sandbox import source_observer

    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.test")
    (tmp_path / "first").write_text("first")
    (tmp_path / "second").write_text("second")
    (tmp_path / "alias").symlink_to("first")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "aliases")
    original = source_observer.git

    def changing(root: Path, *args: str) -> bytes:
        result = original(root, *args)
        if args[:2] == ("bundle", "create"):
            (tmp_path / "alias").unlink()
            (tmp_path / "alias").symlink_to("second")
        return result

    monkeypatch.setattr(source_observer, "git", changing)
    with pytest.raises(ValueError, match="source changed"):
        source_observer.main(tmp_path)
    assert (tmp_path / "alias").readlink() == Path("second")


def test_snapshot_digest_does_not_normalize_link_bytes(tmp_path: Path) -> None:
    from factory.sandbox.child_source import tree_digest

    (tmp_path / "target").mkdir()
    (tmp_path / "target/file").write_text("inside")
    (tmp_path / "alias").symlink_to("target/file")
    tree_digest(tmp_path)
    (tmp_path / "alias").unlink()
    (tmp_path / "alias").symlink_to("target//file")
    with pytest.raises(ValueError, match="canonical"):
        tree_digest(tmp_path)
