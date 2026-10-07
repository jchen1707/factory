"""A failed `ensure` leaves the run at `sandbox_creating`; the next tick must retry it."""

from __future__ import annotations

import pytest

from factory import driver
from factory.machine import State
from factory.sandbox.base import SandboxSpec
from factory.sandbox.sbx import SbxError
from factory.steps import Context
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from tests.integration.conftest import FakeSandbox


@pytest.mark.parametrize("recover", [False, True])
def test_creation_reentry_rechecks_runtime_without_self_transition(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, recover: bool
) -> None:
    claim_step.run(ctx)
    context_step.run(ctx)
    assert isinstance(ctx.sandbox, FakeSandbox)
    original = ctx.sandbox.ensure
    calls: list[str] = []

    def ensure(spec: SandboxSpec) -> None:
        calls.append(spec.name)
        if len(calls) == 1 or not recover:
            raise SbxError("missing required authority mount")
        original(spec)

    monkeypatch.setattr(ctx.sandbox, "ensure", ensure)
    with pytest.raises(SbxError, match="authority mount"):
        driver.step(ctx)
    assert ctx.state is State.SANDBOX_CREATING
    if recover:
        driver.step(ctx)
        assert ctx.state is State.SANDBOX_READY
    else:
        with pytest.raises(SbxError, match="authority mount"):
            driver.step(ctx)
        assert ctx.state is State.SANDBOX_CREATING
    assert len(calls) == 2
    rows = ctx.store.runtime.db.execute(
        "SELECT from_state, to_state FROM transitions WHERE run_id=?", (ctx.run.id,)
    ).fetchall()
    assert [row[1] for row in rows].count("sandbox_creating") == 1
