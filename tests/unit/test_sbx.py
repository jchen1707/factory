"""§21.1 — golden argv, and the namespace assertion."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.sandbox.base import SandboxSpec, Workspace
from factory.sandbox.sbx import SbxAdapter, create_argv, exec_argv


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
