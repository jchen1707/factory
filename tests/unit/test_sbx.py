"""§21.1 — golden argv, and the namespace assertion."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from factory.sandbox import sbx as sbx_module
from factory.sandbox.base import RunHandle, SandboxSpec, Workspace
from factory.sandbox.sbx import SbxAdapter, SbxError, create_argv, exec_argv


def _spec(**overrides: object) -> SandboxSpec:
    defaults: dict[str, object] = {
        "project": "python-harness",
        "role": "build",
        "name": "factory-build-python-harness",
        "workspaces": (
            Workspace(Path("/Users/james/python-harness")),
            Workspace(Path("/Users/james/Documents/Obsidian Vault")),
        ),
        "template": None,
    }
    defaults.update(overrides)
    return SandboxSpec(**defaults)  # type: ignore[arg-type]


def test_create_argv_for_the_python_build_sandbox() -> None:
    assert create_argv(_spec()) == [
        "sbx",
        "create",
        "codex",
        "--name",
        "factory-build-python-harness",
        "/Users/james/python-harness",
        "/Users/james/Documents/Obsidian Vault",
    ]


def test_create_argv_carries_the_template_only_when_there_is_one() -> None:
    argv = create_argv(_spec(template="codex-pnpm:v1"))
    assert argv[argv.index("-t") + 1] == "codex-pnpm:v1"
    assert "-t" not in create_argv(_spec(template=None))


def test_a_read_only_workspace_gets_the_ro_suffix() -> None:
    # The read-only mount is an *additional* workspace: `sbx create` refuses a `:ro`
    # primary, so the reviewer's writable scratch leads and the code follows.
    spec = _spec(
        name="factory-review-python-harness",
        role="review",
        workspaces=(
            Workspace(Path("/Users/james/factory/state/runs/abc/review")),
            Workspace(Path("/Users/james/python-harness"), readonly=True),
        ),
        share_skills=False,
    )
    argv = create_argv(spec)
    assert "/Users/james/python-harness:ro" in argv
    assert "--no-share-skills" in argv


def test_create_refuses_a_name_outside_the_factory_namespace() -> None:
    with pytest.raises(PermissionError, match="codex-"):
        create_argv(_spec(name="codex-python-harness"))


def test_exec_argv_is_detached_with_a_workdir_and_sorted_env() -> None:
    argv = exec_argv(
        "factory-build-python-harness",
        ["/bin/sh", "-lc", "true"],
        workdir="/Users/james/python-harness/.factory/worktrees/BAC-4",
        env={"UV_PROJECT_ENVIRONMENT": "/home/agent/venvs/python-harness", "A": "b"},
        detach=True,
    )
    assert argv == [
        "sbx",
        "exec",
        "-d",
        "-w",
        "/Users/james/python-harness/.factory/worktrees/BAC-4",
        "-e",
        "A=b",
        "-e",
        "UV_PROJECT_ENVIRONMENT=/home/agent/venvs/python-harness",
        "factory-build-python-harness",
        "/bin/sh",
        "-lc",
        "true",
    ]


def test_exec_refuses_to_smuggle_harness_skip_verify_into_the_run() -> None:
    with pytest.raises(PermissionError, match="HARNESS_SKIP_VERIFY"):
        exec_argv("factory-build-python-harness", ["true"], env={"HARNESS_SKIP_VERIFY": "1"})


@pytest.mark.parametrize("operation", ["stop", "remove", "kill_agent"])
def test_lifecycle_operations_refuse_an_operator_owned_sandbox(operation: str) -> None:
    adapter = SbxAdapter()
    with pytest.raises(PermissionError):
        getattr(adapter, operation)("codex-python-harness")


# --------------------------------------------------------------------------------
# §8.7 — the secret set, and the deny rule that compensates for the one that stays
# --------------------------------------------------------------------------------

#: Verbatim from `sbx inspect factory-build-python-harness --json`, 2026-08-21, taken
#: seconds after the factory created that sandbox itself.
MEASURED_SECRETS = [
    {"name": "github", "source": "uploaded"},
    {"name": "mcpgateway", "source": "uploaded"},
]


def test_create_argv_carries_a_deny_rule_per_host() -> None:
    # §22 F19 — the denied hosts reach the sandbox spec, so egress to one is blocked
    # by the proxy rather than by anything the agent could talk its way past.
    argv = create_argv(_spec(deny_network=("mcp.linear.app", "example.invalid")))
    assert argv.count("--deny-network") == 2
    assert argv[argv.index("--deny-network") + 1] == "mcp.linear.app"
    # Creation-time decisions must precede the workspaces, which `sbx` reads positionally.
    assert argv.index("--deny-network") < argv.index("/Users/james/python-harness")


def _adapter_seeing(secrets: list[dict[str, str]]) -> SbxAdapter:
    adapter = SbxAdapter()
    adapter.exists = lambda name: True  # type: ignore[method-assign]
    adapter.inspect = lambda name: {  # type: ignore[method-assign]
        "name": name,
        "workspace": "/Users/james/python-harness",
        "secrets": secrets,
    }
    return adapter


def test_ensure_refuses_a_sandbox_still_carrying_a_service_secret() -> None:
    # The real case: the sandbox was created while `github` was still global, and `sbx`
    # fixes the secret set at creation, so re-scoping the secret does not retire it.
    with pytest.raises(SbxError) as caught:
        _adapter_seeing(MEASURED_SECRETS).ensure(_spec())
    assert "github" in str(caught.value)
    assert "mcpgateway" not in str(caught.value)


def test_ensure_accepts_a_sandbox_carrying_only_the_gateway_credential() -> None:
    # Unsatisfiable otherwise: `sbx` uploads it into every sandbox whenever any MCP
    # server is registered, and offers no per-sandbox opt-out.
    _adapter_seeing([{"name": "mcpgateway", "source": "uploaded"}]).ensure(_spec())


# --------------------------------------------------------------------------------
# `exec_detached` must not wait for the command it starts
# --------------------------------------------------------------------------------


class _FakePopen:
    """Stands in for `sbx exec -d`, which keeps running long after it is started."""

    def __init__(self, argv: list[str], *, heartbeat: Path | None, rc: int | None, err: str = ""):
        self.argv = argv
        self._rc = rc
        self.pid = 4242
        if heartbeat is not None:
            heartbeat.parent.mkdir(parents=True, exist_ok=True)
            heartbeat.write_text("1787285036")
        if err:
            # What the real `sbx exec` would have written to the handle it was given.
            (_STDERR_TARGET[0]).write_text(err)
        self.returncode = rc

    def poll(self) -> int | None:
        return self._rc


#: Set by each test to the file the adapter opens for `sbx exec`'s stderr.
_STDERR_TARGET: list[Path] = []


def _handle(tmp_path: Path) -> RunHandle:
    return RunHandle(
        run_id="03cda9bfebe644d7",
        attempt=1,
        sandbox="factory-build-python-harness",
        workdir="/Users/james/python-harness/.factory/worktrees/BAC-4",
        attempt_dir=tmp_path,
    )


def test_exec_detached_returns_without_waiting_for_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§4.2's whole design: long work runs in the VM and reports through files, so the
    host must be free to die at any moment.

    `sbx exec -d` does not cooperate — it blocks for as long as the command runs.
    Measured 2026-08-21: `sbx exec -d <sandbox> /bin/sh -lc 'sleep 240'` returned after
    4 m 01 s, and the first implement attempt to clear the preflight died on
    `TimeoutExpired` at 120 s while the agent kept working inside the VM. So this must
    never wait on the process — only on the wrapper's first heartbeat.
    """
    started: list[list[str]] = []

    def fake_popen(argv, **kwargs):  # type: ignore[no-untyped-def]
        started.append(argv)
        # rc None == still running, exactly like the real one, forever.
        return _FakePopen(argv, heartbeat=tmp_path / "heartbeat", rc=None)

    monkeypatch.setattr(sbx_module.subprocess, "Popen", fake_popen)
    SbxAdapter().exec_detached(_handle(tmp_path), "codex exec ...", {})

    assert started
    assert started[0][:3] == ["sbx", "exec", "-d"]


