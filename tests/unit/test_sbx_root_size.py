"""A project's `root_size` reaches `sbx create`, and an existing sandbox of another size is refused.

`sbx create` v0.38.0 has no root-size flag and reads `DOCKER_SANDBOXES_ROOT_SIZE` instead.
Neither `sbx inspect --json` nor `sbx ls --json` reports the size, so an existing sandbox is
measured with `df` inside it. The byte counts below are `df -B1 --output=size /` readings
taken on 2026-10-07 from sandboxes created at 20g (the default) and 40g.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from factory.registry import RegistryError, load_registry
from factory.sandbox.base import Completed, SandboxSpec, Workspace
from factory.sandbox.sbx import SbxAdapter, SbxError

NAME = "factory-build-demo"
PROJECT = "/Users/operator/code/demo"
DF = ("sbx", "exec", NAME, "df", "-B1", "--output=size", "/")
MEASURED_20G = 20957446144
MEASURED_40G = 41956900864
ROOT_SIZE_ENV = "DOCKER_SANDBOXES_ROOT_SIZE"


class Recorder:
    def __init__(self, *, exists: bool, df: Completed | None = None) -> None:
        self.exists = exists
        self.df = df
        self.calls: list[tuple[str, ...]] = []
        self.create_env: Mapping[str, str] | None = None

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: int | None = None,
        stdin: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Completed:
        call = tuple(argv)
        self.calls.append(call)
        if call == ("sbx", "inspect", NAME):
            return Completed(call, 0 if self.exists else 1, "", "")
        if call == ("sbx", "ls", "--json"):
            row = {"name": NAME, "workspaces": [PROJECT]}
            return Completed(call, 0, json.dumps({"sandboxes": [row]}), "")
        if call == ("sbx", "inspect", NAME, "--json"):
            return Completed(call, 0, json.dumps({"workspace": PROJECT, "secrets": []}), "")
        if call == DF:
            assert self.df is not None, "the root was measured for a spec that declares no size"
            return self.df
        assert call[:3] == ("sbx", "create", "claude"), f"unexpected call {call}"
        self.create_env = env
        return Completed(call, 0, "", "")


def _adapter(recorder: Recorder) -> SbxAdapter:
    adapter = SbxAdapter()
    adapter._run = recorder  # type: ignore[method-assign]
    return adapter


def _spec(root_size_gib: int | None) -> SandboxSpec:
    return SandboxSpec(
        project="demo",
        role="build",
        name=NAME,
        workspaces=(Workspace(Path(PROJECT)),),
        root_size_gib=root_size_gib,
    )


def _df(total: int) -> Completed:
    return Completed(DF, 0, f"  1B-blocks\n{total}\n", "")


def test_a_declared_size_reaches_sbx_create() -> None:
    recorder = Recorder(exists=False)
    _adapter(recorder).ensure(_spec(40))
    assert recorder.create_env is not None
    assert recorder.create_env[ROOT_SIZE_ENV] == "40g"


def test_an_undeclared_size_drops_one_inherited_from_the_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ROOT_SIZE_ENV, "80g")
    recorder = Recorder(exists=False)
    _adapter(recorder).ensure(_spec(None))
    assert recorder.create_env is not None
    assert ROOT_SIZE_ENV not in recorder.create_env
    assert recorder.create_env["PATH"]


@pytest.mark.parametrize(
    ("declared", "measured"),
    [(40, MEASURED_20G), (20, MEASURED_40G)],
    ids=["grown", "shrunk"],
)
def test_an_existing_sandbox_of_another_size_is_refused(declared: int, measured: int) -> None:
    recorder = Recorder(exists=True, df=_df(measured))
    with pytest.raises(SbxError, match=f'root_size = "{declared}g"') as caught:
        _adapter(recorder).ensure(_spec(declared))
    assert "sbx rm --force" in str(caught.value)
    assert not any(call[:2] == ("sbx", "create") for call in recorder.calls)


@pytest.mark.parametrize(
    ("declared", "measured"),
    [(40, MEASURED_40G), (20, MEASURED_20G)],
)
def test_an_existing_sandbox_of_the_declared_size_is_accepted(declared: int, measured: int) -> None:
    recorder = Recorder(exists=True, df=_df(measured))
    _adapter(recorder).ensure(_spec(declared))
    assert DF in recorder.calls


@pytest.mark.parametrize(
    "df",
    [
        Completed(DF, 1, "", "ERROR: sandbox not running"),
        Completed(DF, 0, "", ""),
        Completed(DF, 0, "  1B-blocks\n-\n", ""),
    ],
    ids=["exec-failed", "empty", "not-a-number"],
)
def test_an_unmeasurable_root_is_refused(df: Completed) -> None:
    with pytest.raises(SbxError, match="cannot measure its root filesystem"):
        _adapter(Recorder(exists=True, df=df)).ensure(_spec(40))


def test_an_undeclared_size_is_not_measured() -> None:
    recorder = Recorder(exists=True)
    _adapter(recorder).ensure(_spec(None))
    assert DF not in recorder.calls


REGISTRY = """
[vault]
path = "/tmp/vault"

[projects.demo]
team = "DEM"
path = "/tmp/demo"
remote = "https://example.invalid/demo.git"
base_branch = "v2"
stack = "python"
build_sandbox = "factory-build-demo"
"""


def _load(tmp_path: Path, extra: str = "") -> int | None:
    path = tmp_path / "projects.toml"
    path.write_text(REGISTRY + extra)
    return load_registry(path).projects["demo"].root_size_gib


def test_the_registry_reads_root_size_in_whole_gib(tmp_path: Path) -> None:
    assert _load(tmp_path, 'root_size = "40g"\n') == 40


def test_a_project_without_root_size_keeps_the_sbx_default(tmp_path: Path) -> None:
    assert _load(tmp_path) is None


@pytest.mark.parametrize("value", ['"40"', "40", '"40G"', '"0g"', '"4.5g"', '"40gb"', '" 40g"'])
def test_a_malformed_root_size_refuses_to_load(tmp_path: Path, value: str) -> None:
    with pytest.raises(RegistryError, match="root_size"):
        _load(tmp_path, f"root_size = {value}\n")
