"""`doctor`'s §15.2 sensitive-path check — the one that makes an inert Tier-2 trigger visible.

The defect this file exists for was silent for four phases. `_SENSITIVE_DIRS` in
`steps/review.py` held `src/**/routes/**` for the frontend stack, and there has never been
a `routes` directory in `frontend-harness`: measured 2026-08-23, the glob matched 0 of its
192 tracked files. A trigger that cannot fire and a trigger that happened not to fire write
the same evidence — `tier2: no-trigger` — so no run, no test and no PR body could tell them
apart. Moving the list to `projects.toml` made it editable. This check is what makes it
checkable, and these tests are what keep the check honest.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from factory.cli import _sensitive_paths_check
from factory.registry import Project


def _repo(tmp_path: Path, *files: str) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    for name in files:
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed"],
        cwd=path,
        check=True,
    )
    return path


def _project(path: Path, *globs: str) -> Project:
    return Project(
        name="p",
        team="AAA",
        path=path,
        remote="https://example.invalid/p.git",
        base_branch="v2",
        stack="frontend",
        template="",
        kits=(),
        static_mcp=(),
        build_sandbox="factory-build-p",
        review_sandbox="factory-review-p",
        vault_mount="rw",
        network_allow=(),
        sensitive_paths=globs,
    )


def test_a_glob_matching_no_tracked_file_fails_the_check(tmp_path: Path) -> None:
    """The exact shape of the original defect, reproduced: the repository is real, the
    glob is well-formed, and it names a directory that is not there."""
    repo = _repo(tmp_path, "src/features/projects/ui/ProjectsPage.tsx", "e2e/projects.spec.ts")

    _, ok, detail = _sensitive_paths_check(_project(repo, "src/**/routes/**"))

    assert not ok
    assert "src/**/routes/**" in detail


def test_a_glob_that_matches_passes_and_says_how_much(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "src/features/projects/ui/ProjectsPage.tsx", "e2e/projects.spec.ts")

    _, ok, detail = _sensitive_paths_check(_project(repo, "src/features/**", "e2e/**"))

    assert ok
    assert "2 glob(s), 2 files" in detail


def test_one_dead_glob_among_live_ones_still_fails(tmp_path: Path) -> None:
    """Checking the list as a whole would let a dead glob hide behind a live one — which is
    how a four-entry list rots one entry at a time."""
    repo = _repo(tmp_path, "src/features/a.tsx")

    _, ok, detail = _sensitive_paths_check(_project(repo, "src/features/**", "src/gone/**"))

    assert not ok
    assert "src/gone/**" in detail
    assert "src/features/**" not in detail


def test_declaring_nothing_is_not_a_failure(tmp_path: Path) -> None:
    """An empty list is a decision — the size and protected-path triggers still apply — and
    `doctor` must not nag a project into naming a directory it does not have."""
    repo = _repo(tmp_path, "src/features/a.tsx")

    _, ok, detail = _sensitive_paths_check(_project(repo))

    assert ok
    assert detail == "none declared"
