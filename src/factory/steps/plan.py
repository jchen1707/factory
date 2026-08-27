"""`worktree_ready -> planning -> implementing` — the repositories' own second path.

This is not a recovery mode the factory invented. Both consumers' `AGENTS.md` describe
`/plan` in one context, then `/implement-from-plan` in a fresh one, as the handoff for
work that is large or ambiguous — and `models.toml` makes that handoff a real model
switch, because the planner role and the builder role are different models.

It is reached two ways: deliberately (`factory run --plan`, or a registry threshold),
and automatically as rung 3 of the §16.3a ladder, when two attempts have failed and
running the same prompt a third time would produce the same failure.

The worktree is **never reset** on a rewind. Attempt 3's plan is written with the
current diff in hand, so it can decide to keep, amend or revert what is there — which
is a judgement the plan step is for and the retry loop is not.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from factory import artifacts
from factory.agent.base import AgentInvocation
from factory.artifacts import AttemptDir
from factory.machine import AUTOMATIC, Blocked, State
from factory.sandbox.base import RunHandle
from factory.steps import Context, advance

__all__ = ["collect", "plan_dir", "should_plan", "start"]

STEP = "plan"

PLAN_FILES = ("plan.md", "test-plan.md")

#: The plan phase's terminal file. Not `exit`: a rung-3 rewind is **one** attempt with
#: two phases sharing one attempt directory, so the plan's exit code cannot occupy the
#: name the implement phase is about to write. Every reader that asks "has this phase
#: finished?" — `sbx.poll` through `RunHandle.exit_name`, and `_exit_code` below — must
#: be told this name, or a finished plan reads as an attempt that never ended.
PLAN_EXIT_NAME = "plan-exit"


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
    return ctx.home / "schemas" / "implement_result.schema.json"


def start(ctx: Context, *, actor: str = AUTOMATIC) -> tuple[AttemptDir, RunHandle, Path] | None:
    """Write the plan prompt and spawn the detached agent. `None` for a dry run.

    `actor` threads through the `advance` into `planning`; see `implement.start`. A rewind
    from `SUSPENDED`/`BLOCKED` is human-gated, a rung-3 rewind from `RESUMABLE` is not.
    """
    attempt = ctx.run.attempt + 1
    worktree = ctx.worktree
    attempt_dir = AttemptDir.create(ctx.factory_dir, attempt)
    role = ctx.routing.role("planner")
    plans = plan_dir(ctx)

    prompt = _prompt(ctx, plans)
    prompt_path = attempt_dir.path("plan-prompt.md")

    invocation = AgentInvocation(
        model=role.model,
        effort=role.effort,
        workdir=str(worktree),
        prompt_path=prompt_path,
        # `/plan` writes files; its final message is prose, so there is no result
        # schema to hand it. The schema file still has to exist for `--output-schema`,
        # so the step points at the implement one and ignores the answer: the evidence
        # that planning happened is `plan.md` and `test-plan.md`, not a JSON blob.
        #
        # The *staged copy*, not `ctx.home`'s original. The agent runs inside the build
        # sandbox and the control plane's home is not mounted there, so a home path is a
        # path codex cannot open: measured on FRO-11 attempt 3, `codex exec` died in
        # under a second with "Failed to read output schema file". The attempt directory
        # resolves to the identical string on both sides -- §14.1's protocol -- which is
        # why `implement.start` has always pointed at its own copy.
        schema_path=attempt_dir.schema,
        output_path=attempt_dir.path("plan-last-message.json"),
        events_path=attempt_dir.path("plan-events.jsonl"),
        stderr_path=attempt_dir.path("plan-stderr.log"),
        exit_path=attempt_dir.path(PLAN_EXIT_NAME),
        heartbeat_path=attempt_dir.heartbeat,
        vault_directory=str(ctx.registry.vault.path),
        env=dict(ctx.project.env),
    )
    script = ctx.agent.wrapper_script(invocation)

    prompt_path.write_text(prompt, encoding="utf-8")
    shutil.copyfile(_schema_source(ctx), attempt_dir.schema)
    # Recorded under the same attempt number the implement phase will use, which is why
    # both write into one attempt directory under `plan-` and bare prefixes: a rewind is
    # one attempt with two phases, not two attempts. `steps/reap.py` needs the row to
    # exist at all — without it a planning run that outlives its tick looks like a run
    # with no attempt, which is the shape of an orphan.
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
    )
    ctx.sandbox.exec_detached(handle, script, dict(ctx.project.env))
    ctx.log("plan.started", model=role.model, effort=role.effort)
    return attempt_dir, handle, invocation.exit_path


def collect(ctx: Context, attempt_dir: AttemptDir) -> None:
    """Both files, or the run blocks. The evidence that planning happened is the files.

    `/plan`'s final message is prose, so there is no schema to validate; what is
    checkable is whether `plan.md` and `test-plan.md` exist, and a `/plan` that finished
    without writing them has not planned.
    """
    plans = plan_dir(ctx)
    missing = [name for name in PLAN_FILES if not (plans / name).exists()]
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
            f"`/plan` finished but did not write {missing} under {plans}",
        )
    ctx.store.record_check(ctx.run.id, ctx.run.attempt, "plan_written", "pass", artifact=str(plans))
    artifacts.write_manifest(attempt_dir.root, produced_by=str(State.PLANNING))
    ctx.store.finish_attempt(
        ctx.run.id,
        ctx.run.attempt,
        State.PLANNING,
        exit_code=_exit_code(attempt_dir),
        outcome="planned",
    )
    ctx.log("plan.finished", plan_dir=str(plans))


def _exit_code(attempt_dir: AttemptDir) -> int | None:
    try:
        return int(attempt_dir.path(PLAN_EXIT_NAME).read_text().strip())
    except (OSError, ValueError):
        return None


def _prompt(ctx: Context, plans: Path) -> str:
    if ctx.issue is None:
        raise Blocked("no-issue-loaded", ctx.run.linear_id)
    lines = [
        f"# Plan {ctx.issue.identifier} — {ctx.issue.title}",
        "",
        "Use the `plan` command from this repository's harness. Write two files under",
        f"`{plans}`: `plan.md` and `test-plan.md`. Write no implementation code in",
        "this turn.",
        "",
        "## Context, already written for you",
        "",
        "- `.factory/context/ticket.md`, `spec.md`, `breakdown.md`, `comments.md`",
        "",
    ]
    if ctx.run.attempt > 0:
        lines += [
            "## What earlier attempts already did",
            "",
            "This is a rewind, not a first pass. The worktree has **not** been reset, so",
            "the current diff is in front of you: decide whether to keep, amend or revert",
            "it, and say which in the plan. The failure evidence is in",
            f"`.factory/run/{ctx.run.attempt}/`.",
            "",
        ]
    lines += [
        "## The test plan is the half that matters",
        "",
        "Every case must be able to fail. A test that passes before the implementation",
        "exists proves nothing about the new behaviour, and this run will replay exactly",
        "that: the test half of the diff, applied at the base ref, must fail.",
    ]
    return "\n".join(lines) + "\n"
