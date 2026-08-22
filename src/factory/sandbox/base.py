"""The sandbox contract — §8.4.

Everything the control plane needs from an execution environment, and nothing about
how `sbx` provides it. The whole state machine runs in tests against a fake that
satisfies this protocol with no VM, no Docker and no network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

__all__ = [
    "Completed",
    "RunHandle",
    "RunResult",
    "RunStatus",
    "SandboxAdapter",
    "SandboxSpec",
    "Workspace",
]


@dataclass(frozen=True)
class Workspace:
    path: Path
    readonly: bool = False

    def as_argument(self) -> str:
        """`sbx`'s own spelling: a `:ro` suffix mounts read-only."""
        return f"{self.path}:ro" if self.readonly else str(self.path)


def detached_shell_script(*, heartbeat_path: Path, exit_path: Path, body: str) -> str:
    """The `/bin/sh -lc` envelope for a detached run inside a sandbox.

    A heartbeat loop, the `body` (the command(s) that do the work), and the body's
    exit code written to `exit_path` last and atomically (`mv` from a temp file),
    so `exit` appearing is the terminal record `poll` and `reap` rely on (§4.2).
    `body` is interpolated raw — the caller `shlex.quote`s every path and argv it
    names — so this function never has to guess what needs quoting.

    The codex agent wrapper (`agent/codex.py:wrapper_script`) and the verify gate
    report both run through here, so the heartbeat/exit protocol has one copy.
    """
    import shlex

    hb = shlex.quote(str(heartbeat_path))
    ex = shlex.quote(str(exit_path))
    return (
        "set -u\n"
        f"( while :; do date -u +%s > {hb}; sleep 20; done ) &\n"
        "HB=$!\n"
        f"{body}\n"
        "code=$?\n"
        "kill $HB 2>/dev/null\n"
        f'printf %s "$code" > {ex}.tmp && mv {ex}.tmp {ex}\n'
    )


@dataclass(frozen=True)
class SandboxSpec:
    """What a sandbox is. Three of these fields are fixed at creation and cannot be
    changed afterwards — the workspace set, the template and the static MCP set — so a
    spec change forces a **new sandbox name** rather than a silent mismatch (§9.1)."""

    project: str
    role: Literal["build", "review"]
    name: str
    workspaces: tuple[Workspace, ...]
    template: str | None = None
    kits: tuple[str, ...] = ()
    static_mcp: tuple[str, ...] = ()
    deny_network: tuple[str, ...] = ()
    memory: str | None = None
    cpus: int | None = None
    #: Never a secret value. §8.7's rule is that no credential of any kind enters the
    #: VM, and the preflight asserts `sbx inspect --json` reports no injected secret.
    env: Mapping[str, str] = field(default_factory=dict)
    #: Phase 2. The frontend path needs it because `node_modules` cannot be shared with
    #: a macOS host (p0-10); the python path uses `UV_PROJECT_ENVIRONMENT` instead.
    clone: bool = False
    share_skills: bool = True


@dataclass(frozen=True)
class RunHandle:
    run_id: str
    attempt: int
    sandbox: str
    #: Absolute, and identical on the host and in the VM. That identity is what makes
    #: the filesystem a usable protocol between the two.
    workdir: str
    attempt_dir: Path
    session_id: str | None = None


class RunStatus(StrEnum):
    RUNNING = "running"
    EXITED = "exited"
    ORPHANED = "orphaned"


@dataclass(frozen=True)
class Completed:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    events_path: Path
    stderr_path: Path
    last_message_path: Path


class SandboxAdapter(Protocol):
    def exists(self, name: str) -> bool: ...

    def inspect(self, name: str) -> dict[str, Any]: ...

    def ensure(self, spec: SandboxSpec) -> None: ...

    def exec_detached(self, handle: RunHandle, script: str, env: Mapping[str, str]) -> None: ...

    def exec_sync(
        self,
        name: str,
        argv: Sequence[str],
        *,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int | None = None,
        stdin: str | None = None,
    ) -> Completed: ...

    def poll(self, handle: RunHandle) -> RunStatus: ...

    def collect(self, handle: RunHandle) -> RunResult: ...

    def git_daemon_url(self, name: str) -> str | None: ...

    def stop(self, name: str) -> None: ...

    def remove(self, name: str) -> None: ...
