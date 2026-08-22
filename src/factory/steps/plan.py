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
import time
from pathlib import Path

from factory import artifacts
from factory.agent.base import AgentInvocation
from factory.artifacts import AttemptDir
from factory.machine import Blocked, Resumable, State
from factory.sandbox.base import RunHandle
from factory.sandbox.sbx import exec_argv
from factory.steps import Context, advance

__all__ = ["run", "should_plan"]

STEP = "plan"

PLAN_FILES = ("plan.md", "test-plan.md")


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


def run(ctx: Context) -> None:
    """Run layer A's `/plan` in a fresh context and require both files to exist."""
    attempt = ctx.run.attempt + 1
    worktree = ctx.worktree
    attempt_dir = AttemptDir.create(ctx.factory_dir, attempt)
    role = ctx.routing.role("planner")
    branch_slug = (ctx.branch or ctx.run.linear_id).replace("/", "-")
    plan_dir = worktree / ".agents" / "plans" / branch_slug

    prompt = _prompt(ctx, plan_dir)
    prompt_path = attempt_dir.path("plan-prompt.md")

    invocation = AgentInvocation(
        model=role.model,
        effort=role.effort,
        workdir=str(worktree),
        prompt_path=prompt_path,
        # `/plan` writes files; its final message is prose, so there is no result
        # schema to hand it. The schema file still has to exist for `--output-schema`,
        # so the step points at the same one and ignores the answer: the evidence that
        # planning happened is `plan.md` and `test-plan.md`, not a JSON blob.
        schema_path=ctx.home / "schemas" / "implement_result.schema.json",
        output_path=attempt_dir.path("plan-last-message.json"),
        events_path=attempt_dir.path("plan-events.jsonl"),
        stderr_path=attempt_dir.path("plan-stderr.log"),
        exit_path=attempt_dir.path("plan-exit"),
        heartbeat_path=attempt_dir.heartbeat,
        vault_directory=str(ctx.registry.vault.path),
        env=dict(ctx.project.env),
    )
    script = ctx.agent.wrapper_script(invocation)

    if ctx.dry_run:
        ctx.would(f"write {prompt_path}")
        ctx.would(
            " ".join(
                exec_argv(
                    ctx.project.build_sandbox,
                    ["/bin/sh", "-lc", "<the plan script>"],
                    workdir=str(worktree),
                    env=dict(ctx.project.env),
                    detach=True,
                )
            )
        )
        ctx.would(f"require {plan_dir}/plan.md and test-plan.md")
        advance(ctx, State.PLANNING)
        advance(ctx, State.IMPLEMENTING)
        return

    prompt_path.write_text(prompt, encoding="utf-8")
    shutil.copyfile(invocation.schema_path, attempt_dir.schema)
    advance(ctx, State.PLANNING)

    handle = RunHandle(
        run_id=ctx.run.id,
        attempt=attempt,
        sandbox=ctx.project.build_sandbox,
        workdir=str(worktree),
        attempt_dir=attempt_dir.root,
    )
    ctx.sandbox.exec_detached(handle, script, dict(ctx.project.env))
    ctx.log("plan.started", model=role.model, effort=role.effort)

    timeout = ctx.timeout_for(State.PLANNING)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not invocation.exit_path.exists():
        ctx.store.renew_lease(ctx.run.id, ttl_seconds=900)
        time.sleep(10)
    if not invocation.exit_path.exists():
        raise Resumable("plan-timeout", f"no exit file after {timeout}s")

    missing = [name for name in PLAN_FILES if not (plan_dir / name).exists()]
    if missing:
        raise Blocked(
            "plan-incomplete",
            f"`/plan` finished but did not write {missing} under {plan_dir}",
        )
    ctx.store.record_check(
        ctx.run.id, ctx.run.attempt, "plan_written", "pass", artifact=str(plan_dir)
    )
    artifacts.write_manifest(attempt_dir.root, produced_by=str(State.PLANNING))
    ctx.log("plan.finished", plan_dir=str(plan_dir))


def _prompt(ctx: Context, plan_dir: Path) -> str:
    if ctx.issue is None:
        raise Blocked("no-issue-loaded", ctx.run.linear_id)
    lines = [
        f"# Plan {ctx.issue.identifier} — {ctx.issue.title}",
        "",
        "Use the `plan` command from this repository's harness. Write two files under",
        f"`{plan_dir}`: `plan.md` and `test-plan.md`. Write no implementation code in",
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
