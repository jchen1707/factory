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


def detached_shell_script(
    *, heartbeat_path: Path, exit_path: Path, body: str, pgid_path: Path | None = None
) -> str:
    """The `/bin/sh -lc` envelope for a detached run inside a sandbox.

    A heartbeat loop, the `body` (the command(s) that do the work), and the body's
    exit code written to `exit_path` last and atomically (`mv` from a temp file),
    so `exit` appearing is the terminal record `poll` and `reap` rely on (§4.2).
    `body` is interpolated raw — the caller `shlex.quote`s every path and argv it
    names — so this function never has to guess what needs quoting.

    Every detached step runs through here — the codex wrapper
    (`agent/codex.py:wrapper_script`) for plan/implement/review, and the verify gate
    report — so the heartbeat/exit protocol has one copy.

    `pgid_path` extends that protocol with the body's **in-VM process group id**, and it
    is what makes two concurrent runs in one sandbox safe to stop independently. Without
    it the only way to signal a run is `pkill -x <name>` (`SbxAdapter.kill_agent`), which
    matches every process of that name in the VM — and both the build and the review
    sandbox are named once per *project* (`steps/sandbox.py`). In a project running two
    tickets at once, a timeout on one run signalled the other run's agent as well; the
    victim's wrapper then wrote `exit 143` and the factory read it as that run's own
    timeout. Wrong terminal record, wrong ladder rung, and nothing in the artifacts to
    tell it from a real one.

    A process group is exact where a process name is not, and it is a *group* rather than
    the single pid `$!` would give because a body may be compound: `steps/review.py`
    passes one codex block per axis, so there is no single pid to name. `setsid` puts the
    whole body in a fresh session — measured present at `/usr/bin/setsid`, with `--wait`,
    in the stock image on 2026-09-02 — and the group leader publishes its own `$$`, which
    is the pgid however `setsid` chose to fork. `kill -TERM -<pgid>` then reaches the body
    and everything it spawned, and nothing else.

    Two details are load-bearing:

    - **`setsid --wait`**, so the envelope's `wait` returns the *body's* status. Plain
      `setsid` may fork and exit 0, which would report every killed run as a success.
    - **The body goes to a file, not to `sh -s`.** The inner shell must not read its
      script from stdin, because a body command that reads stdin without a redirect
      would eat the rest of it. `verify`'s gate report redirects only stdout and stderr,
      so this is a real case and not a hypothetical one.

    The wrapper still owns `exit`: signalling the group stops the body, the wrapper
    survives, `wait` returns 143 and the terminal record lands — the same property
    `sbx.kill_agent`'s own docstring was measured into place to protect, kept rather than
    reinvented. Verified end to end in `factory-build-nemoclaw-dev` on 2026-09-02: the
    body died, `exit` read `143`, and no process outside the group was touched.

    `pgid_path` absent keeps the original foreground envelope, so a caller that has not
    opted in behaves exactly as before.
    """
    import shlex

    hb = shlex.quote(str(heartbeat_path))
    ex = shlex.quote(str(exit_path))
    if pgid_path is None:
        return (
            "set -u\n"
            f"( while :; do date -u +%s > {hb}; sleep 20; done ) &\n"
            "HB=$!\n"
            f"{body}\n"
            "code=$?\n"
            "kill $HB 2>/dev/null\n"
            f'printf %s "$code" > {ex}.tmp && mv {ex}.tmp {ex}\n'
        )
    pg = shlex.quote(str(pgid_path))
    script = shlex.quote(f"{pgid_path}-body.sh")
    return (
        "set -u\n"
        f"( while :; do date -u +%s > {hb}; sleep 20; done ) &\n"
        "HB=$!\n"
        # A quoted heredoc delimiter: the body is written through literally, and `$$`
        # below is expanded by the *inner* shell, where it is the new group leader.
        f"cat > {script} <<'__FACTORY_BODY__'\n"
        f'printf %s "$$" > {pg}.tmp && mv {pg}.tmp {pg}\n'
        f"{body}\n"
        "__FACTORY_BODY__\n"
        f"setsid --wait /bin/sh {script} &\n"
        "BODY=$!\n"
        "wait $BODY\n"
        "code=$?\n"
        "kill $HB 2>/dev/null\n"
        f"rm -f {pg}\n"
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
    #: Custom-secret names this project's registry entry declares (`[sandbox_delivery]`),
    #: which `policy.capability_secrets` therefore admits inside the VM. Empty for every
    #: project that has not opted in, which is the strong default.
    #:
    #: Carried on the spec rather than looked up where it is checked, because the check
    #: lives in `SbxAdapter._assert_spec_matches`, which has a sandbox name and no
    #: registry. Not a creation-time field — it appears in no `create_argv` — so changing
    #: it does not force a new sandbox name.
    allowed_custom_secrets: tuple[str, ...] = ()


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
    #: The wrapper's process-group file, relative to `attempt_dir`. Parallel to
    #: `exit_name` and for the same reason: the plan phase writes its own so a rewind's
    #: two phases can share one attempt directory without one half signalling the other's
    #: process group.
    pgid_name: str = "pgid"
    #: The terminal file this phase's wrapper writes, relative to `attempt_dir`. The
    #: plan phase writes `plan-exit` so that a rewind's two phases can share one attempt
    #: directory (`steps/plan.py`); every other phase writes `exit`. A poller that reads
    #: only `exit` calls a finished plan an orphan -- measured on FRO-11 attempt 3.
    exit_name: str = "exit"


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

    def kill_agent(self, name: str, proc: str = "codex") -> None: ...

    def kill_group(self, name: str, pgid: int) -> None: ...

    def poll(self, handle: RunHandle) -> RunStatus: ...

    def collect(self, handle: RunHandle) -> RunResult: ...

    def git_daemon_url(self, name: str) -> str | None: ...

    def custom_secret_placeholder(self, name: str, env: str) -> str | None: ...

    def stop(self, name: str) -> None: ...

    def remove(self, name: str) -> None: ...
