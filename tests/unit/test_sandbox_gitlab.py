"""The in-sandbox GitLab adapter — the reversed §13.2 boundary.

These pin the three things that are *security* properties rather than behaviour, because
each one fails silently: the token never reaches argv, the CA is always passed, and the
credential files are created with a restrictive umask. A regression in any of them still
delivers a merge request, which is exactly why a test has to say so.

The adapter is driven with a recording fake rather than a live VM. That is the whole
reason `_Adapter` is a two-method Protocol.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from factory.delivery import sandbox_gitlab as sg

BASE = "https://172.18.194.183"
PROJECT = "nexus-core/ran-ai-agents/nemoclaw-test"
WORKDIR = "/workspace/nemoclaw-dev"
#: Not a credential — a recognisable stand-in the assertions search for. Named `FAKE_PLACEHOLDER`
#: rather than `TOKEN` because ruff S105 reads any constant whose name says "token" as a
#: hardcoded secret, and a blanket noqa here would also hide a real one.
FAKE_PLACEHOLDER = "sbx-cs-NOTAREALPLACEHOLDER"


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class _FakeSandbox:
    """Records every exec, including stdin, so a test can assert on what crossed."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.queue: list[_Completed] = []

    def exec_sync(self, name, argv, *, workdir=None, env=None, timeout=None, stdin=None):  # type: ignore[no-untyped-def]
        self.calls.append(
            {"name": name, "argv": list(argv), "workdir": workdir, "env": env, "stdin": stdin}
        )
        return self.queue.pop(0) if self.queue else _Completed()

    @property
    def all_argv(self) -> str:
        return " ".join(" ".join(c["argv"]) for c in self.calls)

    @property
    def all_env(self) -> str:
        return " ".join(str(c["env"]) for c in self.calls)


@pytest.fixture
def sbx() -> _FakeSandbox:
    return _FakeSandbox()


@pytest.fixture
def forge(sbx: _FakeSandbox) -> sg.SandboxGitlabForge:
    return sg.SandboxGitlabForge(
        sbx, "factory-build-nemoclaw-dev", base_url=BASE, project_path=PROJECT, workdir=WORKDIR
    )


# --------------------------------------------------------------------------------
# the credential must not be reachable by an agent that can read /proc
# --------------------------------------------------------------------------------


def test_the_token_never_appears_in_argv_or_env(sbx: _FakeSandbox) -> None:
    """argv is world-readable in /proc on Linux, and the agent shares the VM.

    This is the whole reason `install_credential` writes a header *file* instead of
    passing `-e PRIVATE_TOKEN=...`. An author who "simplifies" that back to an env var
    re-opens the hole without changing a single visible behaviour.
    """
    sg.install_credential(
        sbx, "factory-build-nemoclaw-dev", ca_pem="---PEM---", placeholder=FAKE_PLACEHOLDER
    )

    assert FAKE_PLACEHOLDER not in sbx.all_argv, "the token reached argv"
    assert FAKE_PLACEHOLDER not in sbx.all_env, "the token reached the environment"
    # It crossed on stdin, which is the only channel that is not in /proc.
    assert any(FAKE_PLACEHOLDER in str(c["stdin"] or "") for c in sbx.calls)


def test_the_credential_files_are_created_before_they_are_written(sbx: _FakeSandbox) -> None:
    # `umask 077` has to precede the redirect: `cat > f` creates the file first, so a
    # umask set afterwards leaves a window where the token is world-readable.
    sg.install_credential(
        sbx, "factory-build-nemoclaw-dev", ca_pem="---PEM---", placeholder=FAKE_PLACEHOLDER
    )

    for call in sbx.calls:
        script = call["argv"][-1]
        assert script.startswith("umask 077;"), f"no umask before the write: {script}"


def test_install_raises_without_leaking_the_token(sbx: _FakeSandbox) -> None:
    sbx.queue.append(_Completed(returncode=1, stderr="permission denied"))

    with pytest.raises(sg.SandboxGitlabError) as exc:
        sg.install_credential(
            sbx, "factory-build-nemoclaw-dev", ca_pem="p", placeholder=FAKE_PLACEHOLDER
        )

    assert FAKE_PLACEHOLDER not in str(exc.value)


def test_revoke_removes_both_files(sbx: _FakeSandbox) -> None:
    # A sandbox outlives one run (§16.5 keeps it for sandbox_idle_hours), so a token left
    # behind is a token available to whatever runs in it next.
    sg.revoke_credential(sbx, "factory-build-nemoclaw-dev")

    script = sbx.calls[0]["argv"][-1]
    assert script.startswith("rm -f")
    assert sg.CA_PATH in script
    assert sg.HEADER_PATH in script


# --------------------------------------------------------------------------------
# every call must pin the CA — the image does not trust the internal issuer
# --------------------------------------------------------------------------------