def test_exec_detached_holds_the_process_handle_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The exec dies with its host process, so dropping the handle would kill the run.
    monkeypatch.setattr(
        sbx_module.subprocess,
        "Popen",
        lambda argv, **kw: _FakePopen(argv, heartbeat=tmp_path / "heartbeat", rc=None),
    )
    adapter = SbxAdapter()
    adapter.exec_detached(_handle(tmp_path), "codex exec ...", {})
    assert "factory-build-python-harness" in adapter._detached


def test_exec_detached_reports_a_start_that_died_before_its_first_beat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No heartbeat and a dead process is the one shape that is genuinely a failure to
    start, and the sandbox's stderr is the only thing that says why."""
    _STDERR_TARGET[:] = [tmp_path / sbx_module.SBX_EXEC_STDERR]
    monkeypatch.setattr(
        sbx_module.subprocess,
        "Popen",
        lambda argv, **kw: _FakePopen(argv, heartbeat=None, rc=127, err="sbx: no such sandbox"),
    )
    with pytest.raises(SbxError) as caught:
        SbxAdapter().exec_detached(_handle(tmp_path), "codex exec ...", {})
    assert "no such sandbox" in str(caught.value)
    assert "127" in str(caught.value)


def test_sbx_exec_stderr_is_kept_apart_from_the_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transport's complaints and the agent's output are different evidence.

    It is also why this is a file and not a pipe: the process outlives the call, so a
    pipe nobody drains would block at 64 KB — on the very exec it exists to diagnose.
    """
    monkeypatch.setattr(
        sbx_module.subprocess,
        "Popen",
        lambda argv, **kw: _FakePopen(argv, heartbeat=tmp_path / "heartbeat", rc=None),
    )
    SbxAdapter().exec_detached(_handle(tmp_path), "codex exec ...", {})
    assert (tmp_path / sbx_module.SBX_EXEC_STDERR).exists()
    assert sbx_module.SBX_EXEC_STDERR != "stderr.log"


def test_the_session_holder_outlives_the_process_that_started_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one keyword argument that makes §4.2's guarantee true on `sbx` v0.38.0.

    sandboxd stops a sandbox 30 s after its **last session** disconnects — measured
    2026-08-21 from its own log: `session disconnected, deferring auto-stop … delay:
    30000000000`, then `auto-stop grace period expired`, then `auto-stopped runtime
    after last session disconnected`, 30.0 s apart. Nothing else keeps a sandbox up:
    no flag, no in-VM process, no traffic. So the run lives exactly as long as the
    `sbx exec` process holding that session, and §4.2 promises the factory may die at
    any moment — which is only true if that process is not tied to this one.

    `start_new_session=True` makes it a session leader, reparented to pid 1 and out of
    this process's group, so neither this process exiting nor a Ctrl-C at the terminal
    that started the factory takes it down. Measured the same day: parent `os._exit(0)`
    immediately after spawn, and 115 s later — long past the grace period — the sandbox
    was still `running` with its in-VM output file one second old.
    """
    captured: dict[str, object] = {}

    def fake_popen(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return _FakePopen(argv, heartbeat=tmp_path / "heartbeat", rc=None)

    monkeypatch.setattr(sbx_module.subprocess, "Popen", fake_popen)
    SbxAdapter().exec_detached(_handle(tmp_path), "codex exec ...", {})

    assert captured.get("start_new_session") is True
    # And it has to be findable afterwards, by a process that did not start it.
    assert sbx_module.holder_pid(tmp_path) == 4242


def test_poll_calls_a_run_orphaned_the_moment_its_holder_dies(tmp_path: Path) -> None:
    """A dead holder is terminal even with a heartbeat seconds old.

    The sandbox has either stopped already or will within 30 s, and a stopping microVM
    takes every process inside it. Waiting out `ORPHAN_AFTER_SECONDS` of stale
    heartbeat first would spend 90 s reaching a verdict that is already decided.
    """
    dead = subprocess.Popen(["/usr/bin/true"])
    dead.wait()
    (tmp_path / sbx_module.SBX_EXEC_PID).write_text(f"{dead.pid}\n")
    (tmp_path / "heartbeat").write_text(str(int(time.time())))

    assert SbxAdapter().poll(_handle(tmp_path)) is sbx_module.RunStatus.ORPHANED


def test_poll_still_reports_a_live_holder_as_running(tmp_path: Path) -> None:
    (tmp_path / sbx_module.SBX_EXEC_PID).write_text(f"{os.getpid()}\n")
    (tmp_path / "heartbeat").write_text(str(int(time.time())))

    assert SbxAdapter().poll(_handle(tmp_path)) is sbx_module.RunStatus.RUNNING


def test_poll_without_a_pid_file_falls_back_to_the_heartbeat(tmp_path: Path) -> None:
    """An attempt written before the pid file existed still polls correctly."""
    (tmp_path / "heartbeat").write_text(str(int(time.time())))

    assert SbxAdapter().poll(_handle(tmp_path)) is sbx_module.RunStatus.RUNNING


def test_a_read_only_primary_workspace_is_refused_before_sbx_sees_it() -> None:
    """`sbx create` rejects it — `ERROR: primary workspace must be read/write (remove
    ':ro' or ':readonly')` — and its own help scopes `:ro` to the *additional*
    workspaces. The reviewer sandbox was specced the other way round and died on this
    40 seconds into a real run (BAC-4, `1effc543d83a459a`)."""
    spec = _spec(
        role="review",
        name="factory-review-python-harness",
        workspaces=(
            Workspace(Path("/Users/james/python-harness"), readonly=True),
            Workspace(Path("/Users/james/factory/state/runs/abc/review")),
        ),
    )
    with pytest.raises(SbxError) as caught:
        create_argv(spec)
    assert "read/write" in str(caught.value)


def test_the_writable_scratch_leads_and_the_code_under_review_follows_read_only() -> None:
    spec = _spec(
        role="review",
        name="factory-review-python-harness",
        workspaces=(
            Workspace(Path("/Users/james/factory/state/runs/abc/review")),
            Workspace(Path("/Users/james/python-harness"), readonly=True),
        ),
    )
    assert create_argv(spec)[-2:] == [
        "/Users/james/factory/state/runs/abc/review",
        "/Users/james/python-harness:ro",
    ]


# --------------------------------------------------------------------------------
# The kill must not select the process that writes the terminal record
# --------------------------------------------------------------------------------


#: `pkill` semantics, measured inside `factory-build-python-harness-2` on 2026-08-22
#: rather than read off a man page: `-f` matches the **whole command line**, `-x`
#: matches the **process name** exactly. The measurement that matters is that a
#: `-f "codex exec"` pattern selected three processes — the agent, the wrapper
#: `/bin/sh -lc` whose command line embeds the agent's argv, and the wrapper's
#: heartbeat subshell — and killing the wrapper is what leaves an attempt with no
#: `exit` file at all.
def _pkill_selects(argv: list[str], *, process_name: str, cmdline: str) -> bool:
    flags = {part for part in argv if part.startswith("-")}
    pattern = argv[-1]
    if "-f" in flags:
        return pattern in cmdline
    if "-x" in flags:
        return process_name == pattern
    return pattern in process_name


def test_kill_agent_does_not_select_the_wrapper_that_writes_the_exit_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§16.3b's suspend and §16.1's timeout both promise a real terminal record after
    the kill. They cannot have one if the kill selects the wrapper: the `exit` file is
    written by the wrapper, *after* the body returns.

    Measured on BAC-6 run `3f03240cd3bc4bd0`: suspend killed the agent and waited out
    `KILL_GRACE_SECONDS`, and no `exit` file ever appeared — because
    `pkill -f "codex exec"` had killed the shell that would have written it.
    """
    from factory.agent.base import AgentInvocation
    from factory.agent.codex import CodexAdapter

    invocation = AgentInvocation(
        model="gpt-5.6-sol",
        effort="xhigh",
        workdir="/repo/wt",
        prompt_path=tmp_path / "prompt.md",
        schema_path=tmp_path / "schema.json",
        output_path=tmp_path / "last-message.json",
        events_path=tmp_path / "events.jsonl",
        stderr_path=tmp_path / "stderr.log",
        exit_path=tmp_path / "exit",
        heartbeat_path=tmp_path / "heartbeat",
        vault_directory="/Users/james/Documents/Obsidian Vault",
    )
    script = CodexAdapter().wrapper_script(invocation)
    wrapper_cmdline = f"/bin/sh -lc {script}"

    captured: list[list[str]] = []
    monkeypatch.setattr(
        SbxAdapter,
        "_run",
        lambda self, argv, **kw: captured.append(list(argv)),
    )
    SbxAdapter().kill_agent("factory-build-python-harness")

    argv = captured[0]
    assert argv[:3] == ["sbx", "exec", "factory-build-python-harness"]
    pkill = argv[3:]
    assert pkill[0] == "pkill"

    # The agent itself must die: `codex exec ...` runs as a process named `codex`.
    assert _pkill_selects(
        pkill, process_name="codex", cmdline=" ".join(CodexAdapter().command(invocation))
    )
    # The wrapper must not, and neither must its heartbeat subshell. Both are `/bin/sh`
    # processes whose command line contains the agent's argv verbatim.
    assert not _pkill_selects(pkill, process_name="sh", cmdline=wrapper_cmdline)
