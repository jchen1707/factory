"""GitLab delivery **from inside the sandbox** — the §13.2 boundary, deliberately reversed.

Read this before changing anything here. The rest of the control plane is built on the
rule that no credential granting a capability enters a sandbox, and that every forge write
happens on the host (`delivery/github.py`, `delivery/gitlab.py`). This module is the
exception James asked for on 2026-08-31: for a project that opts in, the agent's own
sandbox pushes the branch and opens the merge request.

**What that costs, stated once, here, where someone changing it will read it:** an
unattended agent gains the ability to write to company repositories. It does **not** gain a
credential. `sbx secret set-custom` keeps the real `glpat-…` on the host and gives the
sandbox a placeholder; the proxy swaps it into the request headers on the way out. Measured
2026-08-31 in `factory-build-python-harness-2` against `/api/v4/version`:

    anonymous                                    -> 401
    -H "PRIVATE-TOKEN: sbx-cs-AFlXreOjVPUyNl4k"  -> 200

So the placeholder is a capability token bounded to one host and one sandbox scope. Exfiltrating
it buys nothing: off-proxy, or aimed at any other host, it is a meaningless string. That is
the whole reason this module is defensible at all, and it is the property to preserve — a
future author who "simplifies" this to a real token in the VM has made a materially
different change from the one that was approved.

The placeholder is read from `sbx secret ls` on the host and passed in by the caller.
`set-custom --env` is documented to export it into the sandbox, but measured 2026-08-31 it
never reaches an `sbx exec` session — absent before and after a restart — and the command
is marked EXPERIMENTAL. Reading it host-side is both more reliable and one fewer moving
part in the delivery path.

Why REST-over-curl rather than `glab`, measured 2026-08-31 in `factory-build-python-harness-2`:

    glab: MISSING        gh: /usr/bin/gh      curl: /usr/bin/curl     git: /usr/bin/git

`glab` is not in the sandbox image, and baking it in is a *template* change, which §9.1
fixes at creation — every factory sandbox would need a new name. `curl` is already there,
so the same six operations are spoken directly to `/api/v4`. The host adapter in
`gitlab.py` stays the tested default; this is the opt-in sibling.

Two facts the image forces, both measured in the same sandbox:

- The instance's CA is **not** in the image trust store, so every call passes `--cacert`
  at a path the caller wrote into the VM. Without it curl exits 60; with it the same
  request returns 401 rather than a TLS error, which is what proves the path works.
- There is **no `~/.ssh`**, so a push cannot use the host's SSH key. The remote is
  rewritten to HTTPS for the push only, with the token supplied out of band.

The token is never an argv element and never an environment variable of the *agent's*
process: it is written to a file inside the VM by `install_credential` and read back by
`curl --header @file` / git's `credential.helper`. argv is world-readable in `/proc` on
Linux, and an agent that can run `ps` is an agent that can read a token passed that way.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "CA_PATH",
    "HEADER_PATH",
    "SandboxGitlabError",
    "SandboxGitlabForge",
    "install_credential",
]


class SandboxGitlabError(Exception):
    """A `git` or `curl` command inside the sandbox that failed, with its output."""


#: Fixed paths inside the VM. Not configurable: two of them hold a credential and a trust
#: anchor, and a path that varies per project is a path that gets logged somewhere
#: eventually.
#:
#: Under the agent's home rather than `/tmp`, deliberately. `/tmp` is world-writable, so a
#: second process in the VM could pre-create the header path and read what lands in it —
#: `umask 077` protects the *contents* of a file this process creates, not the directory
#: it sits in. `/home/agent` is the same home `UV_PROJECT_ENVIRONMENT` uses in
#: `projects.toml`, so it is a path this codebase already relies on existing.
_CRED_DIR = "/home/agent/.factory-forge"
CA_PATH = f"{_CRED_DIR}/ca.pem"
HEADER_PATH = f"{_CRED_DIR}/header"


class _Adapter(Protocol):
    """The slice of `SandboxAdapter` this module uses. Narrow on purpose — it is what
    makes the tests able to drive this with a recording fake rather than a live VM."""

    def exec_sync(
        self,
        name: str,
        argv: Sequence[str],
        *,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int | None = None,
        stdin: str | None = None,
    ) -> Any: ...


def install_credential(adapter: _Adapter, sandbox: str, *, ca_pem: str, placeholder: str) -> None:
    """Write the CA and the `PRIVATE-TOKEN` header file into the VM, `0600`.

    `placeholder` is the `sbx-cs-…` substitution token from `sbx secret ls`, **never** a
    real credential. Passing a `glpat-…` here would work, and would silently turn this into
    the much wider bypass the module docstring says it is not. The parameter is named for
    what it must be so that a call site passing the wrong thing reads wrong.

    Called once per run, before delivery. Both values arrive on **stdin**, never in argv:
    `sbx exec ... -e X=...` would put the value in the *host* process table, and a shell
    heredoc would put it in the VM's. `sh -c 'umask 077; mkdir -m 700 …; cat > path'`
    creates the file unreadable to anything but the agent user before a byte is written.

    The header file is why this is not simply an argument. `curl --header @f` reads the
    name and value from the file, so even the placeholder stays out of `/proc/*/cmdline`,
    where any other process in the VM could read it.
    """
    for path, content in (
        (CA_PATH, ca_pem),
        (HEADER_PATH, f"PRIVATE-TOKEN: {placeholder}\n"),
    ):
        completed = adapter.exec_sync(
            sandbox,
            # `mkdir -m 700` before the umask matters: the umask governs the file, the
            # mode governs the directory it lands in, and only the pair keeps another
            # process in the VM from pre-creating the path and reading what arrives.
            [
                "sh",
                "-c",
                f"umask 077; mkdir -p -m 700 {shlex.quote(_CRED_DIR)} && cat > {shlex.quote(path)}",
            ],
            stdin=content,
        )
        if not completed.ok:
            # The token is not in `completed`, but a caller logging this exception is
            # logging the *path*, not the value. Keep it that way.
            raise SandboxGitlabError(
                f"could not install {path} in {sandbox}: {completed.stderr.strip()}"
            )


def revoke_credential(adapter: _Adapter, sandbox: str) -> None:
    """Remove both files. Best-effort, called in a finally: a sandbox outlives one run
    (§16.5 keeps it for `sandbox_idle_hours`), so a token left behind is a token
    available to whatever runs next in it."""
    adapter.exec_sync(
        sandbox, ["sh", "-c", f"rm -f {shlex.quote(CA_PATH)} {shlex.quote(HEADER_PATH)}"]
    )


_MR_IID = re.compile(r"/-/merge_requests/(\d+)")


def _api_argv(method: str, url: str, *, fields: Mapping[str, str] | None = None) -> list[str]:
    """A curl argv for one API call.

    `--fail-with-body` so a 4xx is a non-zero exit *and* still prints GitLab's error
    message; plain `--fail` swallows the body, which is the difference between "403" and
    "403: insufficient_scope". `--data-urlencode` keeps branch names and MR titles from
    being reinterpreted as query syntax.
    """
    argv = [
        "curl",
        "--silent",
        "--show-error",
        "--fail-with-body",
        "--cacert",
        CA_PATH,
        "--header",
        f"@{HEADER_PATH}",
        "--request",
        method,
        "--max-time",
        "30",
    ]
    for key, value in (fields or {}).items():
        argv += ["--data-urlencode", f"{key}={value}"]
    argv.append(url)
    return argv


class SandboxGitlabForge:
    """The `Forge` shape, executed inside `sandbox` instead of on the host.

    Answers the same six names as `delivery/gitlab.py` so `steps/deliver.py` does not
    branch: which object it holds is decided once, by `delivery/forge.py`.

    `project_path` is the URL-encoded `group/subgroup/project` the API addresses. It comes
    from the registry rather than being parsed out of the git remote, for the same reason
    `forge.for_project` reads the forge from the registry: a repository that has moved
    should fail loudly rather than have a path inferred for it.
    """

    def __init__(
        self,
        adapter: _Adapter,
        sandbox: str,
        *,
        base_url: str,
        project_path: str,
        workdir: str,
    ) -> None:
        self._adapter = adapter
        self._sandbox = sandbox
        self._base = base_url.rstrip("/")
        self._project = project_path
        self._workdir = workdir

    # -- helpers ---------------------------------------------------------------

    @property
    def _api(self) -> str:
        # `%2F` rather than the numeric id: the numeric id is one more round trip and one
        # more thing to be stale, and the encoded path is what the API documents.
        return f"{self._base}/api/v4/projects/{self._project.replace('/', '%2F')}"

    def _run(self, argv: Sequence[str], *, what: str) -> str:
        completed = self._adapter.exec_sync(
            self._sandbox, list(argv), workdir=self._workdir, timeout=120
        )
        if not completed.ok:
            raise SandboxGitlabError(
                f"{what} failed in sandbox {self._sandbox}: "
                f"{(completed.stderr or completed.stdout).strip()}"
            )
        return completed.stdout

    @staticmethod
    def _json(payload: str) -> Any:
        try:
            return json.loads(payload or "null")
        except json.JSONDecodeError as exc:
            raise SandboxGitlabError(f"GitLab returned a non-JSON body: {payload[:200]}") from exc

    # -- the Forge surface -----------------------------------------------------

    def push(self, worktree: Path, branch: str) -> None:
        """Push from inside the VM over HTTPS, authenticating from the header file.

        `worktree` is accepted and ignored: the sandbox has the workspace mounted at
        `self._workdir`, and the host path is not the VM path for a `--clone` sandbox.
        Taking the argument keeps the signature identical to the host adapters, which is
        what lets `deliver` stay branchless.

        The credential reaches git through `http.extraHeader` read from the same file, so
        it is not in the remote URL — a token in a URL lands in `.git/config`, in reflogs,
        and in any error message git prints.

        `core.hooksPath=/dev/null` for the reason `gitlab.push` gives on the host: the
        hooks belong to the agent's edits, not to the factory's push.
        """
        remote = f"{self._base}/{self._project}.git"
        script = (
            f"set -e; cd {shlex.quote(self._workdir)}; "
            f"git -c core.hooksPath=/dev/null "
            f"-c http.sslCAInfo={shlex.quote(CA_PATH)} "
            f'-c http.extraHeader="$(cat {shlex.quote(HEADER_PATH)})" '
            f"push -u {shlex.quote(remote)} {shlex.quote(branch)}"
        )
        completed = self._adapter.exec_sync(
            self._sandbox, ["sh", "-c", script], workdir=self._workdir, timeout=300
        )
        if not completed.ok:
            raise SandboxGitlabError(
                f"git push failed in sandbox {self._sandbox}: {completed.stderr.strip()}"
            )

    def find_pr(self, worktree: Path, branch: str) -> str | None:
        """The open MR URL for `branch`, or None. F16 — the duplicate guard.

        A failed call returns None, matching both host adapters: `create_pr` then fails
        loudly, which is the more useful signal than a delivery that stops here.
        """
        url = f"{self._api}/merge_requests?state=opened&source_branch={branch}"
        try:
            rows = self._json(self._run(_api_argv("GET", url), what="merge_requests list"))
        except SandboxGitlabError:
            return None
        if not isinstance(rows, list) or not rows:
            return None
        web_url = rows[0].get("web_url")
        return str(web_url) if web_url else None

    def create_pr(
        self, worktree: Path, *, base: str, head: str, title: str, body_file: Path
    ) -> str:
        """`POST /merge_requests`. Opens **ready for review**; merge is still James's.

        The description is read from `body_file` **on the host** and sent as a form field,
        rather than the file being copied into the VM: the body carries the gate report and
        the review summary, and `artifacts.scan_for_secrets` has already run over it
        host-side in `deliver`. Copying it in would put that evidence inside the sandbox
        for no gain.

        `remove_source_branch` is not sent, for the reason the host adapter gives: how the
        merge lands is part of the merge, and the merge is a human's.
        """
        description = body_file.read_text(encoding="utf-8")
        argv = _api_argv(
            "POST",
            f"{self._api}/merge_requests",
            fields={
                "source_branch": head,
                "target_branch": base,
                "title": title,
                "description": description,
            },
        )
        row = self._json(self._run(argv, what="merge request create"))
        if not isinstance(row, dict) or not row.get("web_url"):
            raise SandboxGitlabError(f"merge request create returned no web_url: {row}")
        return str(row["web_url"])

    def edit_pr(self, worktree: Path, number: int, *, body_file: Path) -> None:
        """`PUT /merge_requests/:iid`. Idempotent by iid (§13.2)."""
        argv = _api_argv(
            "PUT",
            f"{self._api}/merge_requests/{number}",
            fields={"description": body_file.read_text(encoding="utf-8")},
        )
        self._run(argv, what=f"merge request update {number}")

    def pr_number(self, url: str) -> int | None:
        """The MR iid from its URL. Same shape as the host adapter."""
        match = _MR_IID.search(url)
        return int(match.group(1)) if match else None

    def pr_state(self, cwd: Path, url: str) -> str | None:
        """`OPEN`, `MERGED`, `CLOSED` — or None when the call could not be made.

        Normalised to GitHub's spelling for the reason `gitlab.pr_state` documents at
        length: `cli.py` compares against the literal `"MERGED"`, and an adapter returning
        GitLab's lowercase `merged` would make a run James had merged refuse to complete,
        for ever, with no error.
        """
        iid = self.pr_number(url)
        if iid is None:
            return None
        try:
            row = self._json(
                self._run(_api_argv("GET", f"{self._api}/merge_requests/{iid}"), what="mr view")
            )
        except SandboxGitlabError:
            return None
        if not isinstance(row, dict):
            return None
        state = str(row.get("state") or "").lower()
        if not state:
            return None
        return {"opened": "OPEN", "locked": "OPEN", "merged": "MERGED", "closed": "CLOSED"}.get(
            state, state.upper()
        )
