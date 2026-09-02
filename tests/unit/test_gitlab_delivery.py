"""The GitLab adapter — §13.2.

The three places `gitlab.py` differs from `github.py` in a way that is *silent* when wrong:
the state vocabulary, the MR URL shape, and the JSON `glab mr list` returns. Each one fails
by producing a plausible wrong answer rather than an error, which is why they are pinned
here instead of being left to the first real merge request.

`subprocess.run` is patched rather than `glab` being installed: these assert the adapter's
own translation, not GitLab's behaviour.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from factory.delivery import gitlab

#: Never touched — `subprocess.run` is patched in every test here, so this only has to be a
#: path-shaped value the adapter can stringify.
WORKTREE = Path("/nonexistent/worktree")


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Glab:
    """Records argv and replays queued results, so a test can assert on both."""

    def __init__(self) -> None:
        self.argv: list[list[str]] = []
        self.queue: list[_Proc] = []

    def __call__(self, argv: list[str], **_: Any) -> _Proc:
        self.argv.append(list(argv))
        return self.queue.pop(0) if self.queue else _Proc()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> _Glab:
    fake = _Glab()
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


# --------------------------------------------------------------------------------
# the state vocabulary — the failure that has no error message
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("glab_state", "expected"),
    [
        ("opened", "OPEN"),
        ("merged", "MERGED"),
        ("closed", "CLOSED"),
        # GitLab's transient merge-in-progress marker. Reported as OPEN because it is not
        # yet a merge, and `factory complete` must not record one.
        ("locked", "OPEN"),
    ],
)
def test_pr_state_is_normalised_to_the_github_spelling(
    calls: _Glab, glab_state: str, expected: str
) -> None:
    """`cli.py` compares against the literal `"MERGED"`.

    An adapter that passed GitLab's lowercase `merged` straight through would make that
    comparison silently false: a run James had merged would refuse to complete, for ever,
    with no error anywhere. Nothing else in the codebase would notice.
    """
    calls.queue.append(_Proc(stdout=json.dumps({"state": glab_state})))

    assert gitlab.pr_state(WORKTREE, "https://gl.invalid/g/p/-/merge_requests/7") == expected


def test_pr_state_is_none_rather_than_not_merged_when_glab_cannot_answer(
    calls: _Glab,
) -> None:
    # An unauthenticated or offline `glab` must not look like an MR James has not merged.
    # The caller refuses to complete on either, but only one of them is worth retrying.
    calls.queue.append(_Proc(returncode=1, stderr="not authenticated"))

    assert gitlab.pr_state(WORKTREE, "https://gl.invalid/g/p/-/merge_requests/7") is None


# --------------------------------------------------------------------------------
# the URL shape
# --------------------------------------------------------------------------------


def test_pr_number_reads_the_iid_from_a_gitlab_url() -> None:
    # GitLab namespaces the MR under `/-/`, and the iid is per project. GitHub's
    # `/pull/(\d+)` matches nothing here, so an adapter reusing it would return None and
    # `deliver` would silently open a duplicate MR on every re-delivery instead of editing.
    assert gitlab.pr_number("https://gl.invalid/group/sub/proj/-/merge_requests/42") == 42
    assert gitlab.pr_number("https://github.com/o/r/pull/42") is None


def test_create_pr_finds_the_url_when_glab_prints_it_to_stderr(calls: _Glab) -> None:
    # `glab` prints progress lines around the URL and some versions put it on stderr. The
    # URL is found by shape for that reason; taking the last stdout line would return a
    # progress message as the PR URL and record it on the run row.
    calls.queue.append(
        _Proc(
            stdout="Creating merge request for x into v2\n",
            stderr="https://gl.invalid/o/r/-/merge_requests/9\n",
        )
    )

    url = gitlab.create_pr(WORKTREE, base="v2", head="x", title="t", body_file=WORKTREE / "body.md")

    assert url == "https://gl.invalid/o/r/-/merge_requests/9"


# --------------------------------------------------------------------------------
# argv — the flags that are load-bearing
# --------------------------------------------------------------------------------


def test_create_pr_never_prompts_and_never_opens_a_draft(calls: _Glab) -> None:
    gitlab.create_pr(WORKTREE, base="v2", head="x", title="t", body_file=WORKTREE / "body.md")

    argv = calls.argv[0]
    # Unattended: `glab mr create` prompts when it is not given every answer, and a prompt
    # would hang the run rather than fail it.
    assert "--yes" in argv
    # Not a draft — the gates ran green and the review ran clean before delivery is reached.
    assert "--draft" not in argv
    # How the merge lands is part of the merge, and the merge is James's.
    assert "--squash" not in argv
    # The body goes through a file so a long body never enters the process table.
    assert "--description-file" in argv
    assert argv[:3] == ["glab", "mr", "create"]


def test_find_pr_reads_a_bare_array_and_a_wrapped_object(
    calls: _Glab, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Guessing wrong about which shape `glab` emits defeats F16 silently: `find_pr` returns
    # None, `deliver` takes the create branch, and a duplicate MR opens on every re-run.
    row = {"web_url": "https://gl.invalid/o/r/-/merge_requests/3"}
    for payload in (json.dumps([row]), json.dumps({"merge_requests": [row]})):
        calls.queue.append(_Proc(stdout=payload))
        assert gitlab.find_pr(WORKTREE, "x") == row["web_url"]


def test_find_pr_is_none_when_glab_fails(calls: _Glab) -> None:
    calls.queue.append(_Proc(returncode=1))

    assert gitlab.find_pr(WORKTREE, "x") is None


def test_push_passes_no_gitlab_push_option(calls: _Glab) -> None:
    """`git push -o merge_request.create` would open an MR with no §13.2 body.

    The boundary test greps for the string; this one asserts the argv the adapter actually
    builds, so a push option added through a variable rather than a literal still fails.
    """
    gitlab.push(WORKTREE, "branch-x")

    argv = calls.argv[0]
    assert "-o" not in argv
    assert not any(a.startswith("merge_request.") for a in argv)
    assert "core.hooksPath=/dev/null" in argv
