"""Git — worktrees, branches, base refs — §11.

Every command here is `git`, run on the host, with an argv list. The factory never runs
a build, a test, `pnpm install` or `uv sync` on the host against a worktree: `git`, `gh`
and file reads are the complete host-side command set (§17.4). Anything that executes
repository content runs inside the sandbox.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from factory.machine import Blocked

__all__ = [
    "GitError",
    "add_worktree",
    "branch_name",
    "branch_type_for_labels",
    "changed_paths",
    "fetch",
    "identifier_on_base",
    "prune_worktrees",
    "remote_branch_exists",
    "remote_branches_matching",
    "remove_worktree",
    "slugify",
    "worktree_exists",
]

SLUG_MAX = 40

#: §11.1 — the ticket's category label decides the branch type. Anything else is a
#: chore, which is the honest answer for a ticket that did not say.
_TYPE_BY_LABEL = {"feature": "feat", "bug": "fix"}


class GitError(Exception):
    """A git command that failed, with its stderr attached."""


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {repo}:\n{proc.stderr.strip()}")
    return proc.stdout.strip()


def _git_ok(repo: Path, *args: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    return proc.returncode == 0


def slugify(title: str, *, limit: int = SLUG_MAX) -> str:
    """Lowercase, non-alphanumerics to `-`, collapsed, truncated, trailing `-` stripped.

    Truncation happens before the trailing strip so a title cut mid-word does not leave
    a dangling hyphen in a branch name that then appears in every PR URL.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:limit].strip("-")


def branch_type_for_labels(labels: list[str]) -> str:
    for label in labels:
        if mapped := _TYPE_BY_LABEL.get(label.strip().lower()):
            return mapped
    return "chore"


def branch_name(branch_type: str, identifier: str, title: str) -> str:
    """`<type>/<TEAM>-<num>-<slug>` — verbatim from both repos' `AGENTS.md`.

    The shape is not cosmetic: `/code-review`, `agent-review.yml` and Linear's GitHub
    integration all resolve the ticket out of it mechanically.
    """
    return f"{branch_type}/{identifier}-{slugify(title)}"


def fetch(repo: Path, base_branch: str) -> None:
    _git(repo, "fetch", "origin", base_branch, "--prune")


def remote_branch_exists(repo: Path, branch: str) -> bool:
    return bool(_git(repo, "ls-remote", "--heads", "origin", branch))


def remote_branches_matching(repo: Path, identifier: str) -> list[str]:
    """Remote branches whose name mentions the identifier.

    Not a duplicate of `identifier_on_base`: P0-11 found `BAC-4` sitting complete and
    unmerged on `origin/feat/BAC-4-application-skeleton`, where a log search against
    the base ref sees nothing at all.
    """
    listing = _git(repo, "branch", "-r", "--list", f"*{identifier}*")
    return [line.strip() for line in listing.splitlines() if line.strip()]


def identifier_on_base(repo: Path, identifier: str, base_ref: str) -> list[str]:
    """Commits on the base ref whose **subject** names the identifier.

    Subjects only, deliberately. The naive `git log --grep` the discovery note proposes
    matches the message body too, and on this machine it returns a commit whose body
    reads "Lessons from the BAC-2/BAC-4 end-to-end run" for a ticket that was never
    implemented. A false `already-implemented` block is worse than a missed one,
    because it stops a legitimate run with a reason a human has to disprove.
    """
    pattern = rf"\b{re.escape(identifier)}\b"
    listing = _git(repo, "log", "--format=%H %s", base_ref)
    return [line for line in listing.splitlines() if re.search(pattern, line.split(" ", 1)[-1])]


def worktree_exists(repo: Path, path: Path) -> bool:
    listing = _git(repo, "worktree", "list", "--porcelain")
    return any(line == f"worktree {path}" for line in listing.splitlines())


@dataclass(frozen=True)
class WorktreePlan:
    """What `add_worktree` would do, for `--dry-run`."""

    repo: Path
    path: Path
    branch: str
    base_ref: str

    def commands(self) -> list[list[str]]:
        return [
            [
                "git",
                "-C",
                str(self.repo),
                "fetch",
                "origin",
                self.base_ref.split("/", 1)[-1],
                "--prune",
            ],
            [
                "git",
                "-C",
                str(self.repo),
                "worktree",
                "add",
                "-b",
                self.branch,
                str(self.path),
                self.base_ref,
            ],
        ]


def add_worktree(repo: Path, path: Path, branch: str, base_ref: str) -> None:
    """Create the worktree, refusing to reuse a branch that already exists remotely.

    The refusal is the point. `git worktree add -b` against an existing remote branch
    either fails or silently attaches to it depending on the name, and attaching would
    put an unattended writer on top of somebody else's unmerged work. Deleting the
    remote branch is a human's call, so this raises with the branch named instead.

    Submodules are deliberately not recursed. Neither consumer has any; `harness` does,
    and `harness` is never a factory work target (§24.4). A `--recurse-submodules` here
    would be a decision made for a repository the factory does not drive.
    """
    if remote_branch_exists(repo, branch):
        raise Blocked(
            "branch-exists",
            f"origin/{branch} already exists. The factory never reuses a remote branch: "
            f"delete it (`git push origin --delete {branch}`) or rename the ticket's slug.",
        )
    if worktree_exists(repo, path):
        raise Blocked("worktree-exists", f"{path} is already a worktree of {repo}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(path), base_ref)


def remove_worktree(repo: Path, path: Path, *, force: bool = False) -> None:
    """Remove and prune. `unlock` first when git refuses — never `rm -rf`. F15."""
    if not worktree_exists(repo, path):
        prune_worktrees(repo)
        return
    args = ["worktree", "remove", str(path)]
    if force:
        args.append("--force")
    if not _git_ok(repo, *args):
        _git_ok(repo, "worktree", "unlock", str(path))
        _git(repo, "worktree", "remove", "--force", str(path))
    prune_worktrees(repo)


def prune_worktrees(repo: Path) -> None:
    _git(repo, "worktree", "prune")


def delete_local_branch(repo: Path, branch: str) -> None:
    """Only ever a local branch, and only when it has no remote counterpart.

    A pushed branch is never deleted by the factory: the work is visible to other
    people at that point, and reclaiming disk is not a reason to remove it.
    """
    if remote_branch_exists(repo, branch):
        return
    _git_ok(repo, "branch", "-D", branch)


def changed_paths(worktree: Path, base_ref: str) -> list[str]:
    """`git diff --name-only <base>...HEAD`, the input to the host-execution guard."""
    listing = _git(worktree, "diff", "--name-only", f"{base_ref}...HEAD")
    return [line for line in listing.splitlines() if line]


def diff_stat(worktree: Path, base_ref: str) -> str:
    return _git(worktree, "diff", "--stat", f"{base_ref}...HEAD")


def head_sha(repo: Path, ref: str = "HEAD") -> str:
    return _git(repo, "rev-parse", ref)


def is_clean(worktree: Path) -> bool:
    return not _git(worktree, "status", "--porcelain")
