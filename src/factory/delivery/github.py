"""GitHub delivery from the host — §13.2, §17.4.

The control plane performs every push and every PR write, from the host, with the existing
`gh` keyring credential. The sandbox gets no git remote credential. Every command here is an
argv list run with `subprocess`, never a shell string — the same rule the rest of the control
plane follows, and the reason `shell=True` appears nowhere in this repository.

One of two forge adapters behind `forge.for_project`; `gitlab.py` is the other and answers
the same six names. The §13.2 body they both render lives in `body.py`, because none of that
evidence is GitHub's.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

__all__ = [
    "GithubError",
    "create_pr",
    "edit_pr",
    "find_pr",
    "pr_number",
    "pr_state",
    "push",
]


class GithubError(Exception):
    """A `git` or `gh` command that failed, with its output attached."""


# --------------------------------------------------------------------------------
# §17.4 — push (hooks disabled), then the PR
# --------------------------------------------------------------------------------


def push(worktree: Path, branch: str) -> None:
    """`git -C <worktree> -c core.hooksPath=/dev/null push -u origin <branch>`.

    Hooks are disabled on the push path because the host's hooks are for the agent's
    uncommitted edits, not for the factory's own commit; a hook firing here would be the
    wrong layer and could block a push the gates already cleared.
    """
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(worktree),
            "-c",
            "core.hooksPath=/dev/null",
            "push",
            "-u",
            "origin",
            branch,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GithubError(f"git push failed in {worktree}:\n{proc.stderr.strip()}")


def find_pr(worktree: Path, branch: str) -> str | None:
    """The open PR URL for `branch`, or None. F16 — run before create so a second run edits
    rather than opening a duplicate."""
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--head",
            branch,
            "--json",
            "number,url",
            "--limit",
            "5",
        ],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        # `gh` not authenticated, or no repo — treat as "no existing PR" and let create fail
        # loudly instead, which is the more useful signal.
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return rows[0]["url"] if rows else None


def create_pr(worktree: Path, *, base: str, head: str, title: str, body_file: Path) -> str:
    """`gh pr create`. The PR opens **ready for review**; merge is still James's (§24.8).

    Not `--draft`. Draft is GitHub's word for "work in progress, do not review yet", and
    that is not what arrives: the gates ran green and the two-tier review ran clean before
    delivery is reached at all. A draft cannot take a review request and cannot be merged,
    so the un-draft keystroke was a manual step carrying no information, sitting in front
    of the two acts (review, merge) that are James's anyway.

    Measured 2026-08-23, so it is not re-derived: draft-ness gates neither workflow in
    either harness. `ci.yml` is `on: pull_request:` with no type filter and runs on drafts;
    `agent-review.yml` is `on: pull_request: types: [labeled]` behind
    `if label.name == 'agent-review'`, deliberately, because it is billed model spend. No
    workflow fires on `ready_for_review` anywhere. Ready-for-review is therefore an honest
    label on the PR, not a trigger — and §5.3's `merge-is-james` still keeps the merge his.

    `--base` is the project's base branch (`v2`, never `main`); `--head` is the run's branch.
    The body comes from a file (`--body-file`) so a long body never enters the process table.
    """
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            base,
            "--head",
            head,
            "--title",
            title,
            "--body-file",
            str(body_file),
        ],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GithubError(f"gh pr create failed:\n{proc.stderr.strip()}")
    # `gh pr create` prints the new PR's URL to stdout.
    return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""


def edit_pr(worktree: Path, number: int, *, body_file: Path) -> None:
    """`gh pr edit <number> --body-file`. Idempotent by PR number (§13.2)."""
    proc = subprocess.run(
        ["gh", "pr", "edit", str(number), "--body-file", str(body_file)],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GithubError(f"gh pr edit {number} failed:\n{proc.stderr.strip()}")


def pr_number(url: str) -> int | None:
    """Extract the PR number from a `gh` URL, for `edit_pr` on a re-run."""
    import re

    match = re.search(r"/pull/(\d+)", url)
    return int(match.group(1)) if match else None


def pr_state(cwd: Path, url: str) -> str | None:
    """`OPEN`, `MERGED`, `CLOSED` — or None when `gh` could not answer.

    The evidence behind `awaiting_human -> completed`. None is deliberately not
    "not merged": a `gh` that is unauthenticated, offline or pointed at no repo must not
    look the same as a PR James has not merged, because the caller refuses to complete a
    run on either — but only one of them is worth retrying.
    """
    proc = subprocess.run(
        ["gh", "pr", "view", url, "--json", "state"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        row = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    state = row.get("state")
    return str(state) if state else None
