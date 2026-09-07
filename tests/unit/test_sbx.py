"""§21.1 — golden argv, and the namespace assertion."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from factory.sandbox import sbx as sbx_module
from factory.sandbox.base import Completed, RunHandle, SandboxSpec, Workspace
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


def test_ensure_accepts_the_custom_secret_the_project_declared() -> None:
    # In-VM delivery: the sandbox holds a `sbx-cs-…` placeholder under this name, and the
    # spec carries the declaration that admits it. Without this, `ensure` refuses the
    # sandbox on every tick after the secret is provisioned, and the project cannot run.
    _adapter_seeing([{"name": "FACTORY_GITLAB_TOKEN", "source": "custom"}]).ensure(
        _spec(allowed_custom_secrets=("FACTORY_GITLAB_TOKEN",))
    )


def test_ensure_still_refuses_that_secret_for_a_project_that_did_not_declare_it() -> None:
    # The reversibility property, at the adapter: the admission travels on the spec, so a
    # project whose `[sandbox_delivery]` table is deleted goes back to refusing the same
    # sandbox with no code change anywhere.
    with pytest.raises(SbxError, match="FACTORY_GITLAB_TOKEN"):
        _adapter_seeing([{"name": "FACTORY_GITLAB_TOKEN", "source": "custom"}]).ensure(_spec())


#: What `sbx secret ls --sandbox <name>` prints on v0.38.0, copied from the live host on
#: 2026-09-01. Whitespace-aligned, no `--json` flag (`unknown flag: --json`), and the
#: header row contains the word PLACEHOLDER — which is why the reader matches on the
#: `sbx-cs-` prefix rather than on a column index or on the header's position.
MEASURED_LISTING = """CUSTOM SECRETS
SCOPE                            TARGETS          ENV                    PLACEHOLDER               SECRET
factory-build-nemoclaw-dev       172.18.194.183   FACTORY_GITLAB_TOKEN   sbx-cs-AFlXreOjVPUyNl4k   glpat-******...******9VtS
"""


def _adapter_listing(stdout: str, *, ok: bool = True) -> SbxAdapter:
    adapter = SbxAdapter()

    def run(argv: Sequence[str], *, timeout: int | None = None, stdin: str | None = None):  # type: ignore[no-untyped-def]
        return Completed(tuple(argv), 0 if ok else 1, stdout, "")

    adapter._run = run  # type: ignore[method-assign]
    return adapter


def test_the_placeholder_is_read_out_of_the_secret_listing() -> None:
    adapter = _adapter_listing(MEASURED_LISTING)
    assert (
        adapter.custom_secret_placeholder("factory-build-nemoclaw-dev", "FACTORY_GITLAB_TOKEN")
        == "sbx-cs-AFlXreOjVPUyNl4k"
    )


def test_a_secret_in_another_sandboxs_scope_is_not_this_sandboxs_placeholder() -> None:
    # `sbx secret ls` with no `--sandbox` prints global entries and every other scope's.
    # Reading a row that names a different sandbox would hand the delivery path a
    # placeholder the proxy will not substitute for it — a 401 at the end of a paid run.
    listing = MEASURED_LISTING.replace("factory-build-nemoclaw-dev", "codex-python-harness")
    assert (
        _adapter_listing(listing).custom_secret_placeholder(
            "factory-build-nemoclaw-dev", "FACTORY_GITLAB_TOKEN"
        )
        is None
    )


def test_no_placeholder_is_none_rather_than_an_empty_string() -> None:
    # None is what `steps/deliver.py` turns into a named `Blocked`; "" would be installed
    # as a credential file and fail at the push with a bare 401.
    assert _adapter_listing("", ok=False).custom_secret_placeholder("factory-build-x", "E") is None
    assert (
        _adapter_listing("CUSTOM SECRETS\n").custom_secret_placeholder("factory-build-x", "E")
        is None
    )


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


def test_poll_does_not_reap_a_paused_run_on_a_stale_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 5 defect 1: a sleeping laptop reaped a healthy run as `attempt-orphaned`.

    While the host is suspended the heartbeat subshell stops advancing — the sandbox
    suspends with the host — so on every wake the heartbeat file is minutes stale.
    `poll` read that stale beat as an orphan verdict even though the holder pid was
    live and the sandbox was still running: nothing had died, only paused. The orphan
    signature §16.1 actually wants — the holder gone — is checked first and is the
    only death signal when a holder is recorded. A stale heartbeat with a live holder
    and a running sandbox is a paused run, left for the §5.1 state timeout, not `poll`.
    """
    (tmp_path / sbx_module.SBX_EXEC_PID).write_text(f"{os.getpid()}\n")
    # Minutes stale — well past ORPHAN_AFTER_SECONDS, as it is after every wake.
    (tmp_path / "heartbeat").write_text(str(int(time.time()) - 600))
    adapter = SbxAdapter()
    monkeypatch.setattr(adapter, "exists", lambda _name: True)
    monkeypatch.setattr(adapter, "inspect", lambda _name: {"state": "running"})

    assert adapter.poll(_handle(tmp_path)) is sbx_module.RunStatus.RUNNING


