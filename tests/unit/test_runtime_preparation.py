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


def test_changed_preparation_cannot_bypass_uncertainty(tmp_path: Path) -> None:
    from dataclasses import replace

    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="TEST-1", project="test", team="TEST")
    sandbox = Sandbox()
    sandbox.fail = True
    prep = RuntimePreparation(store, sandbox, tmp_path)
    kwargs: dict[str, Any] = {
        "spec": SandboxSpec("test", "review", identity().sandbox, ()),
        "workdir": "/work/tree",
        "env": {},
        "model": "gpt-5.6-sol",
        "observe": identity,
    }
    with pytest.raises(Blocked):
        prep.ensure(run.id, **kwargs)
    sandbox.fail = False
    kwargs["trust_root"] = "/work"
    kwargs["observe"] = lambda: replace(identity(), spec_sha256="8" * 64)
    with pytest.raises(Blocked, match="runtime-preparation-uncertain"):
        prep.ensure(run.id, **kwargs)
    assert sandbox.calls == 1
    store.close()


def test_operator_replacement_preserves_unknown_receipt_and_replays(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="TEST-1", project="test", team="TEST")
    sandbox = Sandbox()
    sandbox.fail = True
    prep = RuntimePreparation(store, sandbox, tmp_path)
    kwargs: dict[str, Any] = {
        "spec": SandboxSpec("test", "review", identity().sandbox, ()),
        "workdir": "/work/tree",
        "env": {},
        "model": "gpt-5.6-sol",
        "observe": identity,
    }
    with pytest.raises(Blocked):
        prep.ensure(run.id, **kwargs)
    old = store.effects(run.id)[0]
    import hashlib
    import json

    assert old.external_id is not None
    previous = json.loads(old.external_id)
    previous["owner"]["preparation"] = "0" * 64  # receipt from the diagnosed older source
    store.runtime.db.execute(
        "UPDATE effects SET external_id=? WHERE run_id=? AND step=?",
        (json.dumps(previous), run.id, old.step),
    )
    old = store.effects(run.id)[0]
    assert old.external_id is not None
    receipt = hashlib.sha256(old.external_id.encode()).hexdigest()
    from dataclasses import replace

    for _ in range(2):
        prep.authorize_replacement(
            run.id,
            old.step,
            receipt_sha256=receipt,
            observe=lambda: replace(identity(), probe_sha256="9" * 64),
            evidence_sha256="a" * 64,
        )
    assert sandbox.calls == 1
    assert store.find_effect(run.id, 0, old.step, "runtime-preparation", "thread-start") == old
    sandbox.fail = False
    kwargs["trust_root"] = "/work"
    prep.ensure(run.id, **kwargs)
    prep.ensure(run.id, **kwargs)
    assert sandbox.calls == 2
    assert store.runtime.invocations(run.id) == []
    store.close()


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ('{"failure":"configuration-changed"}', "configuration-changed"),
        ('{"failure":"secret-canary"}', "worker-failed"),
        ("secret-canary", "worker-failed"),
    ],
)
def test_worker_failure_retains_only_safe_category(
    tmp_path: Path, stdout: str, expected: str
) -> None:
    import json

    class RefusingSandbox(Sandbox):
        def exec_sync(self, *args: Any, **kwargs: Any) -> Completed:
            return Completed((), 1, stdout, "secret-canary")

    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="TEST-1", project="test", team="TEST")
    with pytest.raises(Blocked) as caught:
        RuntimePreparation(store, RefusingSandbox(), tmp_path).ensure(
            run.id,
            spec=SandboxSpec("test", "review", identity().sandbox, ()),
            workdir="/work",
            env={},
            model="gpt-5.6-sol",
            observe=identity,
        )
    failure = next((tmp_path / "state/runtime-preparation").glob("*/failure.json"))
    assert json.loads(failure.read_text())["failure_code"] == expected
    assert "secret-canary" not in failure.read_text() + str(caught.value)
    store.close()


@pytest.mark.parametrize("changed", ["receipt", "generation", "evidence"])
def test_replacement_rejects_changed_evidence(tmp_path: Path, changed: str) -> None:
    import hashlib
    from dataclasses import replace

    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="TEST-1", project="test", team="TEST")
    sandbox = Sandbox()
    sandbox.fail = True
    prep = RuntimePreparation(store, sandbox, tmp_path)
    with pytest.raises(Blocked):
        prep.ensure(
            run.id,
            spec=SandboxSpec("test", "review", identity().sandbox, ()),
            workdir="/work",
            env={},
            model="gpt-5.6-sol",
            observe=identity,
        )
    old = store.effects(run.id)[0]
    assert old.external_id is not None
    import json

    previous = json.loads(old.external_id)
    previous["owner"]["preparation"] = "0" * 64
    store.runtime.db.execute(
        "UPDATE effects SET external_id=? WHERE run_id=? AND step=?",
        (json.dumps(previous), run.id, old.step),
    )
    old = store.effects(run.id)[0]
    assert old.external_id is not None
    with pytest.raises(
        Blocked,
        match={
            "generation": "Identity changed",
            "receipt": "Receipt changed",
            "evidence": "Invalid evidence digest",
        }[changed],
    ):
        prep.authorize_replacement(
            run.id,
            old.step,
            receipt_sha256="f" * 64
            if changed == "receipt"
            else hashlib.sha256(old.external_id.encode()).hexdigest(),
            evidence_sha256="invalid" if changed == "evidence" else "a" * 64,
            observe=lambda: (
                replace(identity(), generation="different")
                if changed == "generation"
                else identity()
            ),
        )
    assert len(store.effects(run.id)) == 1
    store.close()


def test_operator_replacement_requires_corrected_source(tmp_path: Path) -> None:
    import hashlib

    store = Store(tmp_path / "state.db")
    run = store.insert_run(linear_id="TEST-1", project="test", team="TEST")
    sandbox = Sandbox()
    sandbox.fail = True
    prep = RuntimePreparation(store, sandbox, tmp_path)
    with pytest.raises(Blocked):
        prep.ensure(
            run.id,
            spec=SandboxSpec("test", "review", identity().sandbox, ()),
            workdir="/work",
            env={},
            model="gpt-5.6-sol",
            observe=identity,
        )
    old = store.effects(run.id)[0]
    assert old.external_id is not None
    with pytest.raises(Blocked, match="corrected preparation source"):
        prep.authorize_replacement(
            run.id,
            old.step,
            receipt_sha256=hashlib.sha256(old.external_id.encode()).hexdigest(),
            evidence_sha256="a" * 64,
            observe=identity,
        )
    assert len(store.effects(run.id)) == 1
    store.close()
