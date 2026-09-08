"""Zero-model runtime preparation is owned and never blindly repeated after a crash."""

from pathlib import Path
from typing import Any

import pytest

from factory.machine import Blocked
from factory.runtime_preparation import RuntimePreparation
from factory.sandbox.base import Completed, SandboxSpec
from factory.store import Store
from tests.unit.test_certification import identity


class Sandbox:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def exec_sync(self, *args: Any, **kwargs: Any) -> Completed:
        self.calls += 1
        if self.fail:
            raise ConnectionError("lost acknowledgement")
        return Completed(
            (),
            0,
            '{"thread_id":"prepared-thread","configuration_before":"'
            + "a" * 64
            + '","configuration_after":"'
            + "b" * 64
            + '","model_turns":0}',
            "",
        )


def test_preparation_replays_without_another_thread_or_paid_invocation(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="TEST-1", project="test", team="TEST")
    sandbox = Sandbox()
    prep = RuntimePreparation(store, sandbox, tmp_path)
    kwargs: dict[str, Any] = {
        "spec": SandboxSpec("test", "build", identity().sandbox, ()),
        "workdir": "/work",
        "env": {},
        "model": "gpt-5.6-sol",
        "observe": identity,
    }
    prep.ensure(run.id, **kwargs)
    prep.ensure(run.id, **kwargs)
    assert sandbox.calls == 1
    assert store.runtime.invocations(run.id) == []
    store.close()


def test_unknown_preparation_does_not_launch_another_thread(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="TEST-1", project="test", team="TEST")
    sandbox = Sandbox()
    sandbox.fail = True
    prep = RuntimePreparation(store, sandbox, tmp_path)
    kwargs: dict[str, Any] = {
        "spec": SandboxSpec("test", "build", identity().sandbox, ()),
        "workdir": "/work",
        "env": {},
        "model": "gpt-5.6-sol",
        "observe": identity,
    }
    with pytest.raises(Blocked, match="runtime-preparation"):
        prep.ensure(run.id, **kwargs)
    from dataclasses import replace

    kwargs["observe"] = lambda: replace(identity(), spec_sha256="9" * 64)
    with pytest.raises(Blocked, match="runtime-preparation-uncertain"):
        prep.ensure(run.id, **kwargs)
    assert sandbox.calls == 1
    store.close()


def test_new_generation_requires_its_own_preparation(tmp_path: Path) -> None:
    from dataclasses import replace

    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="TEST-1", project="test", team="TEST")
    sandbox = Sandbox()
    prep = RuntimePreparation(store, sandbox, tmp_path)
    kwargs: dict[str, Any] = {
        "spec": SandboxSpec("test", "build", identity().sandbox, ()),
        "workdir": "/work",
        "env": {},
        "model": "gpt-5.6-sol",
        "observe": identity,
    }
    prep.ensure(run.id, **kwargs)
    kwargs["observe"] = lambda: replace(identity(), generation="replacement-generation")
    prep.ensure(run.id, **kwargs)
    assert sandbox.calls == 2
    assert len(store.effects(run.id)) == 2
    assert store.runtime.invocations(run.id) == []
    store.close()


def test_changed_configuration_is_not_authorized_by_confirmed_preparation(tmp_path: Path) -> None:
    from dataclasses import replace

    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="TEST-1", project="test", team="TEST")
    sandbox = Sandbox()
    prep = RuntimePreparation(store, sandbox, tmp_path)
    kwargs: dict[str, Any] = {
        "spec": SandboxSpec("test", "build", identity().sandbox, ()),
        "workdir": "/work",
        "env": {},
        "model": "gpt-5.6-sol",
        "observe": identity,
    }
    prep.ensure(run.id, **kwargs)
    kwargs["observe"] = lambda: replace(identity(), spec_sha256="9" * 64)
    sandbox.fail = True
    with pytest.raises(Blocked, match="runtime-preparation-failed"):
        prep.ensure(run.id, **kwargs)
    assert sandbox.calls == 2
    store.close()


def test_independent_controller_sees_committed_preparation_before_execution(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    other = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="TEST-1", project="test", team="TEST")
    second = Sandbox()
    kwargs: dict[str, Any] = {
        "spec": SandboxSpec("test", "build", identity().sandbox, ()),
        "workdir": "/work",
        "env": {},
        "model": "gpt-5.6-sol",
        "observe": identity,
    }

    class RacingSandbox(Sandbox):
        def exec_sync(self, *args: Any, **options: Any) -> Completed:
            with pytest.raises(Blocked, match="runtime-preparation-uncertain"):
                RuntimePreparation(other, second, tmp_path).ensure(run.id, **kwargs)
            return super().exec_sync(*args, **options)

    first = RacingSandbox()
    RuntimePreparation(store, first, tmp_path).ensure(run.id, **kwargs)
    RuntimePreparation(other, second, tmp_path).ensure(run.id, **kwargs)
    assert first.calls == 1
    assert second.calls == 0
    store.close()
    other.close()
