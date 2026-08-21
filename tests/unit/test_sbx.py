"""§21.1 — golden argv, and the namespace assertion."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.sandbox.base import SandboxSpec, Workspace
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
