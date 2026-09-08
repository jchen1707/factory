"""Project isolation selection. Existing runs retain their recorded sandbox identities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from factory.machine import Blocked
from factory.registry import Project
from factory.store import Run, Store


def project_for_run(project: Project, run: Run, store: Store) -> Project:
    settings = store.runtime.settings("run", run.id)
    build, review = settings.get("build_sandbox"), settings.get("review_sandbox")
    return replace(
        project,
        build_sandbox=build or project.build_sandbox,
        review_sandbox=review or project.review_sandbox,
    )


def prepare(project: Project, run: Run, store: Store) -> Project:
    existing = store.runtime.settings("run", run.id)
    if "isolation" in existing:
        return project_for_run(project, run, store)
    settings = store.runtime.settings("project", project.name)
    mode = settings.get("isolation", "shared")
    if mode == "per-run":
        measurement = settings.get("isolation_measurement")
        if not measurement:
            raise Blocked("isolation-validation-required", "Missing isolation manifest")
        validate_measurement(Path(measurement), project)
        if project.sandbox_delivery:
            raise Blocked(
                "isolation-delivery-boundary", "Sandbox delivery is scoped to its existing sandbox"
            )
        store.runtime.configure(
            "run",
            run.id,
            {
                "isolation": mode,
                "build_sandbox": f"{project.build_sandbox}-{run.id}",
                "review_sandbox": f"{project.review_sandbox}-{run.id}",
            },
        )
    else:
        store.runtime.configure("run", run.id, {"isolation": "shared"})
    return project_for_run(project, run, store)


def validate_measurement(path: Path, project: Project) -> None:
    try:
        evidence = json.loads(path.read_text())
        if evidence["project"] != project.name or evidence["layout"] != (
            "clone" if project.requires_clone else "bind"
        ):
            raise ValueError("project or layout changed")
        for name in (
            "two_runs",
            "dependencies",
            "temporary_files",
            "databases",
            "ports",
            "cancellation",
        ):
            check = evidence["checks"][name]
            captured = path.parent / check["evidence"]
            if (
                check["status"] != "pass"
                or hashlib.sha256(captured.read_bytes()).hexdigest() != check["sha256"]
            ):
                raise ValueError(f"isolation unverified: {name}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Blocked("isolation-validation-required", str(exc)) from exc