def test_poll_reads_the_exit_file_the_phase_actually_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan attempt's wrapper writes `plan-exit`, not `exit`.

    Measured on FRO-11 attempt 3, 2026-08-23: the rung-3 rewind's `codex exec` failed in
    under a second and wrote `plan-exit` with `1`. `poll` looked only for `exit`, found
    none, saw the holder gone and called the attempt **orphaned** -- so the run spent its
    last ladder rung on what was really a collectable failure with a one-line cause in
    `plan-stderr.log`. A phase names its own exit file, and `poll` reads that one.
    """
    (tmp_path / "plan-exit").write_text("1\n")
    handle = replace(_handle(tmp_path), exit_name="plan-exit")

    monkeypatch.setattr(SbxAdapter, "exists", lambda self, name: False)
    assert SbxAdapter().poll(handle) is sbx_module.RunStatus.EXITED
    # The default is unchanged for every phase that writes `exit`.
    assert SbxAdapter().poll(_handle(tmp_path)) is not sbx_module.RunStatus.EXITED


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
        pgid_path=tmp_path / "pgid",
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


def test_kill_agent_targets_node_for_the_verifying_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 5 defect 3: the `verifying` step runs `node gate_report.mjs`, not
    `codex exec`, so the default `pkill -x codex` matched nothing and a hung gate
    report was never signalled — no `exit` file, no terminal record, the run went
    `resumable` on a timeout it could not actually stop. `kill_agent` takes the
    in-VM process name; the verifying step passes `node`."""
    captured: list[list[str]] = []
    monkeypatch.setattr(
        SbxAdapter,
        "_run",
        lambda self, argv, **kw: captured.append(list(argv)),
    )
    SbxAdapter().kill_agent("factory-build-python-harness", "node")

    argv = captured[0]
    assert argv[:3] == ["sbx", "exec", "factory-build-python-harness"]
    assert argv[3:] == ["pkill", "-x", "node"]


