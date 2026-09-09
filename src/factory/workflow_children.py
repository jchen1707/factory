"""Host-owned, fresh-thread read-only children; never enter the independent review stage."""

from __future__ import annotations

import json
import shlex
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from factory import accounting, authority, child_snapshots, child_workspaces, execution, repo
from factory.agent.app_server import AppServerAdapter
from factory.agent.base import AgentInvocation
from factory.agent_launches import AgentLaunches
from factory.delegation import DelegationBroker
from factory.delegation_controller import _mkdir
from factory.execution import AgentApprovalRequired, ProjectQueued
from factory.machine import Blocked
from factory.sandbox.base import RunHandle, SandboxSpec, Workspace, detached_shell_script
from factory.steps import Context
from factory.workflow_certification import freeze_script, service


def advance(ctx: Context) -> None:
    retained = ctx.store.runtime.settings("run", ctx.run.id).get("delegation_parent")
    if retained is None:
        return
    from factory.workflow_launches import reconcile

    reconcile(ctx)
    # Clone mailboxes are prepared before worktree creation. Until the parent
    # invocation exists there is no launched owner whose children can execute.
    if ctx.store.runtime.invocation(retained["id"]) is None:
        return
    broker = DelegationBroker(ctx.store, retained["id"], ctx.worktree)
    for request in broker.requests():
        if request["status"] not in {"pending", "prepared"}:
            continue
        try:
            _advance(ctx, broker, request)
        except (Blocked, ProjectQueued, AgentApprovalRequired) as exc:
            # One child's queue/refusal must not prevent a sibling or parent observation.
            ctx.store.runtime.configure(
                "run",
                ctx.run.id,
                {
                    "child-status:" + request["id"]: {
                        "reason": getattr(exc, "reason", type(exc).__name__),
                        "detail": str(exc)[:1024],
                    }
                },
            )


