"""§17.4 host-git hardening — a prompt-injected builder must not reach host code (issue #125).

The build sandbox mounts the project root read/write, and the agent commits inside the VM into
the shared `.git`. So a builder can write `.git/config`, `.git/hooks/*` and `.git/info/attributes`.
Every host-side `factory.repo` call then reads that agent-controlled metadata. git treats several
config keys and the hook scripts as commands to run, so without hardening the host executes
whatever the builder planted.

Each test plants one executing key the way the builder could, then calls the real host helper that
reads it, and asserts the planted command did NOT run on the host. The planted payload only writes
a marker file, so the marker's absence is the proof. These fail on origin/main.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from factory import repo


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
        env=_clean_env(),
    )
    return proc.stdout.strip()


def _clean_env() -> dict[str, str]:
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


@pytest.fixture
def poisoned(tmp_path: Path) -> tuple[Path, Path, str, Path]:
    """A project whose worktree carries agent work, ready for one planted key.

    Returns `(project, worktree, payload, marker)`. `payload` is an executable that writes
    `marker` and then fails; planting it in any executing config key or hook proves host
    execution when `marker` appears.
    """
    marker = tmp_path / "HOST_RAN"
    payload = tmp_path / "payload.sh"
    payload.write_text(f'#!/bin/sh\necho ran >> "{marker}"\nexit 0\n')
    payload.chmod(0o755)

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True
    )
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.email", "t@example.invalid")
    _git(seed, "config", "user.name", "t")
    (seed / "app.py").write_text("x = 1\n")
    (seed / "tests").mkdir()
    (seed / "tests" / "test_base.py").write_text("def test_base():\n    pass\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-qm", "base")
    _git(seed, "push", "-q", str(origin), "main")

    project = tmp_path / "project"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(project)], check=True, capture_output=True
    )
    worktree = project / ".factory" / "worktrees" / "T-1"
    _git(project, "worktree", "add", "-q", "-b", "feat/T-1-x", str(worktree), "origin/main")
    _git(worktree, "config", "user.email", "t@example.invalid")
    _git(worktree, "config", "user.name", "t")
    (worktree / "app.py").write_text("x = 2\n")
    (worktree / "tests" / "test_new.py").write_text("def test_new():\n    pass\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-qm", "agent work")
    (worktree / "app.py").write_text("x = 3\n")  # dirty, as a mid-build tree is
    return project, worktree, str(payload), marker


def _plant_config(project: Path, key: str, value: str) -> None:
    subprocess.run(
        ["git", "-C", str(project), "config", key, value],
        check=True,
        capture_output=True,
        env=_clean_env(),
    )


def test_core_fsmonitor_does_not_run_on_a_host_diff(poisoned: tuple[Path, Path, str, Path]) -> None:
    project, worktree, payload, marker = poisoned
    _plant_config(project, "core.fsmonitor", payload)
    # The host-execution guard diffs the agent branch every run.
    repo.changed_paths(worktree, "origin/main")
    repo.is_clean(worktree)
    repo.diff_stat(worktree, "origin/main")
    assert not marker.exists(), "core.fsmonitor executed on the host"


def test_diff_external_does_not_run_on_a_host_diff(poisoned: tuple[Path, Path, str, Path]) -> None:
    project, worktree, payload, marker = poisoned
    _plant_config(project, "diff.external", payload)
    # The red-phase replay extracts the test-half patch with a content diff.
    repo.diff_pathspec(worktree, "origin/main", ["tests"])
    assert not marker.exists(), "diff.external executed on the host"


def test_hooks_do_not_run_on_a_host_worktree_add(poisoned: tuple[Path, Path, str, Path]) -> None:
    project, _worktree, _payload, marker = poisoned
    hooks = project / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("post-checkout", "reference-transaction"):
        hook = hooks / name
        hook.write_text(f'#!/bin/sh\necho ran >> "{marker}"\nexit 0\n')
        hook.chmod(0o755)
    # The red-phase replay cuts a scratch worktree on the host.
    scratch = project / ".factory" / "scratch" / "S-1"
    repo.add_detached_worktree(project, scratch, "origin/main")
    assert not marker.exists(), "a planted hook executed on the host"


def test_smudge_filter_does_not_run_on_a_host_checkout(
    poisoned: tuple[Path, Path, str, Path],
) -> None:
    project, _worktree, payload, marker = poisoned
    _plant_config(project, "filter.evil.smudge", f"{payload} smudge")
    info = project / ".git" / "info"
    info.mkdir(exist_ok=True)
    (info / "attributes").write_text("*.py filter=evil\n")
    scratch = project / ".factory" / "scratch" / "S-1"
    repo.add_detached_worktree(project, scratch, "origin/main")
    assert not marker.exists(), "a planted smudge filter executed on the host"
