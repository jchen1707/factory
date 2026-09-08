"""Durable prepared workflow launches, resumed without re-entering step preparation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from factory.agent_launches import AgentLaunches
from factory.machine import Blocked, State
from factory.sandbox.base import RunHandle

if TYPE_CHECKING:
    from factory.agent_launches import DetachedExecution
    from factory.steps import Context
    from factory.store import Store


@contextmanager
def preparation(ctx: Context) -> Iterator[None]:
    """Keep in-memory workflow state aligned with committed preparation, even on rollback."""
    try:
        with ctx.store.runtime.transaction():
            yield
    finally:
        ctx.refresh()


def prepare(
    ctx: Context, invocation_id: str, handle: RunHandle, script: str, *, inputs: tuple[Path, ...]
) -> None:
    """Persist preparation before admission can queue it; model execution stays outside SQLite."""
    from factory import repo

    if ctx.store.runtime.settings("run", ctx.run.id).get("certification_mode") == "automatic":
        from factory.workflow_certification import freeze_script

        script = freeze_script(script, inputs)
    payload = json.dumps(
        {
            "state": str(ctx.state),
            "handle": asdict(handle) | {"attempt_dir": str(handle.attempt_dir)},
            "script": script,
            "env_sha256": _environment(ctx),
            "head": repo.head_sha(ctx.worktree),
            "inputs": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs},
        },
        sort_keys=True,
    )
    with ctx.store.runtime.transaction():
        ctx.store.runtime.configure("run", ctx.run.id, {"waiting_invocation": invocation_id})
        existing = ctx.store.find_effect(
            ctx.run.id, handle.attempt, invocation_id, "agent-preparation", "launch"
        )
        if existing is not None:
            if existing.external_id != payload:
                raise Blocked("launch-preparation-changed", invocation_id)
        else:
            ctx.store.intend_effect(
                ctx.run.id, handle.attempt, invocation_id, "agent-preparation", "launch"
            )
            ctx.store.runtime.db.execute(
                "UPDATE effects SET external_id=? WHERE run_id=? AND attempt=? AND step=? "
                "AND system='agent-preparation' AND key='launch'",
                (payload, ctx.run.id, handle.attempt, invocation_id),
            )


def resume(ctx: Context) -> bool:
    """Resume a prepared current step; an uncertain launch is observed, never repeated."""
    from factory import authority, repo
    from factory.agent.selection import select

    for effect in ctx.store.effects(ctx.run.id):
        if effect.system != "agent-preparation" or effect.status != "intended":
            continue
        payload = json.loads(effect.external_id or "{}")
        if payload["state"] != str(ctx.state) or effect.attempt != ctx.run.attempt:
            continue
        invocation = ctx.store.runtime.invocation(effect.step)
        if invocation is None:
            raise Blocked("launch-invocation-missing", effect.step)
        values = payload["handle"]
        handle = RunHandle(**(values | {"attempt_dir": Path(values["attempt_dir"])}))
        launches = AgentLaunches(ctx.store, ctx.sandbox)
        intent = ctx.store.find_effect(
            ctx.run.id, handle.attempt, effect.step, "agent-launch", "spawn"
        )
        if intent is None:
            authority.current(ctx)
            metadata = invocation["metadata"]
            if metadata.get("semantic_role") == "builder" and metadata.get("parent_id") is None:
                from factory.workflow_delegation import parent_configuration

                parent_configuration(ctx, effect.step)
            if (
                payload["env_sha256"] != _environment(ctx)
                or payload["head"] != repo.head_sha(ctx.worktree)
                or any(
                    not Path(path).is_file()
                    or hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest
                    for path, digest in payload["inputs"].items()
                )
                or metadata["policy_revision"]
                != (ctx.store.runtime.policy(ctx.run.id) or {}).get("revision")
            ):
                raise Blocked("launch-preparation-stale", effect.step)
            report: object
            if (
                ctx.store.runtime.settings("run", ctx.run.id).get("certification_mode")
                == "automatic"
            ):
                from factory.workflow_certification import validate_prepared

                report = validate_prepared(
                    ctx, metadata["runtime_compatibility"], review=ctx.state is State.REVIEWING
                )
            else:
                select(ctx, review=ctx.state is State.REVIEWING)
                report = getattr(ctx.agent, "report", None)
            if metadata["runtime_compatibility"] != report:
                raise Blocked("launch-preparation-stale", effect.step)
            started = launches.start(
                effect.step,
                handle,
                payload["script"],
                ctx.env,
                usd_limit=ctx.routing.usd_per_run,
                max_attempts=ctx.registry.defaults.max_total_attempts,
            )
            if started and ctx.registry.defaults.timings and ctx.state is State.IMPLEMENTING:
                from factory.agent import timings

                timings.spawn(Path(metadata["events"]), handle.attempt_dir / handle.exit_name)
        with ctx.store.runtime.transaction():
            ctx.store.confirm_effect(
                ctx.run.id,
                handle.attempt,
                effect.step,
                "agent-preparation",
                "launch",
                effect.external_id,
            )
            ctx.store.runtime.configure("run", ctx.run.id, {"waiting_invocation": None})
        return True
    return False


def reconcile(ctx: Context) -> None:
    """Collect usage before freeing exited owned slots; leave ambiguous holders reserved."""
    reconcile_run(ctx.store, ctx.home, ctx.sandbox, ctx.run.id, ctx.project.name)


def reconcile_run(
    store: Store, home: Path, sandbox: DetachedExecution, run_id: str, project: str
) -> None:
    """Retain terminal costs before cancellation can archive/remove execution evidence."""
    from factory import accounting
    from factory.delegation_controller import DelegationController
    from factory.runtime_jobs import RuntimeJobs

    controller = DelegationController(store, home)
    controller.service_run(run_id)
    from factory import delegation_cancellation, delegation_results

    delegation_cancellation.reconcile(store, sandbox, run_id)
    delegation_results.collect(store, run_id, sandbox)
    launches = AgentLaunches(store, sandbox)
    active = RuntimeJobs(store).active_agents(project)
    for lease in sorted(active, key=lambda row: row["parent_id"] is None):
        if lease["run_id"] != run_id:
            continue
        invocation = store.runtime.invocation(lease["invocation_id"])
        if invocation is None:
            continue
        if (
            store.find_effect(
                run_id, invocation["attempt"], invocation["id"], "agent-launch", "spawn"
            )
            is None
        ):
            continue
        handle = launches.handle(invocation["id"])
        if lease["parent_id"] is None and (handle.attempt_dir / handle.exit_name).exists():
            controller.cancel_requests(invocation["id"])
            # A paid child still requires owned signalling and terminal collection.
            # Retain parent usage now, but never finalize it over an active subtree.
            if any(child["parent_id"] == invocation["id"] for child in active):
                accounting.collect_invocation(
                    store, home, invocation["id"], Path(invocation["metadata"]["events"])
                )
                continue
        launches.reconcile(
            invocation["id"],
            collect=partial(
                accounting.collect_invocation,
                store,
                home,
                invocation["id"],
                Path(invocation["metadata"]["events"]),
            ),
        )

    delegation_cancellation.reconcile(store, sandbox, run_id)
    delegation_results.collect(store, run_id, sandbox)


def stop_orphans(ctx: Context) -> None:
    """Signal only recorded orphan process groups; absent terminal proof retains capacity."""
    from factory.runtime_jobs import RuntimeJobs
    from factory.sandbox.base import RunStatus
    from factory.steps import read_pgid

    launches = AgentLaunches(ctx.store, ctx.sandbox)
    for lease in RuntimeJobs(ctx.store).active_agents(ctx.project.name):
        if lease["run_id"] != ctx.run.id:
            continue
        invocation = ctx.store.runtime.invocation(lease["invocation_id"])
        if (
            invocation is None
            or ctx.store.find_effect(
                ctx.run.id, invocation["attempt"], invocation["id"], "agent-launch", "spawn"
            )
            is None
        ):
            continue
        handle = launches.handle(invocation["id"])
        if launches.observe(invocation["id"]) != RunStatus.ORPHANED:
            continue
        pgid = read_pgid(handle.attempt_dir / handle.pgid_name)
        if pgid is not None:
            ctx.sandbox.kill_group(handle.sandbox, pgid)
    reconcile(ctx)


def started_at(ctx: Context) -> int | None:
    """The current prepared execution's launch clock excludes its admission wait."""
    row = ctx.store.attempt_row(ctx.run.id, ctx.run.attempt, ctx.state)
    if row is None:
        return None
    for effect in reversed(ctx.store.effects(ctx.run.id)):
        if effect.system != "agent-preparation" or effect.attempt != ctx.run.attempt:
            continue
        payload = json.loads(effect.external_id or "{}")
        if (
            payload["state"] == str(ctx.state)
            and payload["handle"]["attempt_dir"] == row["artifact_dir"]
        ):
            launched = ctx.store.find_effect(
                ctx.run.id, effect.attempt, effect.step, "agent-launch", "spawn"
            )
            return launched.at if launched else None
    return None