def _advance(ctx: Context, broker: DelegationBroker, request: dict[str, Any]) -> None:
    try:
        broker.authorize_preparation(request["id"])
    except (OSError, ValueError) as exc:
        raise Blocked("child-request-refused", request["id"]) from exc
    parent = ctx.store.runtime.invocation(broker.parent_id)
    if parent is None:
        raise Blocked("child-parent-missing", broker.parent_id)
    payload, scratch, trusted = _retain_request(ctx, parent, request)
    writable = request["request"]["task"]["mode"] == "isolated-write"
    source = Path(payload.get("source", str(ctx.worktree)))
    source_mount = source if "snapshot" in payload else ctx.project.path
    spec = SandboxSpec(
        project=ctx.project.name,
        role="build" if writable else "review",
        name=payload["sandbox"],
        workspaces=(
            Workspace(scratch),
            Workspace(source_mount, readonly=not writable),
            Workspace(trusted, readonly=True),
        ),
        template=ctx.project.template or None,
        kits=(),
        static_mcp=(),
        deny_network=ctx.registry.defaults.deny_network,
        env={},
        share_skills=False,
    )
    child_ctx = replace(
        ctx,
        run=replace(ctx.run, worktree=str(source)),
        project=replace(
            ctx.project,
            path=source_mount,
            requires_clone=False,
            env={
                **ctx.project.env,
                "TMPDIR": str(scratch / "tmp"),
                "UV_PROJECT_ENVIRONMENT": str(scratch / "venv"),
            },
        ),
    )
    step = "child:" + request["id"]
    identifier = accounting.key(child_ctx, parent["attempt"], step)
    launches = AgentLaunches(ctx.store, ctx.sandbox)
    if ctx.store.find_effect(ctx.run.id, parent["attempt"], identifier, "agent-launch", "spawn"):
        return
    ctx.sandbox.ensure(spec)
    frozen = ctx.store.find_effect(
        ctx.run.id, parent["attempt"], identifier, "child-execution", "launch"
    )
    runner, inputs = service(
        child_ctx,
        review=not writable,
        spec=spec,
        scratch=scratch / "certification",
        prepare_runtime=frozen is None,
    )
    if frozen is not None:
        launch = json.loads(frozen.external_id or "{}")
        runner.certifications.validate(launch["job"], runner.observe())
        _validate_source(ctx, payload, request["id"])
        if launch["env"] != child_ctx.env:
            raise Blocked("child-environment-stale", request["id"])
    else:
        job = runner.ensure(ctx.run.id, automatic=True)
        if job["status"] != "passed":
            job = runner.advance(job["id"], now=time.time())
        if job["status"] in {"failed", "cancelled"}:
            raise Blocked("child-certification-failed", job["id"])
        if job["status"] != "passed":
            raise ProjectQueued("waiting for child certification " + job["id"])
        current = runner.observe()
        runner.certifications.validate(job["id"], current)
        child_ctx.agent = AppServerAdapter(
            runner.certifications.root / job["id"] / "compatibility.json",
            runtime_version=current.runtime_version,
            sandbox=inputs.spec.name,
            immutable=True,
        )
        role = execution.role_for(child_ctx, request["request"]["task"]["role"])
        directory = scratch / "execution"
        _mkdir(directory)
        prompt = directory / "child.prompt"
        schema = directory / "result.schema.json"
        prompt.write_text(
            payload["instructions"]
            + "\n\n"
            + json.dumps(
                {
                    "task": request["request"],
                    "base": payload["head"],
                    "authority_root": str(trusted),
                },
                sort_keys=True,
            )
        )
        schema.write_text(json.dumps(payload["schema"]))
        invocation = AgentInvocation(
            model=role.model,
            effort=role.effort,
            workdir=str(source),
            prompt_path=prompt,
            schema_path=schema,
            output_path=directory / "result.json",
            events_path=directory / "events.jsonl",
            stderr_path=directory / "stderr",
            exit_path=directory / "exit",
            heartbeat_path=directory / "heartbeat",
            pgid_path=directory / "pgid",
            vault_directory=str(ctx.registry.vault.path),
            env=child_ctx.env,
        )
        child_ctx.agent.prepare(invocation, readonly=not writable)
        script = detached_shell_script(
            heartbeat_path=invocation.heartbeat_path,
            exit_path=invocation.exit_path,
            pgid_path=invocation.pgid_path,
            immutable=True,
            body=shlex.join(child_ctx.agent.command(invocation))
            + " > "
            + shlex.quote(str(invocation.events_path))
            + " 2> "
            + shlex.quote(str(invocation.stderr_path)),
        )
        script = freeze_script(script, (prompt.with_suffix(".app-server.json"),))
        launch = {
            "job": job["id"],
            "script": script,
            "env": child_ctx.env,
            "directory": str(directory),
        }
        with ctx.store.runtime.transaction():
            accounting.begin(
                child_ctx,
                parent["attempt"],
                role,
                step,
                invocation.events_path,
                semantic_role=request["request"]["task"]["role"],
                extra_metadata={
                    "parent_id": parent["id"],
                    "child_result": str(invocation.output_path),
                    "child_result_schema": payload["schema"],
                    "child_mode": request["request"]["task"]["mode"],
                    **(
                        {
                            "child_workspace": payload["workspace"],
                            "child_source_snapshot": payload["snapshot"],
                        }
                        if writable
                        else {}
                    ),
                },
            )
            broker.bind_child(request["id"], identifier)
            launch_owner = (ctx.run.id, parent["attempt"], identifier, "child-execution", "launch")
            ctx.store.intend_effect(*launch_owner)
            ctx.store.confirm_effect(*launch_owner, json.dumps(launch, sort_keys=True))
    _validate_source(ctx, payload, request["id"])
    runner.certifications.validate(launch["job"], runner.observe())
    handle = RunHandle(
        ctx.run.id, parent["attempt"], spec.name, str(source), Path(launch["directory"])
    )
    launches.start(
        identifier,
        handle,
        launch["script"],
        child_ctx.env,
        parent_id=parent["id"],
        usd_limit=ctx.routing.usd_per_run,
        max_attempts=ctx.registry.defaults.max_total_attempts,
    )
    ctx.store.runtime.configure(
        "run", ctx.run.id, {"child-status:" + request["id"]: {"reason": "running"}}
    )