def test_every_api_call_pins_the_ca_and_reads_the_header_from_a_file(
    sbx: _FakeSandbox, forge: sg.SandboxGitlabForge, tmp_path: Path
) -> None:
    """Measured 2026-08-31 in factory-build-python-harness-2: without --cacert, curl
    exits 60; with it the same request returns 401. A call that forgets it does not
    degrade, it fails outright — but it fails at delivery, after the model spend."""
    body = tmp_path / "body.md"
    body.write_text("Fixes BAC-9\n")
    sbx.queue.append(_Completed(stdout=json.dumps([])))
    sbx.queue.append(_Completed(stdout=json.dumps({"web_url": f"{BASE}/x/-/merge_requests/4"})))

    forge.find_pr(tmp_path, "feat/x")
    forge.create_pr(tmp_path, base="main", head="feat/x", title="t", body_file=body)

    for call in sbx.calls:
        argv = call["argv"]
        assert "--cacert" in argv
        assert sg.CA_PATH in argv
        assert f"@{sg.HEADER_PATH}" in argv


def test_the_push_pins_the_ca_and_keeps_the_token_out_of_the_remote_url(
    sbx: _FakeSandbox, forge: sg.SandboxGitlabForge, tmp_path: Path
) -> None:
    # A token in the remote URL lands in .git/config, in reflogs, and in whatever error
    # message git prints on a bad push.
    forge.push(tmp_path, "feat/x")

    script = sbx.calls[0]["argv"][-1]
    assert f"http.sslCAInfo={sg.CA_PATH}" in script
    assert f"cat {sg.HEADER_PATH}" in script
    assert "http.extraHeader=" in script
    assert "@172.18.194.183" not in script, "credential embedded in the remote URL"
    assert "core.hooksPath=/dev/null" in script


# --------------------------------------------------------------------------------
# the same silent-failure translations the host adapter pins
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("api_state", "expected"),
    [("opened", "OPEN"), ("merged", "MERGED"), ("closed", "CLOSED"), ("locked", "OPEN")],
)
def test_pr_state_is_normalised_to_the_github_spelling(
    sbx: _FakeSandbox, forge: sg.SandboxGitlabForge, api_state: str, expected: str
) -> None:
    # `cli.py` compares against the literal "MERGED". Passing GitLab's lowercase through
    # would make a run James had merged refuse to complete, for ever, with no error.
    sbx.queue.append(_Completed(stdout=json.dumps({"state": api_state})))

    assert forge.pr_state(Path("/x"), f"{BASE}/g/p/-/merge_requests/7") == expected


def test_pr_state_is_none_rather_than_not_merged_when_the_call_fails(
    sbx: _FakeSandbox, forge: sg.SandboxGitlabForge
) -> None:
    sbx.queue.append(_Completed(returncode=22, stdout='{"message":"404"}'))

    assert forge.pr_state(Path("/x"), f"{BASE}/g/p/-/merge_requests/7") is None


def test_find_pr_is_none_when_the_call_fails_so_create_fails_loudly_instead(
    sbx: _FakeSandbox, forge: sg.SandboxGitlabForge, tmp_path: Path
) -> None:
    sbx.queue.append(_Completed(returncode=22, stdout='{"message":"403 Forbidden"}'))

    assert forge.find_pr(tmp_path, "feat/x") is None


def test_pr_number_reads_the_iid_and_ignores_a_github_url(
    forge: sg.SandboxGitlabForge,
) -> None:
    assert forge.pr_number(f"{BASE}/group/sub/proj/-/merge_requests/42") == 42
    assert forge.pr_number("https://github.com/o/r/pull/42") is None


def test_the_project_path_is_url_encoded_for_the_api(
    sbx: _FakeSandbox, forge: sg.SandboxGitlabForge, tmp_path: Path
) -> None:
    # A subgroup path must be %2F-encoded or the API reads it as a nested route and 404s
    # — which reads exactly like a permissions problem.
    sbx.queue.append(_Completed(stdout=json.dumps([])))

    forge.find_pr(tmp_path, "feat/x")

    url = sbx.calls[0]["argv"][-1]
    assert "nexus-core%2Fran-ai-agents%2Fnemoclaw-test" in url
    assert "nexus-core/ran-ai-agents" not in url


def test_a_failed_api_call_keeps_gitlabs_error_body(
    sbx: _FakeSandbox, forge: sg.SandboxGitlabForge, tmp_path: Path
) -> None:
    """`--fail-with-body`, not `--fail`. The difference is "403" versus
    "403: insufficient_scope", which is the difference between an hour of debugging and
    a glance."""
    body = tmp_path / "b.md"
    body.write_text("x")
    sbx.queue.append(_Completed(returncode=22, stdout='{"message":"insufficient_scope"}'))

    with pytest.raises(sg.SandboxGitlabError, match="insufficient_scope"):
        forge.create_pr(tmp_path, base="main", head="f", title="t", body_file=body)

    assert "--fail-with-body" in sbx.calls[0]["argv"]
