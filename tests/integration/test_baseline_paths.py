"""Baseline weakening scope uses configured Git pathspecs against a real base tree."""

import subprocess
from pathlib import Path

import pytest

from factory.repo import GitError, paths_at_ref


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_baseline_globs_from_app_directory(tmp_path: Path) -> None:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.test")
    app = tmp_path / "apps/web"
    for name in ("src/old.test.ts", "src/nested/view.test.tsx", "src/app.ts"):
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "baseline")
    base = git(tmp_path, "rev-parse", "HEAD")
    (app / "src/old.test.ts").unlink()
    (app / "src/new.test.ts").write_text("candidate test\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "candidate")
    assert set(
        paths_at_ref(app, base, [":(glob)src/**/*.test.ts", ":(glob)src/**/*.test.tsx"])
    ) == {"src/old.test.ts", "src/nested/view.test.tsx"}


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
@pytest.mark.parametrize(
    ("pathspecs", "expected"),
    [
        (["src"], {"src/plain.test.ts", "src/deep/other.test.ts", "src/space ü.test.ts"}),
        (
            [":(glob)src/**/*.test.ts", ":(exclude)src/deep/**"],
            {"src/plain.test.ts", "src/space ü.test.ts"},
        ),
        (
            [":(top,glob)apps/web/src/**/*.test.ts"],
            {"src/plain.test.ts", "src/deep/other.test.ts", "src/space ü.test.ts"},
        ),
        ([":(icase)SRC/PLAIN.TEST.TS"], {"src/plain.test.ts"}),
        ([":(literal)odd[1]\n.test.ts"], {"odd[1]\n.test.ts"}),
        (["missing"], set()),
        (
            [],
            {
                "src/plain.test.ts",
                "src/deep/other.test.ts",
                "src/space ü.test.ts",
                "odd[1]\n.test.ts",
            },
        ),
    ],
)
def test_baseline_pathspec_semantics_and_candidate_preservation(
    tmp_path: Path, object_format: str, pathspecs: list[str], expected: set[str]
) -> None:
    git(tmp_path, "init", f"--object-format={object_format}")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.test")
    app = tmp_path / "apps/web"
    for name in (
        "apps/web/src/plain.test.ts",
        "apps/web/src/deep/other.test.ts",
        "apps/web/src/space ü.test.ts",
        "apps/web/odd[1]\n.test.ts",
        "apps/other/src/outside.test.ts",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("base\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "baseline")
    (app / "src/plain.test.ts").write_text("staged\n")
    git(tmp_path, "add", ".")
    (app / "src/plain.test.ts").write_text("unstaged\n")
    (app / "src/untracked.test.ts").write_text("untracked\n")
    index_before = (tmp_path / ".git/index").read_bytes()
    status_before = git(tmp_path, "status", "--porcelain")
    assert set(paths_at_ref(app, "HEAD", pathspecs)) == expected
    assert (tmp_path / ".git/index").read_bytes() == index_before
    assert git(tmp_path, "status", "--porcelain") == status_before
    assert (app / "src/plain.test.ts").read_text() == "unstaged\n"
    with pytest.raises(GitError):
        paths_at_ref(app, "missing-ref", pathspecs)
