"""Retire only child VM generations owned by durable preparation and accounting."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import TYPE_CHECKING, Any

from factory.sandbox.sbx import SbxError

if TYPE_CHECKING:
    from factory.gc import Action
    from factory.registry import Registry
    from factory.sandbox.base import SandboxAdapter
    from factory.store import Store

_TERMINAL = {"completed", "failed", "cancelled", "suspended"}


def parent_hold(store: Store, run_id: str) -> str | None:
    """Parent source remains available until every child has preserved its result."""
    rows = store.runtime.db.execute(
        "SELECT * FROM delegation_requests WHERE run_id=?", (run_id,)
    ).fetchall()
    if rows and any(
        lease["status"] not in _TERMINAL
        for lease in store.runtime.db.execute(
            "SELECT status FROM agent_leases WHERE run_id=?", (run_id,)
        )
    ):
        return "child workflow has unreconciled agent leases"
    for row in rows:
        if row["status"] not in _TERMINAL:
            return "unresolved child request"
        if row["child_id"]:
            lease = store.runtime.db.execute(
                "SELECT status FROM agent_leases WHERE invocation_id=?", (row["child_id"],)
            ).fetchone()
            if lease is None or lease["status"] not in _TERMINAL:
                return "child lease is not terminal"
        try:
            if json.loads(row["request"]).get("task", {}).get(
                "mode"
            ) == "isolated-write" and not _artifact(store, dict(row)):
                return "writable child artifact is not preserved"
        except (ValueError, TypeError, AttributeError):
            return "child artifact evidence is malformed"
    return None


def _artifact(store: Store, request: dict[str, Any]) -> bool:
    rows = store.runtime.db.execute(
        "SELECT external_id FROM effects WHERE run_id=? AND step=? "
        "AND system='child-workspace' AND key='artifact' AND status='confirmed'",
        (request["run_id"], request["id"]),
    ).fetchall()
    if len(rows) != 1 or not rows[0][0]:
        return False
    encoded = rows[0][0]
    artifact = json.loads(encoded)
    result = json.loads(request["result"] or "{}")
    return (
        isinstance(artifact, dict)
        and isinstance(artifact.get("changes"), list)
        and bool(artifact.get("base"))
        and bool(artifact.get("child_head"))
        and result.get("artifact", {}).get("sha256") == hashlib.sha256(encoded.encode()).hexdigest()
    )


def _eligibility(store: Store, effect: dict[str, Any]) -> tuple[str, float]:
    payload = json.loads(effect["external_id"] or "{}")
    name = payload["sandbox"]
    row = store.runtime.db.execute(
        "SELECT * FROM delegation_requests WHERE id=? AND run_id=?",
        (effect["step"], effect["run_id"]),
    ).fetchone()
    if row is None:
        raise ValueError("missing child request")
    request = dict(row)
    task = json.loads(request["request"])
    writable = task.get("task", {}).get("mode") == "isolated-write"
    expected = ("factory-build-child-" if writable else "factory-review-child-") + request["id"]
    if (
        name != expected
        or payload.get("request") != task
        or payload.get("parent_id") != request["parent_id"]
    ):
        raise ValueError("child preparation ownership mismatch")
    if request["status"] not in _TERMINAL:
        raise ValueError("child request is not terminal")
    if writable and not _artifact(store, request):
        raise ValueError("writable artifact is not preserved")
    generations: set[str] = set()
    for cert in store.runtime.db.execute(
        "SELECT identity,status FROM runtime_certifications WHERE run_id=?", (effect["run_id"],)
    ):
        identity = json.loads(cert["identity"])
        if identity.get("sandbox") != name:
            continue
        if cert["status"] not in {"passed", "failed", "cancelled"}:
            raise ValueError("certification is not terminal")
        if identity.get("generation"):
            generations.add(identity["generation"])
    if len(generations) != 1:
        raise ValueError("missing or ambiguous recorded generation")
    activity = float(effect["at"])
    invocation_ids = {request["child_id"]} if request["child_id"] else set()
    for spawn in store.runtime.db.execute(
        "SELECT step,external_id,status,at FROM effects WHERE run_id=? "
        "AND system='agent-launch' AND key='spawn'",
        (effect["run_id"],),
    ):
        launch = json.loads(spawn["external_id"] or "{}")
        if launch.get("handle", {}).get("sandbox") == name:
            invocation_ids.add(spawn["step"])
            activity = max(activity, float(spawn["at"]))
    for identifier in invocation_ids:
        invocation = store.runtime.invocation(identifier)
        lease = store.runtime.db.execute(
            "SELECT status FROM agent_leases WHERE invocation_id=?", (identifier,)
        ).fetchone()
        if lease is None or lease["status"] not in _TERMINAL:
            raise ValueError("child or probe lease is not terminal")
        if invocation is None or invocation["telemetry"] is None:
            raise ValueError("invocation accounting is not retained")
        step = invocation["metadata"].get("cost_step", invocation["role"])
        if not any(
            c["attempt"] == invocation["attempt"] and c["step"] == step
            for c in store.costs(effect["run_id"])
        ):
            raise ValueError("invocation cost accounting is not retained")
        activity = max(activity, float(invocation["updated_at"]))
    # Request completion has no row timestamp; its audit entry is the terminal clock.
    for event in store.runtime.db.execute(
        "SELECT at,payload FROM operator_events WHERE scope='run' AND owner=?",
        (effect["run_id"],),
    ):
        if json.loads(event["payload"]).get("request") == request["id"]:
            activity = max(activity, float(event["at"]))
    return next(iter(generations)), activity


def sweep_children(
    registry: Registry, store: Store, sandbox: SandboxAdapter, *, dry_run: bool, now: float
) -> list[Action]:
    from factory.gc import Action

    actions: list[Action] = []
    rows = store.runtime.db.execute(
        "SELECT * FROM effects WHERE system='child-execution' AND key='prepare'"
    ).fetchall()
    for raw in rows:
        effect = dict(raw)
        name = effect["step"]
        acquired = False
        try:
            payload = json.loads(effect["external_id"] or "{}")
            name = payload.get("sandbox", name)
            if effect["status"] != "confirmed":
                raise ValueError("child preparation is unconfirmed")
            if not dry_run:
                acquired = store.acquire_lease(
                    effect["run_id"], ttl_seconds=600, owner="gc-child-" + uuid.uuid4().hex
                )
                if not acquired:
                    raise ValueError("run controller still owns its lease")
            generation, activity = _eligibility(store, effect)
            age = now - activity
            remove = age >= registry.defaults.gc.sandbox_rm_days * 86400
            if not remove and age < registry.defaults.gc.sandbox_idle_hours * 3600:
                continue
            kind = "sandbox-remove" if remove else "sandbox-stop"
            if not sandbox.exists(name):
                continue
            observe = getattr(sandbox, "generation", None)
            if observe is None or observe(name) != generation:
                raise ValueError("sandbox generation changed or cannot be observed")
            if dry_run:
                actions.append(Action(kind, name, "owned terminal child past age floor", False))
                continue
            for operation in ("stop", "remove") if remove else ("stop",):
                # Reobserve for each effect: a reusable name is never cleanup authority.
                if observe(name) != generation:
                    raise ValueError("sandbox generation changed before cleanup")
                owner = (effect["run_id"], effect["attempt"], effect["step"], "child-gc", operation)
                previous = store.find_effect(*owner)
                if previous and previous.external_id and previous.external_id != generation:
                    raise ValueError("cleanup receipt generation mismatch")
                store.intend_effect(*owner)
                if operation == "stop":
                    sandbox.stop(name)
                else:
                    sandbox.remove(name)
                    if sandbox.exists(name):
                        raise ValueError("sandbox removal did not complete")
                store.confirm_effect(*owner, generation)
            actions.append(Action(kind, name, "owned terminal child past age floor", True))
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            PermissionError,
            OSError,
            SbxError,
        ) as exc:
            actions.append(Action("sandbox-remove", str(name), f"retained: {exc}", False))
        finally:
            if acquired:
                store.release_lease(effect["run_id"])
    return actions
