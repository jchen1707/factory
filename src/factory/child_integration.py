"""Serialized child integration after parent drain and before ordinary verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from factory import authority, repo
from factory.machine import TERMINAL, Blocked, is_human_held
from factory.sandbox import child_apply, child_commit
from factory.steps import Context


class IntegrationHeld(Blocked):
    """Observe a human stop without converting it into a new blocked transition."""


def advance(ctx: Context) -> None:
    requests = ctx.store.runtime.db.execute(
        "SELECT * FROM delegation_requests WHERE run_id=? AND status='completed' ORDER BY rowid",
        (ctx.run.id,),
    ).fetchall()
    integrated: list[dict[str, Any]] = []
    for row in requests:
        request = dict(row)
        payload = json.loads(request["request"])
        if payload["task"]["mode"] != "isolated-write":
            continue
        parent = ctx.store.runtime.invocation(request["parent_id"])
        if parent is None:
            raise Blocked("child-integration-owner-missing", request["id"])
        lease = ctx.store.runtime.db.execute(
            "SELECT status FROM agent_leases WHERE invocation_id=?", (parent["id"],)
        ).fetchone()
        if lease is None or lease["status"] == "active":
            continue
        # The same lock serializes all factory controllers and repository maintenance.
        with repo.serialized_git(ctx.project.path):
            _integrate(ctx, request, parent, payload)
            integrated.append(request)
    if integrated:
        with repo.serialized_git(ctx.project.path):
            _commit(ctx, integrated)
        _assert_running(ctx)


def _assert_running(ctx: Context) -> None:
    """Fence a surviving sync operation after a later controller records a stop."""
    current = ctx.store.run_by_id(ctx.run.id)
    if current is None or current.state in TERMINAL or is_human_held(current.state):
        state = str(current.state) if current is not None else "missing"
        raise IntegrationHeld(
            "child-integration-held",
            f"Run is {state}; retain child artifacts and settled integration receipts",
        )


def _integrate(
    ctx: Context, request: dict[str, Any], parent: dict[str, Any], payload: dict[str, Any]
) -> None:
    _assert_running(ctx)
    owner = (ctx.run.id, parent["attempt"], request["id"], "child-integration", "complete")
    previous = ctx.store.find_effect(*owner)
    if previous and previous.status == "confirmed":
        return
    files_owner = (*owner[:-1], "files")
    files = ctx.store.find_effect(*files_owner)
    if files and files.status == "confirmed":
        return
    snapshot = ctx.store.runtime.policy(ctx.run.id)
    if snapshot is None or snapshot["revision"] != payload["policy_revision"]:
        raise Blocked("child-integration-authority-stale", request["id"])
    authority.validate_integrity(snapshot)
    # A different attempt must never adopt old pending edits while its writer is running.
    active = ctx.store.runtime.db.execute(
        "SELECT 1 FROM agent_leases WHERE run_id=? AND status='active'", (ctx.run.id,)
    ).fetchone()
    if active:
        raise Blocked("child-integration-writer-active", request["id"])
    result = json.loads(request["result"] or "null")
    if not isinstance(result, dict) or not isinstance(result.get("artifact"), dict):
        raise Blocked("child-integration-artifact-missing", request["id"])
    retained_artifact = ctx.store.find_effect(
        ctx.run.id, parent["attempt"], request["id"], "child-workspace", "artifact"
    )
    if retained_artifact is None or retained_artifact.status != "confirmed":
        raise Blocked("child-integration-artifact-missing", request["id"])
    encoded = retained_artifact.external_id or "{}"
    artifact = json.loads(encoded)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    if result["artifact"].get("sha256") != digest:
        raise Blocked("child-integration-artifact-stale", request["id"])
    generation = ctx.store.runtime.settings("run", ctx.run.id).get("delegation_generation")
    observer = getattr(ctx.sandbox, "generation", None)
    if (
        not generation
        or not callable(observer)
        or observer(ctx.project.build_sandbox) != generation
    ):
        raise Blocked("child-integration-generation-stale", request["id"])
    observed = ctx.sandbox.exec_sync(
        ctx.project.build_sandbox,
        [
            "/usr/bin/git",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(ctx.worktree),
            "rev-parse",
            "HEAD",
        ],
        timeout=60,
    )
    if not observed.ok or observed.stdout.strip() != artifact["base"]:
        raise Blocked("child-integration-base-stale", request["id"])
    ctx.store.intend_effect(*owner)
    script = Path(child_apply.__file__).read_text()
    for change in artifact["changes"]:
        checked = ctx.sandbox.exec_sync(
            ctx.project.build_sandbox,
            [
                "/usr/bin/python3",
                "-I",
                "-S",
                "-c",
                script,
                str(ctx.worktree),
                "check",
            ],
            timeout=60,
            stdin=json.dumps(change, sort_keys=True),
        )
        if not checked.ok or checked.stdout.strip() not in {"checked", "already-applied"}:
            raise Blocked("child-integration-conflict", change["before"]["path"])
    for index, change in enumerate(artifact["changes"]):
        _assert_running(ctx)
        path = change["before"]["path"]
        if not any(
            path == scope or path.startswith(scope + "/") for scope in payload["task"]["paths"]
        ):
            raise Blocked("child-integration-scope-refused", path)
        item_owner = (*owner[:-1], "file:" + str(index))
        retained = ctx.store.find_effect(*item_owner)
        item = json.dumps(change, sort_keys=True)
        if retained and retained.status == "confirmed" and retained.external_id != item:
            raise Blocked("child-integration-artifact-stale", path)
        # Original bytes in this committed receipt are also the preservation artifact.
        ctx.store.intend_effect(*item_owner)
        applied = ctx.sandbox.exec_sync(
            ctx.project.build_sandbox,
            ["/usr/bin/python3", "-I", "-S", "-c", script, str(ctx.worktree)],
            timeout=60,
            stdin=item,
        )
        if not applied.ok or applied.stdout.strip() not in {"applied", "already-applied"}:
            raise Blocked("child-integration-conflict", path)
        ctx.store.confirm_effect(*item_owner, item)
    _assert_running(ctx)
    with ctx.store.runtime.transaction():
        # Existing passes are retained as history but cannot authorize changed code.
        ctx.store.runtime.db.execute(
            "UPDATE checks SET status='stale' WHERE run_id=? AND check_name LIKE 'authority:%'",
            (ctx.run.id,),
        )
        ctx.store.runtime.audit(
            "run", ctx.run.id, "child-integrated", {"request": request["id"], "digest": digest}
        )
        ctx.store.intend_effect(*files_owner)
        ctx.store.confirm_effect(*files_owner, digest)


def _commit(ctx: Context, requests: list[dict[str, Any]]) -> None:
    _assert_running(ctx)
    pending = []
    for row in requests:
        invocation = ctx.store.runtime.invocation(row["parent_id"])
        if invocation is None:
            raise Blocked("child-integration-owner-stale", ctx.run.id)
        done = ctx.store.find_effect(
            ctx.run.id, invocation["attempt"], row["id"], "child-integration", "complete"
        )
        if not done or done.status != "confirmed":
            pending.append(row)
    if not pending:
        return
    parent = ctx.store.runtime.invocation(pending[0]["parent_id"])
    if parent is None or any(row["parent_id"] != parent["id"] for row in pending):
        raise Blocked("child-integration-owner-stale", ctx.run.id)
    owner = (ctx.run.id, parent["attempt"], parent["id"], "child-integration", "commit")
    prepared_owner = (*owner[:-1], "commit-plan")
    previous = ctx.store.find_effect(*prepared_owner)
    if previous and previous.status == "confirmed":
        payload = json.loads(previous.external_id or "{}")
    else:
        artifacts = []
        for row in pending:
            effect = ctx.store.find_effect(
                ctx.run.id, parent["attempt"], row["id"], "child-workspace", "artifact"
            )
            if effect is None:
                raise Blocked("child-integration-artifact-missing", row["id"])
            artifacts.append(json.loads(effect.external_id or "{}"))
        bases = {artifact["base"] for artifact in artifacts}
        if len(bases) != 1:
            raise Blocked("child-integration-base-stale", ctx.run.id)
        intent = ctx.store.intend_effect(*prepared_owner)
        payload = {
            "base": next(iter(bases)),
            "changes": [change for artifact in artifacts for change in artifact["changes"]],
            "time": intent.at,
            "message": "Integrate factory child artifacts "
            + ",".join(row["id"] for row in pending)
            + "\n",
            "requests": [row["id"] for row in pending],
        }
        ctx.store.confirm_effect(*prepared_owner, json.dumps(payload, sort_keys=True))
    if payload["requests"] != [row["id"] for row in pending]:
        raise Blocked("child-integration-plan-stale", ctx.run.id)
    snapshot = ctx.store.runtime.policy(ctx.run.id)
    if snapshot is None or any(
        json.loads(row["request"])["policy_revision"] != snapshot["revision"] for row in pending
    ):
        raise Blocked("child-integration-authority-stale", ctx.run.id)
    authority.validate_integrity(snapshot)
    if ctx.store.runtime.db.execute(
        "SELECT 1 FROM agent_leases WHERE run_id=? AND status='active'", (ctx.run.id,)
    ).fetchone():
        raise Blocked("child-integration-writer-active", ctx.run.id)
    generation = ctx.store.runtime.settings("run", ctx.run.id).get("delegation_generation")
    observer = getattr(ctx.sandbox, "generation", None)
    if (
        not generation
        or not callable(observer)
        or observer(ctx.project.build_sandbox) != generation
    ):
        raise Blocked("child-integration-generation-stale", ctx.run.id)
    for change in payload["changes"]:
        checked = ctx.sandbox.exec_sync(
            ctx.project.build_sandbox,
            [
                "/usr/bin/python3",
                "-I",
                "-S",
                "-c",
                Path(child_apply.__file__).read_text(),
                str(ctx.worktree),
                "check",
            ],
            stdin=json.dumps(change, sort_keys=True),
            timeout=60,
        )
        if not checked.ok or checked.stdout.strip() != "already-applied":
            raise Blocked("child-integration-conflict", change["before"]["path"])
    ctx.store.intend_effect(*owner)
    if payload["changes"]:
        _assert_running(ctx)
        result = ctx.sandbox.exec_sync(
            ctx.project.build_sandbox,
            [
                "/usr/bin/python3",
                "-I",
                "-S",
                "-c",
                Path(child_commit.__file__).read_text(),
                str(ctx.worktree),
            ],
            stdin=json.dumps(payload, sort_keys=True),
            timeout=120,
        )
        if not result.ok:
            raise Blocked("child-integration-commit-held", ctx.run.id)
        commit = result.stdout.strip()
    else:
        commit = payload["base"]
    with ctx.store.runtime.transaction():
        ctx.store.confirm_effect(*owner, commit)
        for row in pending:
            completion = (ctx.run.id, parent["attempt"], row["id"], "child-integration", "complete")
            ctx.store.intend_effect(*completion)
            ctx.store.confirm_effect(*completion, commit)
