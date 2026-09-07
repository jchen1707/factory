"""Snapshot source authority on the host. Shared interpretation remains in layer A."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factory import artifacts, repo
from factory.harness import delivery_policies
from factory.machine import Blocked

if TYPE_CHECKING:
    from factory.registry import Project
    from factory.steps import Context
    from factory.store import Run, Store


@dataclass(frozen=True)
class SnapshotContext:
    """Local inputs for policy publication; no ticket or executing adapters needed."""

    home: Path
    project: Project
    run: Run
    store: Store


def mount(ctx: Context | SnapshotContext) -> Path:
    return ctx.home / "state" / "authority" / ctx.project.name


def current(ctx: Context) -> Path | None:
    snapshot = ctx.store.runtime.policy(ctx.run.id)
    if snapshot:
        validate_integrity(snapshot)
        return Path(snapshot["root"])
    return None


def snapshot(
    ctx: Context | SnapshotContext, *, profile: str | None = None, replace: bool = False
) -> dict[str, Any] | None:
    existing = ctx.store.runtime.policy(ctx.run.id)
    if existing and not replace:
        validate_integrity(existing)
        return existing
    source = ctx.project.path
    raw = json.loads((source / "harness.config.json").read_text())
    if "delivery" not in raw:
        if profile:
            raise Blocked("delivery-policy-undeclared", str(source))
        return None
    selected = (
        profile
        or ctx.store.runtime.effective(ctx.project.name, ctx.run.id).get("delivery_profile")
        or raw["delivery"]["default"]
    )
    if selected not in raw["delivery"]["profiles"]:
        raise Blocked("delivery-profile-undeclared", str(selected))
    revision = existing["revision"] + 1 if existing else 1
    target = mount(ctx) / ctx.run.id / str(revision)
    if target.exists():
        # Publication and the database commit can be interrupted independently.
        # Reconcile a published snapshot; never recopy current source over its authority.
        payload = json.loads((target / "snapshot.json").read_text())
        if payload["root"] != str(target):
            raise Blocked("authority-integrity", "Published snapshot root changed")
        validate_integrity(payload)
        return ctx.store.runtime.snapshot_policy(ctx.run.id, payload, replace=replace)
    staging = target.with_name(f".{revision}.staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    configs = [Path(".")]
    seen: set[Path] = set()
    for relative in configs:
        _relative_path(relative)
        src = source / relative
        if src.resolve() in seen:
            raise Blocked("authority-path-cycle", str(relative))
        seen.add(src.resolve())
        if not src.resolve().is_relative_to(source.resolve()):
            raise Blocked("authority-path-outside-project", str(relative))
        _safe_tree(src / "harness.config.json", source)
        cfg = json.loads((src / "harness.config.json").read_text())
        configs.extend(relative / app for app in cfg.get("apps", []))
        dst = staging / relative
        dst.mkdir(parents=True, exist_ok=True)
        artifacts.write_json(dst / "harness.config.json", cfg)
        review = cfg.get("review", {})
        for location in (
            ".agents/vendor/harness",
            review.get("agentDir", ".agents/agents"),
            review.get("checklistDir", "docs/agents/subagents"),
        ):
            _relative_path(Path(location))
            part = src / location
            _safe_tree(part, source)
            if not part.resolve().is_relative_to(source.resolve()):
                raise Blocked("authority-path-outside-project", str(part))
            if part.is_dir():
                shutil.copytree(part, dst / location, dirs_exist_ok=True)
    resolved = delivery_policies(staging, list(raw["delivery"]["profiles"]))
    payload = {
        "effective": resolved[selected],
        "root": str(target),
        "profile": selected,
        "source_revision": repo._git(source, "rev-parse", "HEAD"),
        "config_sha256": hashlib.sha256((source / "harness.config.json").read_bytes()).hexdigest(),
        "definition": raw["delivery"],
        "deferrals": raw["delivery"]["profiles"][selected]["deferrals"],
    }
    payload["files"] = {
        str(path.relative_to(staging)): artifacts.sha256_of(path)
        for path in sorted(staging.rglob("*"))
        if path.is_file()
    }
    artifacts.write_json(staging / "snapshot.json", payload)
    staging.rename(target)
    return ctx.store.runtime.snapshot_policy(ctx.run.id, payload, replace=replace)


def _relative_path(path: Path) -> None:
    if path.is_absolute() or ".." in path.parts:
        raise Blocked("authority-path-outside-project", str(path))


def _safe_tree(path: Path, source: Path) -> None:
    # Reject links instead of dereferencing mutable targets into trusted authority.
    for item in [path, *path.rglob("*")]:
        if item.is_symlink() or not item.resolve().is_relative_to(source.resolve()):
            raise Blocked("authority-path-outside-project", str(item))


def validate_integrity(payload: dict[str, Any]) -> None:
    """Compare the complete published tree with its host-retained inventory."""
    try:
        root = Path(payload["root"])
        _safe_tree(root, root)
        actual = {
            str(path.relative_to(root)): artifacts.sha256_of(path)
            for path in root.rglob("*")
            if path.is_file() and path != root / "snapshot.json"
        }
        if actual != payload["files"]:
            raise ValueError("Published authority files changed")
        manifest = json.loads((root / "snapshot.json").read_text())
        expected = {key: value for key, value in payload.items() if key != "revision"}
        if manifest != expected:
            raise ValueError("Published authority manifest changed")
    except (OSError, ValueError, KeyError, TypeError, Blocked) as exc:
        raise Blocked("authority-integrity", str(exc)) from exc


def contract(ctx: Context, name: str) -> str:
    root = current(ctx) or ctx.project.path
    path = root / ".agents/vendor/harness/docs/agents" / f"{name}.md"
    if not path.exists():
        raise Blocked("workflow-contract-missing", str(path))
    return path.read_text()


def require_current_evidence(ctx: Context, step: str) -> None:
    snapshot = ctx.store.runtime.policy(ctx.run.id)
    if snapshot is None:
        return
    validate_integrity(snapshot)
    matches = [
        r
        for r in ctx.store.checks(ctx.run.id)
        if r["check_name"] == f"authority:{step}"
        and r["status"] == "pass"
        and r["detail"] == str(snapshot["revision"])
    ]
    if not matches:
        raise Blocked(
            "authority-evidence-stale",
            f"{step} must run against policy revision {snapshot['revision']}",
        )


def record_evidence(ctx: Context, step: str) -> None:
    snapshot = ctx.store.runtime.policy(ctx.run.id)
    if snapshot:
        request = ctx.factory_dir / "run" / str(ctx.run.attempt) / f"{step}-authority.json"
        if (
            not request.exists()
            or json.loads(request.read_text())["revision"] != snapshot["revision"]
        ):
            raise Blocked(
                "authority-evidence-stale", f"{step} ran against an older policy revision"
            )
        ctx.store.record_check(
            ctx.run.id,
            ctx.run.attempt,
            f"authority:{step}",
            "pass",
            detail=str(snapshot["revision"]),
        )


def record_request(ctx: Context, step: str) -> None:
    snapshot = ctx.store.runtime.policy(ctx.run.id)
    if snapshot:
        path = ctx.factory_dir / "run" / str(ctx.run.attempt) / f"{step}-authority.json"
        artifacts.write_json(path, {"revision": snapshot["revision"]})
