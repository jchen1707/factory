"""Noninteractive execution briefs, test design, and fresh diagnosis.

The historical planning state and artifact names remain readable on recovery. New
invocations consume the shared handoff contract and produce a schema-validated result.
The preserved worktree includes both prior commits and uncommitted work.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from factory import accounting, artifacts, execution, handoffs
from factory.agent.base import AgentInvocation, validate_against_schema
from factory.artifacts import AttemptDir
from factory.machine import AUTOMATIC, Blocked, State
from factory.sandbox.base import RunHandle
from factory.steps import Context, advance

__all__ = ["collect", "plan_dir", "should_plan", "start"]

STEP = "plan"

PLAN_FILES = ("plan.md", "test-plan.md")

#: Retain the plan terminal filename for legacy attempts that shared the builder's
#: directory. New preparations use a separate planning directory; all readers still
#: use the recorded handle and this filename when collecting either layout.
PLAN_EXIT_NAME = "plan-exit"

#: The plan phase's own pgid file: legacy rewind phases shared one attempt directory,
#: and a single `pgid` there would let a
#: timeout in one phase signal a process group the other phase started.
PLAN_PGID_NAME = "plan-pgid"


def should_plan(ctx: Context, *, forced: bool = False) -> bool:
    """Is this ticket large or ambiguous enough to plan first?

    `forced` is James typing `--plan`. Otherwise the registry decides, and it decides
    `false` by default: §5.2 says "large or ambiguous" without fixing a threshold, and
    a step that spends a second model run on its own before anyone has seen the first
    one is the wrong default for a first phase.
    """
    if forced:
        return True
    settings = ctx.registry.defaults.planning
    if not settings.auto or ctx.issue is None:
        return False
    return (
        len(ctx.issue.acceptance_criteria) > settings.acceptance_criteria_over
        or len(ctx.issue.description) > settings.description_chars_over
    )


def plan_dir(ctx: Context) -> Path:
    """`.agents/plans/<branch-slug>/` — recomputed rather than remembered.

    `collect` may run in a later process than `start`, so anything it needs must either
    be on disk or be derivable from the run row. This is derivable.
    """
    branch_slug = (ctx.branch or ctx.run.linear_id).replace("/", "-")
    return ctx.worktree / ".agents" / "plans" / branch_slug


def _schema_source(ctx: Context) -> Path:
    """The control-plane original, which is copied into the attempt directory."""
    return ctx.home / "schemas" / "handoff_result.schema.json"


def start(ctx: Context, *, actor: str = AUTOMATIC) -> tuple[AttemptDir, RunHandle, Path] | None:
    """Write the handoff prompt and spawn the detached agent. `None` for a dry run.

    `actor` threads through the `advance` into `planning`; see `implement.start`. A rewind
    from `SUSPENDED`/`BLOCKED` is human-gated, a rung-3 rewind from `RESUMABLE` is not.
    """
    from factory import workflow_launches

    attempt = ctx.run.attempt + 1
    from factory.agent.selection import select

    select(ctx)
    execution.guard(ctx, attempt, STEP, invocation_role=STEP)
    worktree = ctx.worktree
    attempt_dir = AttemptDir(ctx.factory_dir / "run" / str(attempt) / "planning")
    attempt_dir.root.mkdir(parents=True, exist_ok=True)
    settings = ctx.store.runtime.effective(ctx.project.name, ctx.run.id)
    role_name = (
        "diagnoser"
        if ctx.run.attempt
        else "test_designer"
        if settings.get("test_design")
        else "planner"
    )
    role = execution.role_for(ctx, role_name)
    contract = handoffs.contract_name(ctx)
    required_artifacts = (
        handoffs.required_artifacts(ctx, contract) if role_name == "test_designer" else None
    )
    plans = plan_dir(ctx)

    handoffs.write(ctx, ctx.factory_dir / "handoff.json")
    prompt = _prompt(ctx, plans)
    prompt_path = attempt_dir.path("plan-prompt.md")

    invocation = AgentInvocation(
        model=role.model,
        effort=role.effort,
        workdir=str(worktree),
        prompt_path=prompt_path,
        # Stage the schema at the path shared by host and build sandbox.
        schema_path=attempt_dir.schema,
        output_path=attempt_dir.path("plan-last-message.json"),
        events_path=attempt_dir.path("plan-events.jsonl"),
        stderr_path=attempt_dir.path("plan-stderr.log"),
        exit_path=attempt_dir.path(PLAN_EXIT_NAME),
        heartbeat_path=attempt_dir.heartbeat,
        pgid_path=attempt_dir.path(PLAN_PGID_NAME),
        vault_directory=str(ctx.registry.vault.path),
        env=ctx.env,
    )
    script = ctx.agent.wrapper_script(invocation)

    prompt_path.write_text(prompt, encoding="utf-8")
    shutil.copyfile(_schema_source(ctx), attempt_dir.schema)
    reproduction = handoffs.reproduction_binding(ctx) if ctx.run.attempt else {}
    if reproduction:
        schema = json.loads(attempt_dir.schema.read_text())
        identity = reproduction["reproduction_evidence"]
        schema["properties"]["reproduction_evidence"]["enum"] = [identity, ""] if identity else [""]
        artifacts.write_json(attempt_dir.schema, schema)
    request = {
        "contract": contract,
        "role": role_name,
        "diagnosis": ctx.run.attempt > 0,
        "required_artifacts": required_artifacts,
        **reproduction,
    }
    artifacts.write_json(attempt_dir.path("plan-request.json"), request)
    # The brief and builder retain one attempt number with separate execution directories.
    # Publish the attempt and prepared request together so reaping can resume a queued
    # launch without re-entering preparation or mistaking it for an orphan.
    with workflow_launches.preparation(ctx):
        ctx.store.start_attempt(
            ctx.run.id,
            attempt,
            State.PLANNING,
            sandbox=ctx.project.build_sandbox,
            artifact_dir=str(attempt_dir.root),
        )
        ctx.refresh()
        advance(ctx, State.PLANNING, actor=actor)

        handle = RunHandle(
            run_id=ctx.run.id,
            attempt=attempt,
            sandbox=ctx.project.build_sandbox,
            workdir=str(worktree),
            attempt_dir=attempt_dir.root,
            exit_name=PLAN_EXIT_NAME,
            pgid_name=PLAN_PGID_NAME,
        )
        identifier = accounting.begin(
            ctx,
            attempt,
            role,
            STEP,
            invocation.events_path,
            semantic_role=role_name,
            extra_metadata={"handoff_contract": request},
        )
        inputs: tuple[Path, ...] = (
            prompt_path,
            attempt_dir.schema,
            attempt_dir.path("plan-request.json"),
        )
        worker_request = prompt_path.with_suffix(".app-server.json")
        if worker_request.exists():
            inputs += (worker_request,)
        workflow_launches.prepare(ctx, identifier, handle, script, inputs=inputs)
    workflow_launches.resume(ctx)
    ctx.log("plan.started", model=role.model, effort=role.effort)
    return attempt_dir, handle, invocation.exit_path


def collect(ctx: Context, attempt_dir: AttemptDir) -> None:
    """Validate new structured handoffs; retain file-based collection for legacy attempts."""
    accounting.collect(ctx, ctx.run.attempt, STEP, attempt_dir.path("plan-events.jsonl"))
    request_path = attempt_dir.path("plan-request.json")
    recorded = ctx.store.runtime.invocation(accounting.key(ctx, ctx.run.attempt, STEP))
    retained_request = recorded["metadata"].get("handoff_contract") if recorded else None
    request = None
    if request_path.exists():
        try:
            request = json.loads(request_path.read_text())
        except (OSError, ValueError) as exc:
            raise Blocked("handoff-request-invalid", str(exc)) from exc
        if not isinstance(request, dict):
            raise Blocked("handoff-request-invalid", "Expected a request object")
    if retained_request is not None and request != retained_request:
        raise Blocked("handoff-request-changed", "The request differs from its host snapshot")
    diagnosis = False
    required_outputs: tuple[str, ...] = ()
    if request is not None:
        transcript = ctx.agent.read_transcript(
            attempt_dir.path("plan-events.jsonl"), attempt_dir.path("plan-stderr.log")
        )
        if _exit_code(attempt_dir) != 0 or transcript.failed:
            raise Blocked("handoff-agent-failed", transcript.failure or "nonzero exit")
        result = json.loads(attempt_dir.path("plan-last-message.json").read_text())
        validate_against_schema(result, json.loads(attempt_dir.schema.read_text()))
        diagnosis = request["diagnosis"]
        if request.get("role") == "test_designer":
            required_outputs = tuple(handoffs.artifact_names(request.get("required_artifacts")))
        if diagnosis:
            expected_reproduction = (
                {
                    "reproduction_evidence": request.get("reproduction_evidence"),
                    "reproduction_sha256": request.get("reproduction_sha256"),
                }
                if "reproduction_evidence" in request
                else None
            )
            handoffs.authorize_repair(
                ctx, result, ctx.run.attempt, expected_reproduction=expected_reproduction
            )
        elif result["status"] != "ready":
            raise Blocked("readiness-needs-human", result["summary"])
    plans = plan_dir(ctx)
    expected: tuple[str, ...] = (
        ("execution-brief.md", "test-plan.md") if request_path.exists() else PLAN_FILES
    )
    expected = tuple(dict.fromkeys((*expected, *required_outputs)))
    contents = {name: _read_plan(ctx, plans / name) for name in expected}
    # The readiness contract permits proceeding directly, or writing a brief only for
    # technical gaps. Only diagnosis and historical planning promise mandatory files.
    required = expected if diagnosis or not request_path.exists() else required_outputs
    missing = [name for name in required if not contents[name].strip()]
    if missing:
        ctx.store.finish_attempt(
            ctx.run.id,
            ctx.run.attempt,
            State.PLANNING,
            exit_code=_exit_code(attempt_dir),
            outcome="plan-incomplete",
        )
        raise Blocked(
            "plan-incomplete",
            f"Handoff finished but did not write nonempty files {missing} under {plans}",
        )
    # A private clone is invisible to the host. Retain the observed markdown alongside
    # the transcript so the attempt manifest binds the actual collected handoff too.
    retained = attempt_dir.path("planning-output")
    retained.mkdir(parents=True, exist_ok=True)
    for name, content in contents.items():
        if content.strip():
            (retained / name).write_text(content, encoding="utf-8")
    ctx.store.record_check(
        ctx.run.id, ctx.run.attempt, "plan_written", "pass", artifact=str(retained)
    )
    artifacts.write_manifest(attempt_dir.root, produced_by=str(State.PLANNING))
    ctx.store.finish_attempt(
        ctx.run.id,
        ctx.run.attempt,
        State.PLANNING,
        exit_code=_exit_code(attempt_dir),
        outcome="planned",
    )
    ctx.log("plan.finished", plan_dir=str(plans))


def _read_plan(ctx: Context, path: Path) -> str:
    if ctx.project.requires_clone:
        result = ctx.sandbox.exec_sync(
            ctx.project.build_sandbox,
            ["/bin/cat", "--", str(path)],
            env=ctx.env,
            timeout=120,
        )
        return result.stdout if result.ok else ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def _exit_code(attempt_dir: AttemptDir) -> int | None:
    try:
        return int(attempt_dir.path(PLAN_EXIT_NAME).read_text().strip())
    except (OSError, ValueError):
        return None


def _prompt(ctx: Context, plans: Path) -> str:
    return handoffs.prompt(ctx, plans)