def _retain_request(
    ctx: Context, parent: dict[str, Any], request: dict[str, Any]
) -> tuple[dict[str, Any], Path, Path]:
    snapshot = ctx.store.runtime.policy(ctx.run.id)
    if snapshot is None or snapshot["revision"] != request["request"]["policy_revision"]:
        raise Blocked("child-authority-stale", request["id"])
    authority.validate_integrity(snapshot)
    trusted = Path(snapshot["root"])
    root = ctx.home.resolve() / "state/children" / request["id"]
    if root.is_relative_to(ctx.project.path.resolve()) or ctx.project.path.resolve().is_relative_to(
        root
    ):
        raise Blocked("child-mount-overlap", request["id"])
    if not ctx.worktree.resolve().is_relative_to(ctx.project.path.resolve()):
        raise Blocked("child-source-mount-required", request["id"])
    cloned = None
    workspace = None
    writable = request["request"]["task"]["mode"] == "isolated-write"
    contract = "delegation-child-write.md" if writable else "delegation-child.md"
    if ctx.project.requires_clone or writable:
        try:
            if not (trusted / ".agents/vendor/harness/docs/agents" / contract).read_text().strip():
                raise ValueError("empty child contract")
            cloned = child_snapshots.prepare(ctx, parent, request, root)
            for name in request["request"]["task"]["paths"]:
                path = Path(cloned["path"]) / name
                if (not writable and not path.exists()) or path.is_symlink():
                    raise ValueError("requested clone source path unavailable")
            if writable:
                workspace = child_workspaces.prepare(ctx, parent, request, root, cloned)
        except (OSError, ValueError) as exc:
            raise Blocked("child-source-snapshot-refused", request["id"]) from exc
    scratch = root / "scratch"
    owner = (ctx.run.id, parent["attempt"], request["id"], "child-execution", "prepare")
    with ctx.store.runtime.transaction():
        previous = ctx.store.find_effect(*owner)
        if previous is None:
            try:
                schema = json.loads(
                    (
                        trusted / ".agents/vendor/harness/schema/delegation-result.schema.json"
                    ).read_text()
                )
                instructions = (
                    trusted / ".agents/vendor/harness/docs/agents" / contract
                ).read_text()
                if not instructions.strip():
                    raise ValueError("empty child contract")
            except (OSError, ValueError) as exc:
                raise Blocked("child-contract-unavailable", request["id"]) from exc
            for directory in (root, scratch, scratch / "tmp"):
                _mkdir(directory)
            payload = {
                "parent_id": parent["id"],
                "request": request["request"],
                "sandbox": ("factory-build-child-" if writable else "factory-review-child-")
                + request["id"],
                "scratch": str(scratch),
                "head": cloned["head"] if cloned else repo.head_sha(ctx.worktree),
                **(
                    {
                        "source": workspace["path"] if workspace else cloned["path"],
                        "snapshot": cloned,
                    }
                    if cloned
                    else {}
                ),
                **({"workspace": workspace} if workspace else {}),
                "directories": {
                    str(p): [p.stat().st_dev, p.stat().st_ino] for p in (root, scratch)
                },
                "schema": schema,
                "instructions": instructions,
            }
            ctx.store.intend_effect(*owner)
            ctx.store.confirm_effect(*owner, json.dumps(payload, sort_keys=True))
        else:
            payload = json.loads(previous.external_id or "{}")
    if payload["request"] != request["request"] or payload["parent_id"] != parent["id"]:
        raise Blocked("child-preparation-stale", request["id"])
    for name, identity in payload["directories"].items():
        path = Path(name)
        if (
            path.is_symlink()
            or not path.is_dir()
            or [path.stat().st_dev, path.stat().st_ino] != identity
        ):
            raise Blocked("child-directory-stale", request["id"])
    return payload, scratch, trusted


def _validate_source(ctx: Context, payload: dict[str, Any], identifier: str) -> None:
    if "snapshot" in payload:
        child_snapshots.validate(ctx, payload["snapshot"])
    elif payload["head"] != repo.head_sha(ctx.worktree):
        raise Blocked("child-base-stale", identifier)
