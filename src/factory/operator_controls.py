"""Explicit project/run settings. No tracker writes or agent launches occur here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factory.execution import PRESETS
from factory.isolation import validate_measurement
from factory.machine import Blocked, State
from factory.registry import Registry
from factory.store import Run, Store


def _configure(
    store: Store,
    scope: str,
    owner: str,
    changes: dict[str, Any],
    *,
    registry: Registry | None = None,
) -> None:
    changes = dict(changes)
    allowed = {
        "mode",
        "model_preset",
        "delivery_profile",
        "concurrency",
        "workflow",
        "test_design",
        "isolation",
        "isolation_measurement",
        "agent_adapter",
        "app_server_compatibility",
        "certification_mode",
        "certification_config",
        "delegation_mode",
        "max_active_agents",
        "max_children_per_parent",
        "max_delegation_depth",
    }
    if set(changes) - allowed:
        raise ValueError("unknown operator setting")
    if scope not in {"project", "run"}:
        raise ValueError("settings scope must be project or run")
    run = store.run_by_id(owner) if scope == "run" else None
    if scope == "run" and run is None:
        raise ValueError("unknown run")
    project_settings = store.runtime.settings("project", run.project) if run else {}
    modes = {"disabled": 0, "read-only": 1, "isolated-write": 2}
    if "delegation_mode" in changes:
        mode = changes["delegation_mode"]
        if mode is not None and (not isinstance(mode, str) or mode not in modes):
            raise ValueError("invalid delegation mode")
        if (
            run
            and mode is not None
            and modes[mode] > modes[project_settings.get("delegation_mode", "disabled")]
        ):
            raise ValueError("delegation mode exceeds the project capability")
    for field, default in (
        ("max_active_agents", 8),
        ("max_children_per_parent", 2),
        ("max_delegation_depth", 1),
    ):
        if field not in changes or changes[field] is None:
            continue
        value = changes[field]
        if type(value) is not int or value < 1:
            raise ValueError(f"{field} must be a positive integer or inherited")
        if field == "max_delegation_depth" and value != 1:
            raise ValueError("only delegation depth one is supported")
        if run and value > project_settings.get(field, default):
            raise ValueError(f"{field} exceeds the project ceiling")
    if "certification_mode" in changes and changes["certification_mode"] not in (
        "manual",
        "automatic",
    ):
        raise ValueError("certification_mode must be manual or automatic")
    if "certification_config" in changes and (
        not isinstance(changes["certification_config"], str)
        or not Path(changes["certification_config"]).is_absolute()
    ):
        raise ValueError("certification_config must be an absolute host configuration path")
    if {
        "agent_adapter",
        "app_server_compatibility",
        "certification_mode",
        "certification_config",
    } & changes.keys():
        if scope != "project":
            raise ValueError("Runtime selection applies to new project runs")
        effective = store.runtime.settings(scope, owner) | changes
        if effective.get("agent_adapter", "codex-exec") not in {"codex-exec", "app-server"}:
            raise ValueError("Unknown agent adapter")
        automatic = effective.get("certification_mode", "manual") == "automatic"
        if automatic and not effective.get("certification_config"):
            raise ValueError("automatic certification requires certification_config")
        if (
            effective.get("agent_adapter") == "app-server"
            and not automatic
            and not effective.get("app_server_compatibility")
        ):
            raise Blocked(
                "app-server-compatibility-incomplete", "Supply the compatibility manifest directory"
            )
    if "mode" in changes and changes["mode"] not in {"automatic", "approval"}:
        raise ValueError("mode must be automatic or approval")
    if "workflow" in changes and changes["workflow"] not in {"existing", "diagnosis"}:
        raise ValueError("workflow must be existing or diagnosis")
    if "model_preset" in changes and changes["model_preset"] not in {"existing", *PRESETS}:
        raise ValueError("Unknown model preset")
    if "delivery_profile" in changes:
        if changes["delivery_profile"] not in {None, "prototype", "core", "hardening"}:
            raise ValueError("unknown delivery profile")
        snapshot = store.runtime.policy(owner) if scope == "run" else None
        if snapshot and changes["delivery_profile"] == snapshot["profile"]:
            changes.pop("delivery_profile")
        elif snapshot:
            raise Blocked(
                "explicit-policy-replacement-required",
                "Replace the run policy and reverify before delivery",
            )
    if "concurrency" in changes:
        value = changes["concurrency"]
        if value is not None and (type(value) is not int or value < 1):
            raise ValueError("concurrency must be a positive integer or inherited")
        if scope != "project":
            raise ValueError("concurrency is a project setting")
    isolation_keys = {"concurrency", "isolation", "isolation_measurement"}
    if isolation_keys & changes.keys():
        if scope != "project" or registry is None or owner not in registry.projects:
            raise ValueError("isolation settings require a registered project")
        project = registry.projects[owner]
        previous = store.runtime.settings(scope, owner)
        effective = previous | changes
        mode = effective.get("isolation", "shared")
        if mode not in {"shared", "per-run"}:
            raise ValueError("isolation must be shared or per-run")
        if (
            mode == "per-run"
            and previous.get("isolation") != mode
            and "concurrency" not in effective
            and project.concurrency_per_project is None
        ):
            changes["concurrency"] = effective["concurrency"] = 2
        old_limit = previous.get("concurrency") or registry.concurrency_for(project)
        new_limit = effective.get("concurrency") or registry.concurrency_for(project)
        if mode == "per-run" and project.sandbox_delivery:
            raise Blocked("isolation-delivery-boundary", "Sandbox delivery uses its approved scope")
        if new_limit > old_limit or (mode == "per-run" and previous.get("isolation") != mode):
            if mode != "per-run" or not effective.get("isolation_measurement"):
                raise Blocked(
                    "isolation-validation-required",
                    "Retain isolation measurements before raising concurrency",
                )
            validate_measurement(Path(effective["isolation_measurement"]), project)
        if mode == "shared" and previous.get("isolation") == "per-run" and new_limit > 1:
            raise Blocked(
                "isolation-validation-required", "Drain to one slot before sharing sandboxes"
            )
    if "test_design" in changes and type(changes["test_design"]) is not bool:
        raise ValueError("test_design must be boolean")
    store.runtime.configure(scope, owner, changes)


def configure(
    store: Store,
    scope: str,
    owner: str,
    changes: dict[str, Any],
    *,
    registry: Registry | None = None,
) -> None:
    with store.runtime.transaction():
        _configure(store, scope, owner, changes, registry=registry)


def _status(store: Store, project: str, run_id: str | None = None) -> dict[str, Any]:
    """Read retained jobs and accounting; observation never launches or certifies."""
    runs = [
        run
        for run in store.all_runs()
        if run.project == project and (run_id is None or run.id == run_id)
    ]
    agents: list[dict[str, Any]] = []
    children: list[dict[str, Any]] = []
    certifications: list[dict[str, Any]] = []
    preparations: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    invocations: list[dict[str, Any]] = []
    for run in runs:
        preparations.extend(
            {"id": effect.step, "run_id": run.id, "status": effect.status}
            for effect in store.effects(run.id)
            if effect.system == "runtime-preparation"
        )
        agents.extend(
            dict(row)
            for row in store.runtime.db.execute(
                "SELECT invocation_id,parent_id,status FROM agent_leases WHERE run_id=?", (run.id,)
            )
        )
        children.extend(
            dict(row)
            for row in store.runtime.db.execute(
                "SELECT id,parent_id,child_id,status FROM delegation_requests WHERE run_id=?",
                (run.id,),
            )
        )
        certifications.extend(
            dict(row)
            for row in store.runtime.db.execute(
                "SELECT id,status,failure FROM runtime_certifications WHERE run_id=?", (run.id,)
            )
        )
        settings = store.runtime.settings("run", run.id)
        invocation = settings.get("waiting_invocation")
        if invocation:
            waiting.append(
                {
                    "run_id": run.id,
                    "invocation_id": invocation,
                    "approval_mode": store.runtime.effective(project, run.id).get(
                        "mode", "automatic"
                    ),
                }
            )
        for key, value in settings.items():
            if key.startswith("child-status:") and any(
                child["id"] == key.removeprefix("child-status:")
                and child["status"] in {"pending", "prepared"}
                for child in children
            ):
                waiting.append(
                    {
                        "run_id": run.id,
                        "child_id": key.removeprefix("child-status:"),
                        "detail": value,
                    }
                )
        invocations.extend(store.runtime.invocations(run.id))
    active_ids = {item["invocation_id"] for item in agents if item["status"] == "active"}
    terminal = {"completed", "failed", "cancelled", "suspended", "passed"}
    estimates = [(item.get("telemetry") or {}).get("estimate", {}) for item in invocations]
    complete = bool(estimates) and all(item.get("complete") is True for item in estimates)
    complete = complete and all(
        item["status"] in terminal for item in agents + children + certifications
    )
    settings = (
        store.runtime.effective(project, run_id)
        if run_id
        else store.runtime.settings("project", project)
    )
    return {
        "effective": {
            key: settings.get(key, default)
            for key, default in (
                ("delegation_mode", "disabled"),
                ("max_active_agents", 8),
                ("max_children_per_parent", 2),
                ("max_delegation_depth", 1),
                ("certification_mode", "manual"),
            )
        },
        "active_agents": sum(item["status"] == "active" for item in agents),
        "queued_children": sum(
            item["status"] in {"pending", "prepared"} and item["child_id"] not in active_ids
            for item in children
        ),
        "pending_certifications": sum(
            item["status"] in {"pending", "checking"} for item in certifications
        ),
        "runtime_preparations": preparations,
        "agents": agents,
        "children": children,
        "certifications": certifications,
        "waiting": waiting,
        "api_equivalent_estimate_usd": sum(
            (item.get("usd") if item.get("complete") else item.get("known_usd")) or 0
            for item in estimates
        ),
        "cost_complete": complete,
        "unpriced_invocations": sum(not item.get("complete") for item in estimates),
    }


def status(store: Store, project: str, run_id: str | None = None) -> dict[str, Any]:
    """Return one consistent observation of the owned invocation tree and its cost."""
    with store.runtime.transaction():
        return _status(store, project, run_id)


def policy_replacement_refusal(run: Run | None) -> str | None:
    """The shared state prerequisite; lease and source checks still run on submission."""
    if run is None or run.state not in {State.SUSPENDED, State.BLOCKED, State.AWAITING_HUMAN}:
        return "Suspend the run before replacing its policy"
    return None


def approve_pending(store: Store, run: Run, invocation: str) -> None:
    """Approve the pending identity shown by the console, refusing stale forms."""
    with store.runtime.transaction():
        settings = store.runtime.effective(run.project, run.id)
        waiting = settings.get("waiting_invocation")
        if not invocation or invocation != waiting:
            raise ValueError(
                "The pending invocation changed. Reload run settings before approving."
            )
        if settings.get("mode", "automatic") != "approval":
            raise ValueError("This run launches automatically; approval is not required.")
        if settings.get("approved_invocation") == waiting:
            raise ValueError("This invocation is already approved.")
        store.runtime.approve(run.id, invocation)
