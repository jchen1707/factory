"""The `sbx` implementation of the sandbox contract — §8.5.

Only commands verified against `sbx` v0.38.0 appear here. Two absences are deliberate:
`--branch` does not exist on `create` or `run` in this version (the factory makes its
own worktree), and `sbx template save` is forbidden outright because it captures the
entire filesystem including secrets.

`sbx reset`, `sbx logout` and any operation on a sandbox this adapter did not create
are refused by `policy.assert_factory_sandbox`, called at the adapter rather than at
the caller. A guard the caller can forget to call is not a guard.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from factory.policy import assert_factory_sandbox, assert_no_skip_verify, capability_secrets
from factory.sandbox.base import (
    Completed,
    RunHandle,
    RunResult,
    RunStatus,
    SandboxSpec,
)

__all__ = ["SbxAdapter", "SbxError", "create_argv", "exec_argv"]

#: How long after the last heartbeat a run with no `exit` file is presumed orphaned.
#: The wrapper beats every 20 s, so three missed beats plus slack.
ORPHAN_AFTER_SECONDS = 90


class SbxError(Exception):
    """An `sbx` command that failed, with its output attached."""


def create_argv(spec: SandboxSpec) -> list[str]:
    """The exact `sbx create` command line for a spec. Golden-tested.

    Order matters only for readability; correctness is that every creation-time
    decision — template, kits, static MCP, deny rules, workspaces — appears here and
    nowhere else, because none of them can be changed afterwards.
    """
    assert_factory_sandbox(spec.name)
    argv = ["sbx", "create", "codex", "--name", spec.name]
    if spec.template:
        argv += ["-t", spec.template]
    for kit in spec.kits:
        argv += ["--kit", kit]
    if spec.static_mcp:
        argv += ["--static-mcp", ",".join(spec.static_mcp)]
    for host in spec.deny_network:
        argv += ["--deny-network", host]
    if spec.memory:
        argv += ["-m", spec.memory]
    if spec.cpus:
        argv += ["--cpus", str(spec.cpus)]
    if spec.clone:
        argv.append("--clone")
    if not spec.share_skills:
        argv.append("--no-share-skills")
    argv += [workspace.as_argument() for workspace in spec.workspaces]
    return argv


def exec_argv(
    name: str,
    argv: Sequence[str],
    *,
    workdir: str | None = None,
    env: Mapping[str, str] | None = None,
    detach: bool = False,
) -> list[str]:
    """The exact `sbx exec` command line. Golden-tested."""
    assert_factory_sandbox(name)
    if env:
        assert_no_skip_verify(env)
    out = ["sbx", "exec"]
    if detach:
        out.append("-d")
    if workdir:
        out += ["-w", workdir]
    for key, value in sorted((env or {}).items()):
        out += ["-e", f"{key}={value}"]
    out += [name, *argv]
    return out


class SbxAdapter:
    """Create-or-attach, detached execution, and polling by filesystem."""

    def __init__(self, *, timeout: int = 900) -> None:
        self.timeout = timeout

    # -- plumbing -----------------------------------------------------------------

    def _run(
        self, argv: Sequence[str], *, timeout: int | None = None, stdin: str | None = None
    ) -> Completed:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout or self.timeout,
            input=stdin,
        )
        return Completed(tuple(argv), proc.returncode, proc.stdout, proc.stderr)

    # -- lifecycle ----------------------------------------------------------------

    def exists(self, name: str) -> bool:
        """`sbx inspect` is non-zero when the sandbox is absent.

        Undocumented in `sbx --help` and present in the binary, which P0-5 confirmed
        by running it: it returns exactly the fields `inspect()` needs.
        """
        return self._run(["sbx", "inspect", name], timeout=60).ok

    def inspect(self, name: str) -> dict[str, Any]:
        result = self._run(["sbx", "inspect", name, "--json"], timeout=60)
        if not result.ok:
            raise SbxError(f"sbx inspect {name} failed: {result.stderr.strip()}")
        parsed: dict[str, Any] = json.loads(result.stdout)
        return parsed

    def ensure(self, spec: SandboxSpec) -> None:
        """Create only when absent; never re-create to change a spec.

        `csbx` passes its template and static MCP set on the first run only, and this
        does the same — but a spec that no longer matches an existing sandbox is an
        error here rather than a shrug, because those three fields are fixed at
        creation and pretending otherwise would be the silent mismatch §9.1 warns of.
        """
        assert_factory_sandbox(spec.name)
        if self.exists(spec.name):
            self._assert_spec_matches(spec)
            return
        result = self._run(create_argv(spec))
        if not result.ok:
            raise SbxError(f"sbx create for {spec.name} failed:\n{result.stdout}\n{result.stderr}")

    def _assert_spec_matches(self, spec: SandboxSpec) -> None:
        info = self.inspect(spec.name)
        workspace = str(info.get("workspace", ""))
        wanted = [str(w.path) for w in spec.workspaces]
        if wanted and not any(w in workspace for w in wanted):
            raise SbxError(
                f"sandbox {spec.name} exists with workspace {workspace!r}, which does "
                f"not include {wanted}. The workspace set is fixed at creation: use a "
                "new sandbox name rather than expecting this one to change."
            )
        # §8.7: no capability-granting credential inside a factory sandbox. Read from
        # the sandbox rather than assumed from how it was made — a sandbox created
        # while a secret was still global keeps it, because `sbx` fixes the secret set
        # at creation. Recreating is the only fix, which is what the message says.
        offending = capability_secrets(info.get("secrets") or [])
        if offending:
            raise SbxError(
                f"sandbox {spec.name} has injected secrets {offending}; factory "
                "sandboxes carry none (§8.7). Remove it and let the factory recreate "
                "it: the secret set is fixed at creation and cannot be narrowed later."
            )

    def stop(self, name: str) -> None:
        assert_factory_sandbox(name)
        self._run(["sbx", "stop", name], timeout=120)

    def remove(self, name: str) -> None:
        assert_factory_sandbox(name)
        self._run(["sbx", "rm", name], timeout=120)

    # -- execution ----------------------------------------------------------------

    def exec_sync(
        self,
        name: str,
        argv: Sequence[str],
        *,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int | None = None,
        stdin: str | None = None,
    ) -> Completed:
        return self._run(
            exec_argv(name, argv, workdir=workdir, env=env), timeout=timeout, stdin=stdin
        )

    def exec_detached(self, handle: RunHandle, script: str, env: Mapping[str, str]) -> None:
        """The single most important command in the factory (§4.2).

        Long work runs detached **inside** the sandbox and reports through the
        filesystem, so the host process can die at any moment and the next tick learns
        what happened by looking at three files. The script writes its exit code to a
        temp file and `mv`s it, so `exit` appearing is atomic.
        """
        assert_factory_sandbox(handle.sandbox)
        assert_no_skip_verify(env)
        argv = exec_argv(
            handle.sandbox,
            ["/bin/sh", "-lc", script],
            workdir=handle.workdir,
            env=env,
            detach=True,
        )
        result = self._run(argv, timeout=120)
        if not result.ok:
            raise SbxError(
                f"detached exec in {handle.sandbox} failed to start:\n"
                f"{result.stdout}\n{result.stderr}"
            )

    def kill_agent(self, name: str) -> None:
        """Stop a hung run so the attempt gets a real terminal record.

        A timeout is never silently a success: the wrapper's `exit` file still lands
        after the kill, which is what turns "we gave up" into a recorded exit code.
        """
        assert_factory_sandbox(name)
        self._run(["sbx", "exec", name, "pkill", "-f", "codex exec"], timeout=60)

    # -- observation --------------------------------------------------------------

    def poll(self, handle: RunHandle) -> RunStatus:
        """Filesystem first, sandbox state second.

        `exit` present is terminal regardless of what the VM is doing; a stale
        heartbeat with no `exit` is an orphan whether or not the sandbox is up.
        """
        attempt_dir = handle.attempt_dir
        if (attempt_dir / "exit").exists():
            return RunStatus.EXITED
        beat = attempt_dir / "heartbeat"
        try:
            age = time.time() - int(beat.read_text().strip())
        except (OSError, ValueError):
            age = None
        if age is not None and age < ORPHAN_AFTER_SECONDS:
            return RunStatus.RUNNING
        if not self.exists(handle.sandbox):
            return RunStatus.ORPHANED
        state = str(self.inspect(handle.sandbox).get("state", ""))
        if state != "running":
            return RunStatus.ORPHANED
        # The sandbox is up and the file is young enough to still be the first beat.
        return RunStatus.RUNNING if age is None else RunStatus.ORPHANED

    def collect(self, handle: RunHandle) -> RunResult:
        attempt_dir = handle.attempt_dir
        raw = (attempt_dir / "exit").read_text().strip()
        return RunResult(
            exit_code=int(raw),
            events_path=attempt_dir / "events.jsonl",
            stderr_path=attempt_dir / "stderr.log",
            last_message_path=attempt_dir / "last-message.json",
        )


def sbx_available() -> tuple[bool, str]:
    """For `factory doctor`. Reports the daemon version rather than just presence."""
    try:
        proc = subprocess.run(
            ["sbx", "ls"], capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"sbx unusable: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()
    return True, proc.stdout.strip()


def list_sandboxes() -> list[str]:
    proc = subprocess.run(["sbx", "ls"], capture_output=True, text=True, check=False, timeout=60)
    names: list[str] = []
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split()
        if parts:
            names.append(parts[0])
    return names


def worktree_inside(workspace: Path, worktree: Path) -> bool:
    """A worktree must sit inside a mounted workspace, or the VM cannot see it."""
    try:
        worktree.relative_to(workspace)
    except ValueError:
        return False
    return True
