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


def prepare_parent(ctx: Context, attempt: int, *, resume_session: str | None = None) -> None:
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
    retained = ctx.store.runtime.settings("run", ctx.run.id).get("delegation_parent")
    if resume_session is not None and retained is None:
        raise Blocked(
            "delegation-session-transfer-required", "No retained runtime owns this thread"
        )
    if ctx.project.requires_clone and retained is None and ctx.run.worktree is not None:
        raise Blocked(
            "delegation-clone-transfer-required",
            "Preserve and transfer the private clone before selecting a replacement sandbox",
        )
    expected_generation = settings.get("delegation_generation")
    generation_reader = getattr(ctx.sandbox, "generation", None)
    if expected_generation is not None and (
        not callable(generation_reader)
        or generation_reader(ctx.project.build_sandbox) != expected_generation
    ):
        raise Blocked("delegation-runtime-stale", "The retained sandbox generation is required")
    if ctx.project.requires_clone and not callable(generation_reader):
        raise Blocked(
            "delegation-generation-required", "Clone preservation needs creation identity"
        )
    launch = settings.get(f"launch:{attempt}:implement", 0) + 1
    identifier = accounting.key(ctx, attempt, "implement", launch=launch)
    from factory.steps.sandbox import base_build_spec

    controller = DelegationController(ctx.store, ctx.home)
    retained = ctx.store.runtime.settings("run", ctx.run.id).get("delegation_parent")
    if (
        retained is not None
        and retained["id"] == identifier
        and retained.get("resume_session") != resume_session
    ):
        raise Blocked("delegation-thread-mismatch", "Prepared thread identity is immutable")
    if (retained is None or retained["id"] != identifier) and any(
        lease["run_id"] == ctx.run.id
        for lease in RuntimeJobs(ctx.store).active_agents(ctx.project.name)
    ):
        raise Blocked("delegation-parent-still-active", "Reconcile the previous subtree first")
    source = ctx.project.path if ctx.project.requires_clone else ctx.worktree
    if retained is not None and retained["id"] != identifier:
        previous = ctx.store.runtime.invocation(retained["id"])
        identity = (
            ((previous or {}).get("metadata", {}).get("runtime_compatibility") or {})
            .get("certification", {})
            .get("identity", {})
        )
        generation = getattr(ctx.sandbox, "generation", None)
        if (
            not callable(generation)
            or identity.get("sandbox") != ctx.project.build_sandbox
            or not identity.get("generation")
            or generation(ctx.project.build_sandbox) != identity["generation"]
        ):
            raise Blocked("delegation-runtime-stale", "The original runtime generation is required")
        expected_generation = identity["generation"]
        if (
            resume_session is not None
            and (previous or {}).get("telemetry", {}).get("thread_id") != resume_session
        ):
            raise Blocked(
                "delegation-thread-mismatch",
                "Thread does not belong to the previous invocation",
            )
        try:
            spec = controller.transfer(
                retained["id"],
                identifier,
                attempt,
                source,
                base_build_spec(ctx),
                resume_session=resume_session,
            )
        except (OSError, ValueError) as exc:
            raise Blocked("delegation-transfer-refused", str(exc)) from exc
    else:
        spec = controller.prepare(ctx.run.id, attempt, identifier, source, base_build_spec(ctx))
    ctx.store.runtime.configure(
        "run",
        ctx.run.id,
        {
            "delegation_mode": mode,
            "delegation_parent": {
                "id": identifier,
                "attempt": attempt,
                "resume_session": resume_session,
            },
            "build_sandbox": spec.name,
            "delegation_generation": expected_generation,
        },
    )
    ctx.project = replace(ctx.project, build_sandbox=spec.name)
    # Creation can be retried after a controller crash; ensure validates existing
    # specifications and subsequent certification observes the actual generation.
    ctx.sandbox.ensure(spec)
    if ctx.project.requires_clone and expected_generation is None:
        if not callable(generation_reader):
            raise Blocked("delegation-generation-required", "Creation identity unavailable")
        ctx.store.runtime.configure(
            "run", ctx.run.id, {"delegation_generation": generation_reader(spec.name)}
        )


def parent_spec(ctx: Context, base: SandboxSpec) -> SandboxSpec:
    """Reconstruct the exact retained mount contract for certification and later steps."""
    retained = ctx.store.runtime.settings("run", ctx.run.id).get("delegation_parent")
    if retained is None:
        return base
    try:
        return DelegationController(ctx.store, ctx.home).prepare(
            ctx.run.id,
            retained["attempt"],
            retained["id"],
            ctx.project.path if ctx.project.requires_clone else ctx.worktree,
            base,
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
