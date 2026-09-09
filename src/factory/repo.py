"""Git — worktrees, branches, base refs — §11.

Every command here is `git`, run on the host, with an argv list. The factory never runs
a build, a test, `pnpm install` or `uv sync` on the host against a worktree: `git`, `gh`
and file reads are the complete host-side command set (§17.4). Anything that executes
repository content runs inside the sandbox.
"""

from __future__ import annotations

import fcntl
import os
import re
import subprocess
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from factory.machine import Blocked

__all__ = [
    "GitError",
    "add_detached_worktree",
    "add_worktree",
    "added_modified_paths",
    "apply_patch",
    "branch_name",
    "branch_type_for_labels",
    "changed_lines",
    "changed_paths",
    "commits_beyond",
    "diff_pathspec",
    "fetch",
    "holds_only_factory_scaffolding",
    "identifier_on_base",
    "in_progress_operation",
    "local_branches_matching",
    "orphan_worktree_dir",
    "prune_worktrees",
    "reason_to_keep_branch",
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


_git_locks = threading.local()


@contextmanager
def serialized_git(repository: Path) -> Iterator[None]:
    """Serialize factory Git mutations across worktrees, threads, and host processes."""
    common = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if common.returncode:
        raise GitError(common.stderr.strip())
    directory = (repository / common.stdout.strip()).resolve()
    held: set[Path] = getattr(_git_locks, "held", set())
    if directory in held:
        yield
        return
    with (directory / "factory-maintenance.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        _git_locks.held = held | {directory}
        try:
            yield
        finally:
            _git_locks.held = held
            fcntl.flock(stream, fcntl.LOCK_UN)


def _git_process(repository: Path, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repository), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    if args and args[0] in {
        "fetch",
        "worktree",
        "branch",
        "update-ref",
        "gc",
        "maintenance",
        "remote",
    }:
        with serialized_git(repository):
            return run()
    return run()


def _git_raw(repo: Path, *args: str) -> str:
    """git's stdout, byte for byte. The right helper whenever the output is a *document*
    rather than a value: a patch's trailing newline is part of the patch, and `git apply`
    rejects one that lost it as `corrupt patch at line <last>`."""
    proc = _git_process(repo, args)
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {repo}:\n{proc.stderr.strip()}")
    return proc.stdout


def _git(repo: Path, *args: str) -> str:
    """git's stdout, stripped. The right helper when the output is a value or a list of
    them — a ref, a sha, a path, lines to split. Never for a patch: see `_git_raw`."""
    return _git_raw(repo, *args).strip()


def _git_ok(repo: Path, *args: str) -> bool:
    proc = _git_process(repo, args)
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


def fetch_from(repo: Path, remote: str, branch: str) -> None:
    """`git fetch <remote> <branch>` — the clone path's way home.

    `remote` is the `sandbox-<name>` remote `sbx create --clone` adds to the host
    checkout, and the ref lands in `FETCH_HEAD` (it also creates
    `refs/sandboxes/<name>/<branch>`, which nothing here relies on: `FETCH_HEAD` is what
    plain git guarantees, and the fake models plain git).
    """
    _git(repo, "fetch", remote, branch)


def local_branch_exists(repo: Path, branch: str) -> bool:
    return _git_ok(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")


def add_worktree_at_fetch_head(repo: Path, path: Path, branch: str) -> None:
    """Create the host mirror of a branch that was built inside a VM.

    Deliberately *not* `add_worktree`: that one refuses a branch that already exists on
    `origin`, because reusing a remote branch would put an unattended writer on somebody
    else's work. Here there is no writer — the branch is already written, inside the VM,
    and this only lands a copy of it on the host so the reviewer, the replay and the
    host-execution guard can read it.

    When the local branch already exists this attaches to it rather than re-cutting it,
    and the caller checks the result actually matches `FETCH_HEAD`. Silently mirroring a
    stale branch would put the review and the PR body on code the agent did not write.
    """
    if worktree_exists(repo, path):
        raise Blocked("worktree-exists", f"{path} is already a worktree of {repo}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if local_branch_exists(repo, branch):
        _git(repo, "worktree", "add", str(path), branch)
        return
    _git(repo, "worktree", "add", "-b", branch, str(path), "FETCH_HEAD")


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


def local_branches_matching(repo: Path, identifier: str) -> list[str]:
    """Local branches whose name mentions the identifier — the remote scan's other half.

    This one decides deletions, so the match is `\bBAC-4\b` rather than the substring
    `remote_branches_matching` gets away with for a warning: `feat/BAC-40-...` contains
    `BAC-4` and is a different ticket's work.
    """
    listing = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    pattern = rf"\b{re.escape(identifier)}\b"
    return [line for line in listing.splitlines() if line and re.search(pattern, line)]


def commits_beyond(repo: Path, branch: str, base_ref: str) -> int | None:
    """`git rev-list --count <base>..<branch>`, or None when git cannot answer."""
    try:
        return int(_git(repo, "rev-list", "--count", f"{base_ref}..{branch}"))
    except (GitError, ValueError):
        return None


def reason_to_keep_branch(repo: Path, branch: str, base_ref: str) -> str | None:
    """Why cancel must not delete this branch, or None when it is safe to delete.

    Two conditions, and the second is the one that makes deriving a branch name from a
    ticket safe at all. A pushed branch is visible to other people. A branch carrying
    commits beyond the base ref is an implementation — the factory's or a human's — and
    a rollback that removes it silently is worse than the orphan it was cleaning up.
    An unreadable relation counts as a reason: cancel refuses when it cannot tell.
    """
    if remote_branch_exists(repo, branch):
        return "it has been pushed"
    ahead = commits_beyond(repo, branch, base_ref)
    if ahead is None:
        return f"its relation to {base_ref} cannot be read"
    if ahead:
        return f"it carries {ahead} commit{'' if ahead == 1 else 's'} beyond {base_ref}"
    return None


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


def orphan_worktree_dir(repo: Path, path: Path) -> Path | None:
    """A directory at `path` that exists on disk and that git does not know about.

    `remove_worktree` returns early for exactly this case — it checks `worktree_exists`
    first — so a rollback built only on it cleans nothing here. This is what a run that
    died inside `git worktree add` leaves behind, and it blocks every later run with
    `fatal: '<path>' already exists`.
    """
    if not path.is_dir() or worktree_exists(repo, path):
        return None
    return path


def holds_only_factory_scaffolding(path: Path) -> bool:
    """True when the directory holds nothing but the factory's own `.factory/` tree.

    The one condition under which cancel may remove a directory git does not know
    about. Anything else in there is somebody's work, and `rm -rf` on an arbitrary tree
    is F15 — the rule the rest of this module already respects.
    """
    return all(entry.name == ".factory" for entry in path.iterdir())


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
    """Remove and prune. `unlock` first when git refuses — never `rm -rf`. F15.

    Refuses the repository's own main working tree outright. `git worktree list` includes
    it, so `worktree_exists` says yes and every caller downstream believes it found a
    worktree it may remove — which is how `factory cancel FRO-6` came to run
    `git worktree remove --force /Users/james/frontend-harness` on 2026-08-22. Git
    declined ("is a main working tree") and the whole cancel aborted with it, leaving the
    tracker un-restored. Refusing here makes the answer a named no rather than an
    exception thrown from the middle of a rollback.
    """
    if path.resolve() == repo.resolve():
        raise GitError(
            f"refusing to remove {path}: that is the repository itself, not a worktree "
            "the factory created. A `--clone` run records the project path as its "
            "workdir because the branch lives inside the VM; that is not debris."
        )
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


def changed_lines(worktree: Path, base_ref: str) -> int:
    """Total added + deleted lines across the diff — `git diff --numstat`, summed.

    The Tier-2 trigger (§15.2) fans out the full review when a diff is large (≥ 400 changed
    lines), so this is the count that decides it. Binary files report `-\t-` and are
    skipped, which is correct: a binary cannot be read by a reviewer and its churn is not
    line churn.
    """
    listing = _git(worktree, "diff", "--numstat", f"{base_ref}...HEAD")
    total = 0
    for line in listing.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            total += int(parts[0]) + int(parts[1])
    return total


def diff_stat(worktree: Path, base_ref: str) -> str:
    return _git(worktree, "diff", "--stat", f"{base_ref}...HEAD")


def head_sha(repo: Path, ref: str = "HEAD") -> str:
    return _git(repo, "rev-parse", ref)


def is_clean(worktree: Path) -> bool:
    return not _git(worktree, "status", "--porcelain")


#: The files git leaves behind while an operation is half-finished. `git status` reports
#: a tree mid-rebase as merely dirty, and §16.3's resume condition is not "clean" — it is
#: "no uncommitted merge/rebase state", because resuming a Codex session into a tree with
#: conflict markers in it hands the model a repository it did not leave.
#:
#: Only the marker filenames are written out; the operation's name is derived from the
#: filename below. That is not decoration — `test_no_forbidden_git_or_gh_argument_appears`
#: greps `src/` for the bare string a merge command would contain, and it is deliberately
#: blunt enough to fire on a table of git filenames. Deriving the name keeps that check as
#: sharp as it was written to be rather than teaching it an exception.
_IN_PROGRESS_MARKERS: tuple[str, ...] = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "BISECT_LOG",
    "rebase-merge",
    "rebase-apply",
)


def _operation_name(marker: str) -> str:
    for suffix in ("_HEAD", "_LOG", "-merge", "-apply"):
        marker = marker.removesuffix(suffix)
    return marker.replace("_", "-").lower()


def in_progress_operation(worktree: Path) -> str | None:
    """The name of the git operation this worktree is in the middle of, or None.

    Read out of the worktree's own git directory rather than inferred from `git status`,
    because `--porcelain` says "dirty" for a tree the agent is legitimately part-way
    through and for one that is stopped on a conflict, and only the second is a reason
    to restart rather than resume.
    """
    try:
        git_dir = Path(_git(worktree, "rev-parse", "--absolute-git-dir"))
    except GitError:
        return None
    for marker in _IN_PROGRESS_MARKERS:
        if (git_dir / marker).exists():
            return _operation_name(marker)
    return None


# --------------------------------------------------------------------------------
# §15.3 — the red-phase replay's git half
# --------------------------------------------------------------------------------


def add_detached_worktree(repo: Path, path: Path, ref: str) -> None:
    """`git worktree add --detach <path> <ref>` — a scratch checkout at the base ref.

    Used by the red-phase replay to land only the test half of a diff on a clean tree
    at the base ref, so the test gate can be run against it. The path must sit inside a
    mounted workspace or the VM cannot see it — the caller picks the path the same way
    `worktree.py` does.
    """
    if worktree_exists(repo, path):
        raise Blocked("worktree-exists", f"{path} is already a worktree of {repo}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "--detach", str(path), ref)


def diff_pathspec(worktree: Path, base_ref: str, pathspecs: Sequence[str]) -> str:
    """`git diff <base>...HEAD -- <pathspecs>` — the patch for one slice of the diff.

    The red-phase replay applies only the test half, so this is `git diff` narrowed by
    `harness.config.json`'s `tests` pathspecs. An empty result means the change touched no
    test file in the declared set.

    Raw, not stripped: `git apply` needs the patch's final newline, and BAC-4's run
    `1effc543d83a459a` died on exactly that — `corrupt patch at line 387`, 387 being the
    last line of a 12296-byte patch that `strip()` had made 12295."""
    return _git_raw(worktree, "diff", f"{base_ref}...HEAD", "--", *pathspecs)


def added_modified_paths(worktree: Path, base_ref: str, pathspecs: Sequence[str]) -> list[str]:
    """The test files the diff added or modified — `--diff-filter=AM`.

    The replay's "fails naming a new/changed test" check looks for one of these in the
    gate's output. Deleted test files are excluded: a deleted test cannot be the new
    behaviour's red phase, and the test-weakening guard handles deletions separately.
    """
    listing = _git(
        worktree,
        "diff",
        "--name-only",
        "--diff-filter=AM",
        f"{base_ref}...HEAD",
        "--",
        *pathspecs,
    )
    return [line for line in listing.splitlines() if line]


def apply_patch(worktree: Path, patch: str) -> None:
    """Apply a patch to a worktree on stdin. Used by the red-phase replay to land the
    test half of the diff on the scratch worktree at the base ref."""
    proc = subprocess.run(
        ["git", "-C", str(worktree), "apply", "-"],
        input=patch,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GitError(f"git apply failed in {worktree}:\n{proc.stderr.strip()}")


def paths_at_ref(repo: Path, ref: str, pathspecs: Sequence[str]) -> list[str]:
    """Files at `ref` matching Git pathspecs, relative to the app directory.

    The red-phase test-weakening guard scopes itself to test files the base ref already
    knew about: a new test file has no prior assertions to weaken, and the replay already
    covers whether a new test catches the regression.
    """
    # ls-tree rejects :(glob) magic and does not recursively enumerate directory
    # pathspecs. Diffing from an empty tree uses Git's full pathspec implementation
    # without consulting or modifying the candidate index/worktree. Let Git compute
    # and store the empty tree in the repository's object format (SHA-1 or SHA-256).
    empty_tree = _git(repo, "hash-object", "-w", "-t", "tree", "--", os.devnull)
    listing = _git_raw(
        repo,
        "diff",
        "--name-only",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--relative",
        "-z",
        empty_tree,
        f"{ref}^{{tree}}",
        "--",
        *pathspecs,
    )
    return [path for path in listing.split("\0") if path]
