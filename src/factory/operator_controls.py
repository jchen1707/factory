"""Explicit project/run settings. No tracker writes or agent launches occur here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factory.execution import PRESETS
from factory.isolation import validate_measurement
from factory.machine import Blocked
from factory.registry import Registry
from factory.store import Store


def configure(
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
    }
    if set(changes) - allowed:
        raise ValueError("unknown operator setting")
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
