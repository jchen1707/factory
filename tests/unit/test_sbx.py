"""§21.1 — golden argv, and the namespace assertion."""

from __future__ import annotations

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
    spec = _spec(
        name="factory-review-python-harness",
        role="review",
        workspaces=(Workspace(Path("/Users/james/python-harness"), readonly=True),),
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