def test_kill_agent_defaults_to_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The implement/review/reap/recovery callers pass no process name; the default
    stays `codex` so this change does not widen what those steps kill."""
    captured: list[list[str]] = []
    monkeypatch.setattr(
        SbxAdapter,
        "_run",
        lambda self, argv, **kw: captured.append(list(argv)),
    )
    SbxAdapter().kill_agent("factory-build-python-harness")

    assert captured[0][3:] == ["pkill", "-x", "codex"]


def test_generation_is_observed_again_when_a_sandbox_name_is_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    identifiers = iter(
        [
            "6aa2ecf7-415f-41f0-9ec1-b3c1c9e3e0c4",
            "9df815b9-b769-401e-96ef-018d9d32aa5d",
        ]
    )

    def run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(
                {
                    "sandboxes": [
                        {
                            "name": "factory-build-test",
                            "id": next(identifiers),
                        }
                    ]
                }
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", run)
    adapter = SbxAdapter()
    assert adapter.generation("factory-build-test") == "6aa2ecf7-415f-41f0-9ec1-b3c1c9e3e0c4"
    assert adapter.generation("factory-build-test") == "9df815b9-b769-401e-96ef-018d9d32aa5d"


@pytest.mark.parametrize(
    "listing",
    [
        {},
        {"sandboxes": []},
        {"sandboxes": None},
        {"sandboxes": [None]},
        {"sandboxes": [{"name": "factory-build-test"}]},
        {"sandboxes": [{"name": "factory-build-test", "id": "same-name"}]},
        {
            "sandboxes": [
                {"name": "factory-build-test", "id": "6aa2ecf7-415f-41f0-9ec1-b3c1c9e3e0c4"}
            ]
            * 2
        },
    ],
)
def test_generation_refuses_unknown_or_ambiguous_identity(
    listing: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(listing), ""),
    )
    with pytest.raises(SbxError, match="generation unavailable"):
        SbxAdapter().generation("factory-build-test")


def test_generation_refuses_human_sandbox() -> None:
    with pytest.raises(PermissionError):
        SbxAdapter().generation("codex-factory")


def test_runtime_observation_refuses_recreation_during_binary_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    generations = iter(
        [
            "6aa2ecf7-415f-41f0-9ec1-b3c1c9e3e0c4",
            "9df815b9-b769-401e-96ef-018d9d32aa5d",
        ]
    )

    def run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        data: dict[str, object]
        if argv[1] == "ls":
            data = {"sandboxes": [{"name": "factory-build-test", "id": next(generations)}]}
        elif argv[1] == "inspect":
            data = {
                "name": "factory-build-test",
                "image_digest": "sha256:" + "a" * 64,
                "secrets": [],
            }
        else:
            data = {
                "runtime_path": "/opt/codex",
                "runtime_version": "codex-cli 0.153.4",
                "runtime_sha256": "b" * 64,
            }
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SbxError, match="changed during runtime observation"):
        SbxAdapter().observe_runtime("factory-build-test", binary="/opt/codex")


def test_candidate_python_import_cannot_forge_runtime_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import sys

    marker = tmp_path / "candidate-executed"
    forged = {
        "runtime_path": "/opt/codex",
        "runtime_version": "codex-cli 0.153.4",
        "runtime_sha256": "b" * 64,
    }
    (tmp_path / "json.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        f"print({json.dumps(forged)!r})\nraise SystemExit(0)\n"
    )
    actual_run = subprocess.run

    def run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[1] == "ls":
            data = {
                "sandboxes": [
                    {"name": "factory-build-test", "id": "6aa2ecf7-415f-41f0-9ec1-b3c1c9e3e0c4"}
                ]
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")
        if argv[1] == "inspect":
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(
                    {
                        "image_digest": "sha256:" + "a" * 64,
                        "secrets": [],
                    }
                ),
                "",
            )
        command = [sys.executable, *argv[argv.index("/usr/bin/python3") + 1 :]]
        return actual_run(
            command,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(tmp_path)},
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(SbxError, match="native runtime identity unavailable"):
        SbxAdapter().observe_runtime("factory-build-test", binary="/missing/native")
    assert not marker.exists()


def test_full_observation_includes_environment_capability_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    observations = iter(range(100))

    def run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        data: dict = {}
        if argv[1] == "ls":
            data = {
                "sandboxes": [
                    {"name": "factory-build-test", "id": "6aa2ecf7-415f-41f0-9ec1-b3c1c9e3e0c4"}
                ]
            }
        elif argv[1] == "inspect":
            data = {
                "image_digest": "sha256:" + "a" * 64,
                "secrets": [],
                "kits": [],
                "uptime": str(next(observations)),
            }
        elif argv[-1] == "/opt/codex":
            data = {
                "runtime_path": "/opt/codex",
                "runtime_version": "codex-cli 0.153.4",
                "runtime_sha256": "b" * 64,
            }
        else:
            data = {
                "mounts": [],
                "environment_sha256": "c" * 64,
                "configurations": {},
                "hooks": {},
                "credential_names": ["FIXTURE_CAPABILITY"],
                "launcher_sha256": "d" * 64,
                "native_mount": {"path": "/mnt", "uid": 0, "mode": 16877},
            }
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

    monkeypatch.setattr(subprocess, "run", run)
    result = SbxAdapter().observe_certification(
        SandboxSpec("test", "build", "factory-build-test", ()),
        binary="/opt/codex",
        workdir="/workspace",
        env={},
        hook_files={},
    )
    assert result["actual"]["credential_names"] == ["FIXTURE_CAPABILITY"]