def retire_pending(store: Store, run_id: str, attempt: int, state: State) -> bool:
    """Fence a never-launched preparation against admission before Suspend/Cancel."""
    with store.runtime.transaction():
        row = store.attempt_row(run_id, attempt, state)
        if row is None:
            return False
        for effect in reversed(store.effects(run_id)):
            if effect.system != "agent-preparation" or effect.attempt != attempt:
                continue
            payload = json.loads(effect.external_id or "{}")
            if payload["handle"]["attempt_dir"] != row["artifact_dir"]:
                continue
            if payload["state"] != state or effect.status not in {"intended", "cancelled"}:
                continue
            if store.find_effect(run_id, attempt, effect.step, "agent-launch", "spawn") is not None:
                return False
            store.runtime.db.execute(
                "UPDATE effects SET status='cancelled' WHERE run_id=? AND attempt=? AND step=? "
                "AND system='agent-preparation' AND key='launch'",
                (run_id, attempt, effect.step),
            )
            store.runtime.configure("run", run_id, {"waiting_invocation": None})
            store.finish_attempt(run_id, attempt, state, exit_code=None, outcome="not-launched")
            return True
    return False


def _environment(ctx: Context) -> str:
    return hashlib.sha256(json.dumps(dict(ctx.env), sort_keys=True).encode()).hexdigest()
