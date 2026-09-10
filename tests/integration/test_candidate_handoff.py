"""Preserved candidate retention and fresh-run restoration."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from factory import candidate_handoff, repo
from factory.machine import Blocked, State
from factory.steps import Context, implement, worktree
from tests.integration.conftest import git


def _raw_git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _candidate_manifest(ctx: Context, tmp_path: Path, *, protected: bool = False) -> Path:
    base = git(ctx.project.path, "rev-parse", ctx.project.base_ref)
    source_head = git(ctx.project.path, "rev-parse", "HEAD")
    backup = tmp_path / "candidate-repository"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(ctx.project.path), str(backup)],
        check=True,
    )
    git(backup, "config", "user.email", "factory@example.invalid")
    git(backup, "config", "user.name", "factory tests")
    changed = backup / ("uv.lock" if protected else "candidate.txt")
    changed.write_text("preserved candidate\n", encoding="utf-8")
    git(backup, "add", str(changed.relative_to(backup)))
    git(backup, "commit", "-m", "preserve candidate")
    candidate_commit = git(backup, "rev-parse", "HEAD")
    candidate_tree = git(backup, "rev-parse", "HEAD^{tree}")
    patch = _raw_git(
        backup,
        "diff",
        "--binary",
        "--full-index",
        "--no-ext-diff",
        "--no-textconv",
        base,
        candidate_commit,
    )
    bundle = tmp_path / "candidate.bundle"
    git(backup, "bundle", "create", str(bundle), "HEAD")
    manifest = tmp_path / "candidate-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "ticket": ctx.run.linear_id,
                "source_run_id": ctx.run.id,
                "source_head": source_head,
                "base_commit": base,
                "candidate_commit": candidate_commit,
                "candidate_tree": candidate_tree,
                "bundle_file": bundle.name,
                "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                "diff_sha256": hashlib.sha256(patch.encode()).hexdigest(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def _preservable_source(ctx: Context) -> None:
    ctx.store.update_run(
        ctx.run.id,
        base_ref=ctx.project.base_ref,
        worktree=str(ctx.project.path),
    )
    ctx.store.record_transition(
        ctx.run.id,
        from_state=State.APPROVED,
        to_state=State.BLOCKED,
        actor="automatic",
        rule="test-preserved-source",
    )
    ctx.refresh()


def test_check_is_read_only_and_rejects_protected_paths(ctx: Context, tmp_path: Path) -> None:
    _preservable_source(ctx)
    manifest = _candidate_manifest(ctx, tmp_path)
    assert ctx.harness is not None

    candidate = candidate_handoff.check_external(
        ctx.store,
        ctx.project,
        ctx.harness,
        ctx.run.linear_id,
        manifest,
    )

    assert candidate.changed_paths == ("candidate.txt",)
    assert ctx.store.checks(ctx.run.id) == []
    assert not (ctx.state_dir / "handoffs/candidate").exists()

    protected_dir = tmp_path / "protected"
    protected_dir.mkdir()
    protected_manifest = _candidate_manifest(ctx, protected_dir, protected=True)
    with pytest.raises(Blocked, match=r"candidate changes protected paths: uv\.lock"):
        candidate_handoff.check_external(
            ctx.store,
            ctx.project,
            ctx.harness,
            ctx.run.linear_id,
            protected_manifest,
        )


def test_retained_candidate_requires_cancel_then_restores_exact_tree_and_prompt(
    ctx: Context, tmp_path: Path
) -> None:
    _preservable_source(ctx)
    manifest = _candidate_manifest(ctx, tmp_path)
    candidate = candidate_handoff.retain(
        ctx.store,
        ctx.project,
        ctx.harness,  # type: ignore[arg-type]
        ctx.run.linear_id,
        manifest,
        ctx.state_dir,
    )
    retained = ctx.state_dir / "handoffs/candidate"
    assert (retained / "candidate.bundle").is_file()
    assert len(ctx.store.checks(ctx.run.id)) == 1

    with pytest.raises(Blocked, match="cancel it before a fresh carry-forward run"):
        candidate_handoff.validate_retained_source(
            ctx.store,
            ctx.project,
            ctx.harness,  # type: ignore[arg-type]
            ctx.run.linear_id,
            ctx.run.id,
            ctx.state_dir,
        )

    source_run = ctx.run
    ctx.store.record_transition(
        source_run.id,
        from_state=State.BLOCKED,
        to_state=State.CANCELLED,
        actor="human",
        rule="test-cancel",
    )
    validated = candidate_handoff.validate_retained_source(
        ctx.store,
        ctx.project,
        ctx.harness,  # type: ignore[arg-type]
        source_run.linear_id,
        source_run.id,
        ctx.state_dir,
    )
    new_run = ctx.store.insert_run(
        linear_id=source_run.linear_id,
        project=source_run.project,
        team=source_run.team,
    )
    new_ctx = replace(ctx, run=new_run)
    candidate_handoff.copy_to_new_run(validated, new_ctx.state_dir)

    worktree.run(new_ctx)

    assert new_ctx.state is State.WORKTREE_READY
    assert repo.index_tree(new_ctx.worktree) == candidate.candidate_tree
    assert (new_ctx.worktree / "candidate.txt").read_text() == "preserved candidate\n"
    prompt, _ = implement.build_prompt(new_ctx)
    assert "## Preserved candidate carried forward" in prompt
    assert f"run `{source_run.id}`" in prompt
    assert candidate.candidate_commit in prompt
    assert "`candidate.txt`" in prompt


def test_retained_bundle_tampering_is_refused(ctx: Context, tmp_path: Path) -> None:
    _preservable_source(ctx)
    manifest = _candidate_manifest(ctx, tmp_path)
    candidate_handoff.retain(
        ctx.store,
        ctx.project,
        ctx.harness,  # type: ignore[arg-type]
        ctx.run.linear_id,
        manifest,
        ctx.state_dir,
    )
    ctx.store.record_transition(
        ctx.run.id,
        from_state=State.BLOCKED,
        to_state=State.CANCELLED,
        actor="human",
        rule="test-cancel",
    )
    with (ctx.state_dir / "handoffs/candidate/candidate.bundle").open("ab") as stream:
        stream.write(b"tamper")

    with pytest.raises(Blocked, match="bundle SHA-256 does not match"):
        candidate_handoff.validate_retained_source(
            ctx.store,
            ctx.project,
            ctx.harness,  # type: ignore[arg-type]
            ctx.run.linear_id,
            ctx.run.id,
            ctx.state_dir,
        )
