"""Provision a builder's mailbox before selecting its runtime or recording usage."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from factory import accounting
from factory.delegation_controller import DelegationController
from factory.machine import Blocked
from factory.runtime_jobs import RuntimeJobs

if TYPE_CHECKING:
    from factory.sandbox.base import SandboxSpec
    from factory.steps import Context


def prepare_parent(ctx: Context, attempt: int) -> None:
    settings = ctx.store.runtime.effective(ctx.project.name, ctx.run.id)
    mode = settings.get("delegation_mode", "disabled")
    if mode == "disabled":
        return
    modes = {"disabled": 0, "read-only": 1, "isolated-write": 2}
    ceiling = ctx.store.runtime.settings("project", ctx.project.name).get(
        "delegation_mode", "disabled"
    )
    if not isinstance(mode, str) or mode not in modes:
        raise Blocked("delegation-mode-invalid", str(mode))
    if not isinstance(ceiling, str) or ceiling not in modes or modes[mode] > modes[ceiling]:
        raise Blocked("delegation-project-ceiling", "Run delegation exceeds project permission")
    if (
        settings.get("agent_adapter") != "app-server"
        or settings.get("certification_mode") != "automatic"
    ):
        raise Blocked("delegation-runtime-required", "Delegation requires automatic certification")
    if ctx.project.requires_clone:
        raise Blocked(
            "delegation-clone-transfer-required",
            "Preserve and transfer the private clone before selecting a replacement sandbox",
        )
    launch = settings.get(f"launch:{attempt}:implement", 0) + 1
    identifier = accounting.key(ctx, attempt, "implement", launch=launch)
    from factory.steps.sandbox import base_build_spec

    controller = DelegationController(ctx.store, ctx.home)
    with ctx.store.runtime.transaction():
        retained = ctx.store.runtime.settings("run", ctx.run.id).get("delegation_parent")
        if retained != {"id": identifier, "attempt": attempt} and any(
            lease["run_id"] == ctx.run.id
            for lease in RuntimeJobs(ctx.store).active_agents(ctx.project.name)
        ):
            raise Blocked("delegation-parent-still-active", "Reconcile the previous subtree first")
        spec = controller.prepare(
            ctx.run.id, attempt, identifier, ctx.worktree, base_build_spec(ctx)
        )
        ctx.store.runtime.configure(
            "run",
            ctx.run.id,
            {
                "delegation_mode": mode,
                "delegation_parent": {"id": identifier, "attempt": attempt},
                "build_sandbox": spec.name,
            },
        )
    ctx.project = replace(ctx.project, build_sandbox=spec.name)
    # Creation can be retried after a controller crash; ensure validates existing
    # specifications and subsequent certification observes the actual generation.
    ctx.sandbox.ensure(spec)


def parent_spec(ctx: Context, base: SandboxSpec) -> SandboxSpec:
    """Reconstruct the exact retained mount contract for certification and later steps."""
    retained = ctx.store.runtime.settings("run", ctx.run.id).get("delegation_parent")
    if retained is None:
        return base
    try:
        return DelegationController(ctx.store, ctx.home).prepare(
            ctx.run.id, retained["attempt"], retained["id"], ctx.worktree, base
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Blocked("delegation-preparation-stale", str(exc)) from exc


def parent_configuration(ctx: Context, identifier: str) -> dict | None:
    from factory.steps.sandbox import build_spec

    retained = ctx.store.runtime.settings("run", ctx.run.id).get("delegation_parent")
    if retained is None:
        return None
    if retained["id"] != identifier:
        raise Blocked("delegation-preparation-stale", "Builder ownership changed")
    try:
        return DelegationController(ctx.store, ctx.home).configuration(identifier, build_spec(ctx))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Blocked("delegation-preparation-stale", str(exc)) from exc


def configure_parent(ctx: Context, identifier: str) -> None:
    from factory.agent.app_server import AppServerAdapter

    configuration = parent_configuration(ctx, identifier)
    if configuration is None:
        return
    if not isinstance(ctx.agent, AppServerAdapter) or not ctx.agent.immutable:
        raise Blocked("delegation-runtime-required", "A certified builder is required")
    ctx.agent.delegation = configuration
