"""GitLab delivery from the host — §13.2, §17.4.

The sibling of `github.py`, and the same boundary: **the control plane performs every push
and every merge-request write, from the host, with the host's own `glab` keyring
credential.** No GitLab token enters a sandbox. That is not a GitHub-specific rule that
happens to be reused here — it is §8.7, and `policy.capability_secrets` /
`policy.capability_env_names` would block a run that carried one either way. An in-sandbox
GitLab MCP server or a `GITLAB_*` token in the VM environment is a boundary change, not an
integration.

`glab` is GitLab's own CLI (`gitlab-org/cli`) and is the exact analogue of `gh`: keyring
auth via `glab auth login`, `GITLAB_HOST` for a self-managed instance, argv-shaped
subcommands. Nothing here talks to the REST API directly, for the same reason `github.py`
does not — the credential stays in the CLI's keyring and never lands in this process.

Vocabulary: GitLab calls it a *merge request* and numbers it by `iid` (per-project), not a
pull request numbered globally. The function names stay `*_pr` so `forge.Forge` has one
shape and `steps/deliver.py` does not branch; the argv and the parsing are where the
difference actually lives.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

__all__ = [
    "GitlabError",
    "create_pr",
    "edit_pr",
    "find_pr",
    "pr_number",
    "pr_state",
    "push",
]


class GitlabError(Exception):
    """A `git` or `glab` command that failed, with its output attached."""


# --------------------------------------------------------------------------------
# §17.4 — push (hooks disabled), then the merge request
# --------------------------------------------------------------------------------


def push(worktree: Path, branch: str) -> None:
    """Identical to `github.push` — it is plain `git`, and the forge has no say in it.

    Not shared with `github.py` despite being byte-identical: the adapters are selected as
    whole modules by `forge.for_project`, and a `push` that lived in one of them would make
    the other import its sibling to get it, which is the coupling the split exists to avoid.

    `-o merge_request.create` is deliberately **not** passed. GitLab's push options can open
    the MR as a side effect of the push, which would race the F16 duplicate guard below and
    produce an MR with no §13.2 body. The push pushes; `create_pr` opens.

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
        raise GitlabError(f"git push failed in {worktree}:\n{proc.stderr.strip()}")


def find_pr(worktree: Path, branch: str) -> str | None:
    """The open MR URL for `branch`, or None. F16 — run before create so a second run edits
    rather than opening a duplicate.

    `--source-branch` is GitLab's `--head`. Like the GitHub adapter, a `glab` that cannot
    answer is reported as "no existing MR" so `create_pr` fails loudly instead, which is the
    more useful signal.
    """
    proc = subprocess.run(
        [
            "glab",
            "mr",
            "list",
            "--source-branch",
            branch,
            "--output",
            "json",
            "--per-page",
            "5",
        ],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    # `glab mr list --output json` emits a bare array; older builds wrap it. Both are read
    # rather than one being assumed, because guessing wrong here silently defeats F16 and
    # opens a duplicate MR on every re-delivery.
    if isinstance(rows, dict):
        rows = rows.get("merge_requests") or rows.get("items") or []
    if not rows:
        return None
    url = rows[0].get("web_url") or rows[0].get("url")
    return str(url) if url else None


def create_pr(worktree: Path, *, base: str, head: str, title: str, body_file: Path) -> str:
    """`glab mr create`. The MR opens **ready for review**; merge is still James's (§24.8).

    Not a draft, for the reason `github.create_pr` argues in full: the gates ran green and
    the two-tier review ran clean before delivery is reached, so "work in progress" is a
    false statement about what arrives.

    Three flags are load-bearing beyond the obvious:

    - `--description-file`, so a long body never enters the process table (`--body-file`'s
      counterpart).
    - `--yes`, because `glab mr create` prompts interactively when it is not given every
      answer, and the factory runs unattended — a prompt would hang the run rather than
      fail it.
    - `--remove-source-branch=false`. GitLab can be configured to delete the source branch
      on merge, and `gc._delete_branch` treats "the branch was pushed" as somebody else's
      work. Leaving the deletion to the project's own setting rather than asserting it per
      MR keeps that decision where it already lives.

    `--squash` is not passed either: how the merge lands is part of the merge, and the merge
    is James's.
    """
    proc = subprocess.run(
        [
            "glab",
            "mr",
            "create",
            "--target-branch",
            base,
            "--source-branch",
            head,
            "--title",
            title,
            "--description-file",
            str(body_file),
            "--remove-source-branch=false",
            "--yes",
        ],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GitlabError(f"glab mr create failed:\n{proc.stderr.strip()}")
    # `glab` prints progress lines before the URL, and on some versions prints the URL to
    # stderr. The URL is found by shape rather than by position for that reason.
    return _first_mr_url(proc.stdout) or _first_mr_url(proc.stderr) or ""


def edit_pr(worktree: Path, number: int, *, body_file: Path) -> None:
    """`glab mr update <iid> --description-file`. Idempotent by MR iid (§13.2)."""
    proc = subprocess.run(
        ["glab", "mr", "update", str(number), "--description-file", str(body_file)],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GitlabError(f"glab mr update {number} failed:\n{proc.stderr.strip()}")


_MR_URL = re.compile(r"https?://\S+/-/merge_requests/\d+")
#: GitLab namespaces the MR path under `/-/`, and the iid is per project, so a URL is the
#: only thing that identifies one across projects — which is why `find_pr` returns a URL and
#: this is how the iid is recovered from it.
_MR_IID = re.compile(r"/-/merge_requests/(\d+)")


def _first_mr_url(text: str) -> str | None:
    match = _MR_URL.search(text or "")
    return match.group(0) if match else None


def pr_number(url: str) -> int | None:
    """Extract the MR iid from a `glab` URL, for `edit_pr` on a re-run."""
    match = _MR_IID.search(url)
    return int(match.group(1)) if match else None


def pr_state(cwd: Path, url: str) -> str | None:
    """`OPEN`, `MERGED`, `CLOSED` — or None when `glab` could not answer.

    The states are normalised to GitHub's spelling rather than passed through. `cli.py`
    compares against the literal `"MERGED"` to decide whether `factory complete` may record
    a merge, and a forge adapter that returned GitLab's lowercase `opened`/`merged` would
    make that comparison silently false — a run that James had merged would refuse to
    complete, for ever, with no error. The vocabulary difference is this module's to absorb.

    GitLab's fourth state, `locked`, is a transient merge-in-progress marker; it is reported
    as `OPEN` because it is not yet a merge, and `factory complete` must not record one.

    None is deliberately not "not merged": a `glab` that is unauthenticated, offline or
    pointed at no repo must not look the same as an MR James has not merged, because the
    caller refuses to complete a run on either — but only one of them is worth retrying.
    """
    proc = subprocess.run(
        ["glab", "mr", "view", url, "--output", "json"],
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
    state = str(row.get("state") or "").lower()
    if not state:
        return None
    return {"opened": "OPEN", "locked": "OPEN", "merged": "MERGED", "closed": "CLOSED"}.get(
        state, state.upper()
    )
