"""Local policy changes must not depend on ticket availability or credentials."""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factory import authority
from factory.cli import main
from factory.console.app import create_app
from factory.intake.linear import LinearClient
from factory.machine import Blocked, State
from factory.steps import Context


@pytest.mark.parametrize("interface", ["cli", "console"])
@pytest.mark.parametrize("valid_config", [True, False])
@pytest.mark.parametrize("state", [State.SUSPENDED, State.BLOCKED, State.AWAITING_HUMAN])
def test_policy_replacement_uses_local_authority_without_reading_ticket(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, interface: str, state: State, valid_config: bool
) -> None:
    root = Path(__file__).parents[2]
    for name in ("hooks/delivery_policy.mjs", "docs/agents/delivery-review.md"):
        target = ctx.project.path / ".agents/vendor/harness" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / ".agents/vendor/harness" / name, target)
    config_path = ctx.project.path / "harness.config.json"
    config = json.loads(config_path.read_text())
    config["delivery"] = {
        "default": "prototype",
        "requirements": {},
        "profiles": {
            name: {"required": [], "deferrals": []} for name in ("prototype", "hardening")
        },
    }
    config_path.write_text(json.dumps(config))
    ctx.run = ctx.store.insert_run(
        linear_id="BAC-5", project=ctx.project.name, team=ctx.project.team, state=state
    )
    ctx.store.update_run(ctx.run.id, attempt=1, worktree=str(ctx.project.path))
    current = ctx.store.run_by_id(ctx.run.id)
    assert current is not None
    ctx.run = current
    authority.snapshot(ctx)
    (ctx.factory_dir / "run/1").mkdir(parents=True)
    for step in ("verify", "review"):
        authority.record_request(ctx, step)
        authority.record_evidence(ctx, step)
    ctx.store.release_lease(ctx.run.id)
    if not valid_config:
        config.pop("gates", None)
        config.pop("apps", None)
        config_path.write_text(json.dumps(config))

    def forbidden_ticket_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("Policy replacement must not read a tracker issue")

    monkeypatch.setattr(LinearClient, "issue", forbidden_ticket_read)
    monkeypatch.setattr(ctx.linear, "issue", forbidden_ticket_read)
    monkeypatch.setenv("FACTORY_HOME", str(ctx.home))
    # Old CLI loads routing before fetching the ticket. Supply it so the red result
    # reaches the precise unwanted tracker dependency rather than a missing file.
    shutil.copyfile(root / "config/models.toml", ctx.home / "config/models.toml")
    if interface == "cli":
        result = main(["configure", "--ticket", ctx.run.linear_id, "--replace-policy", "hardening"])
        assert result == (0 if valid_config else 2)
    else:
        app = create_app(
            ctx.home, registry=ctx.registry, routing=ctx.routing, store=ctx.store, linear=ctx.linear
        )
        response = TestClient(app).post(
            f"/settings/replace-policy/{ctx.run.linear_id}", data={"profile": "hardening"}
        )
        assert response.status_code == (200 if valid_config else 409)
        if not valid_config:
            assert "harness-config-declares-nothing" in response.text
    snapshot = ctx.store.runtime.policy(ctx.run.id)
    assert snapshot is not None
    assert snapshot["revision"] == (2 if valid_config else 1)
    if not valid_config:
        assert not ctx.store.holds_lease(ctx.run.id)
        return
    assert snapshot["profile"] == "hardening"
    assert not ctx.store.holds_lease(ctx.run.id)
    for step in ("verify", "review"):
        with pytest.raises(Blocked, match="authority-evidence-stale"):
            authority.require_current_evidence(ctx, step)


def test_authority_snapshot_retains_runtime_hook_configuration(ctx: Context) -> None:
    for name in ("hooks/delivery_policy.mjs", "docs/agents/delivery-review.md"):
        target = ctx.project.path / ".agents/vendor/harness" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).parents[2] / ".agents/vendor/harness" / name, target)
    root = ctx.project.path
    config_path = root / "harness.config.json"
    config = json.loads(config_path.read_text())
    config["delivery"] = {
        "default": "core",
        "requirements": {},
        "profiles": {"core": {"required": [], "deferrals": []}},
    }
    config_path.write_text(json.dumps(config))
    wiring = root / ".codex/hooks.json"
    wiring.parent.mkdir(exist_ok=True)
    wiring.write_text('{"hooks": {"PreToolUse": []}}')
    snapshot = authority.snapshot(ctx)
    assert snapshot is not None
    assert (Path(snapshot["root"]) / ".codex/hooks.json").read_text() == wiring.read_text()
