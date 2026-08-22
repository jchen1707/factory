"""`factory` — the command line: `run`, `tick`, `status`, `doctor` and `cancel`.

`run` drives one ticket in the foreground and is typed by a human. `tick` is the same
pipeline without the human: one pass that reaps whatever a previous pass started,
recovers whatever died, moves every run that can move, and only then looks for new
`ready-for-agent` work. §19's approval boundary for Phase 4 is that James decides when
the timer is loaded — until it is, `tick` is a command like any other.

The two share every step. What differs is who waits: `run` stays with an agent for the
length of its turn, and `tick` starts it and comes back later, because a tick that
blocked on a model run could not reap anything else while it did.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from factory import artifacts, gc, machine, recovery, repo
from factory.agent.codex import CodexAdapter
from factory.harness import load_harness_config, vendor_check
from factory.intake.linear import (
    Condition,
    Issue,
    LinearClient,
    LinearError,
    RepoFacts,
    eligibility_verdict,
    evaluate_eligibility,
    keychain_secret,
)
from factory.machine import Blocked, Resumable, State
from factory.registry import Project, Registry, RegistryError, load_registry
from factory.repo import GitError
from factory.routing import MODEL_CACHE, Routing, RoutingError, load_routing
from factory.sandbox.sbx import SbxAdapter, SbxError, sbx_available
from factory.steps import Context
from factory.steps import block as block_step
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import deliver as deliver_step
from factory.steps import implement as implement_step
from factory.steps import plan as plan_step
from factory.steps import reap as reap_step
from factory.steps import review as review_step
from factory.steps import sandbox as sandbox_step
from factory.steps import verify as verify_step
from factory.steps import worktree as worktree_step
from factory.store import Run, Store

__all__ = ["main"]

LEASE_TTL_SECONDS = 900


def factory_home() -> Path:
    """The repository directory, unless `FACTORY_HOME` overrides it.

    `state/`, `artifacts/` and `logs/` hang off it. Never `~/.factory/`: `sbx skills
    import` scans that path and it belongs to Factory.ai's Droid.
    """
    override = os.environ.get("FACTORY_HOME")
    return Path(override) if override else Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------------
# Intake, in one place
# --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Assessment:
    """§7.1's verdict on one ticket, plus the branch name the conditions were judged against.

    One function, two callers. `factory run` prints it and `factory tick` acts on it —
    and it exists as a function at all because the tick did not have one: `claim_step`
    performs the claim but evaluates no condition, so a poller that only called it would
    have started a run on any ticket carrying the label and discovered the rest by
    spending a model run on it. That is the whole class of failure this project cares
    about, arriving through the component meant to prevent it.
    """

    conditions: list[Condition]
    eligible: bool
    block_reason: str | None
    failures: list[str]
    branch: str
    stale_branches: tuple[str, ...]


def assess(registry: Registry, project: Project, issue: Issue) -> Assessment:
    """Evaluate every intake condition for one ticket. Reads only; writes nothing."""
    harness = load_harness_config(project.path)
    identifier = issue.identifier
    branch = repo.branch_name(
        repo.branch_type_for_labels(list(issue.labels)), identifier, issue.title
    )
    facts = RepoFacts(
        tracker_team=harness.team,
        open_pr_heads=_open_pr_heads(project.path),
        base_ref_subjects=tuple(
            repo.identifier_on_base(project.path, identifier, project.base_ref)
        ),
        remote_branches=tuple(repo.remote_branches_matching(project.path, identifier)),
        expected_branch=branch,
        known_teams=frozenset(p.team for p in registry.projects.values()),
    )
    conditions = evaluate_eligibility(issue, facts)
    eligible, block_reason, failures = eligibility_verdict(conditions)
    return Assessment(
        conditions=conditions,
        eligible=eligible,
        block_reason=block_reason,
        failures=failures,
        branch=branch,
        stale_branches=tuple(b for b in facts.remote_branches if b != f"origin/{branch}"),
    )


# --------------------------------------------------------------------------------
# factory run
# --------------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    home = factory_home()
    registry = load_registry(home / "config" / "projects.toml")
    routing = load_routing(home / "config" / "models.toml")

    ticket = args.ticket.upper()
    project = registry.resolve(ticket)

    linear = LinearClient()
    issue = linear.issue(ticket)

    harness = load_harness_config(project.path)
    assessment = assess(registry, project, issue)
    branch = assessment.branch
    conditions = assessment.conditions
    eligible, block_reason, failures = (
        assessment.eligible,
        assessment.block_reason,
        assessment.failures,
    )

    print(f"{ticket} — {issue.title}")
    print(f"  project {project.name}   base {project.base_ref}   branch {branch}")
    for condition in conditions:
        print(
            f"  {'PASS' if condition.passed else 'FAIL'}  {condition.number:>2}. {condition.label}"
        )

    # A remote branch that merely mentions the identifier is a warning, not a block.
    # `origin/feat/BAC-4-application-skeleton` is the live case: unmerged work that the
    # base-ref check cannot see. It blocks the *branch name* — which `add_worktree`
    # enforces exactly — and it is worth reading before the run, but the work is
    # unlanded and may well be worth redoing.
    stale = list(assessment.stale_branches)
    if stale:
        print(f"  NOTE  a remote branch already mentions {ticket}: {', '.join(stale)}")
        print("        the factory will not reuse it; it is also a free oracle to diff against")

    if not eligible:
        if block_reason is None:
            print(f"\n{ticket} is not eligible and no run was created: {failures}")
            return 1
        print(f"\n{ticket} is blocked: {block_reason} ({failures})")

    store = _open_store(home, dry_run=args.dry_run)
    run = store.insert_run(
        linear_id=ticket,
        project=project.name,
        team=issue.team_key,
        full_review=args.full_review,
    )
    if args.full_review:
        print("  NOTE  --full-review: Tier 2 runs whatever the §15.2 trigger rules decide")

    if not args.dry_run and not store.acquire_lease(run.id, ttl_seconds=LEASE_TTL_SECONDS):
        print(f"\n{ticket} is leased by another process ({run.lease_owner}); nothing to do.")
        return 0

    ctx = Context(
        home=home,
        registry=registry,
        routing=routing,
        store=store,
        linear=linear,
        sandbox=SbxAdapter(),
        agent=CodexAdapter(),
        project=project,
        run=store.run_by_id(run.id) or run,
        issue=issue,
        harness=harness,
        dry_run=args.dry_run,
    )

    if not eligible and block_reason:
        _block(ctx, block_reason, "; ".join(failures))
        return 2

    if ctx.run.state is not State.APPROVED:
        print(
            f"\n{ticket} is already at `{ctx.run.state}` (attempt {ctx.run.attempt}). "
            "`factory run` starts a ticket; it does not pick one up part-way. "
            f"`factory tick` advances a run that is already going, "
            f"`factory status {ticket} --evidence` shows what happened, and "
            f"`factory cancel {ticket}` clears the worktree, the branch and the "
            "tracker state so it can be run again."
        )
        ctx.store.release_lease(ctx.run.id)
        return 1

    codex_stanzas_before = _codex_project_stanzas()

    try:
        _drive(ctx, force_plan=args.plan)
    except Blocked as exc:
        _block(ctx, exc.reason, exc.detail)
        _report(ctx)
        return 2
    except Resumable as exc:
        print(f"\n{ticket} is resumable: {exc.reason} — {exc.detail}")
        if not ctx.dry_run:
            ctx.store.record_transition(
                ctx.run.id,
                from_state=ctx.state,
                to_state=State.RESUMABLE,
                actor="auto",
                rule=exc.reason,
                detail=exc.detail[:2000],
            )
        _report(ctx)
        return 3
    finally:
        if not ctx.dry_run:
            _assert_codex_config_untouched(codex_stanzas_before)
            ctx.store.release_lease(ctx.run.id)

    _report(ctx)
    if ctx.dry_run:
        print("\nDry run. These are the commands it would have executed:\n")
        for line in ctx.planned:
            print(f"  {line}")
        print("\nNothing was written: no Linear call, no git write, no sandbox, no model call.")
    else:
        ctx.refresh()
        if ctx.run.pr_url:
            print(
                f"\nReview ran and a draft pull request is open: {ctx.run.pr_url}\n"
                "Nothing has been merged — merge is a human's. "
                "`factory status <TICKET> --evidence` reads the run back."
            )
        elif ctx.state is State.AWAITING_HUMAN:
            print(
                f"\nThe run stopped at `{ctx.state}` for a human to look. No pull request "
                "was opened. `factory status <TICKET> --evidence` shows why."
            )
        else:
            print(
                f"\nThe run came to rest at `{ctx.state}`. "
                "`factory status <TICKET> --evidence` reads it back."
            )
    return 0


def _drive(ctx: Context, *, force_plan: bool) -> None:
    """The fixed per-ticket shape. Python decides control flow; the model decides only what
    to write inside one step. No agent chooses the next state.

    Phase 3 extends the chain past `verifying`: the review step (which runs the red-phase
    replay and the two-tier review) advances to `pr_ready` on a clean review or
    `awaiting_human` on a finding/escalation, and the deliver step opens the draft PR. A
    `Blocked` from either propagates to `cmd_run`, which records it and announces.

    An adapter failure inside a step becomes a `Blocked` on the way out, so it takes that
    same path. Before this, a `GitError` reached `main`'s catch-all instead: the process
    exited 2, but the run row kept whatever state it had reached, `blocked_reason` stayed
    empty and the tracker was never told — a stopped run that looks live to everything
    that reads state. BAC-4's `1effc543d83a459a` sat at `reviewing` that way after the
    red-phase replay's `git apply` failed. The slug names the state it died in, because
    that is the part a human needs before reading anything else.
    """
    try:
        claim_step.run(ctx)
        context_step.run(ctx)
        sandbox_step.run(ctx)
        worktree_step.run(ctx)
        if plan_step.should_plan(ctx, forced=force_plan):
            plan_step.run(ctx)
        implement_step.run(ctx)
        verify_step.run(ctx)
        review_step.run(ctx)
        if ctx.state is State.PR_READY:
            deliver_step.run(ctx)
    except (GitError, SbxError, LinearError) as exc:
        raise Blocked(f"{ctx.state}-step-failed", str(exc)) from exc


def _block(ctx: Context, reason: str, detail: str) -> None:
    """The one place a block is recorded, so §13.1's tracker write cannot be forgotten.

    The transition is recorded before the announcement, so the comment can name the
    state the run came to rest in rather than the one it was leaving.
    """
    print(f"\nBLOCKED: {reason}\n  {detail}")
    if ctx.dry_run:
        ctx.would(f"block: {reason} ({detail})")
        block_step.announce(ctx, reason, detail)
        return
    ctx.store.update_run(ctx.run.id, blocked_reason=reason)
    if machine.can(ctx.state, State.BLOCKED):
        ctx.store.record_transition(
            ctx.run.id,
            from_state=ctx.state,
            to_state=State.BLOCKED,
            actor="auto",
            rule=reason,
            detail=detail[:2000],
        )
        ctx.refresh()
    ctx.log("run.blocked", level="error", reason=reason, detail=detail[:500])
    block_step.announce(ctx, reason, detail)


def _report(ctx: Context) -> None:
    if not ctx.dry_run:
        ctx.refresh()
    tokens_in, tokens_out, usd = ctx.store.spend(ctx.run.id)
    print(f"\nstate      {ctx.state}")
    print(f"attempt    {ctx.run.attempt}")
    if ctx.run.worktree:
        print(f"worktree   {ctx.run.worktree}")
    if ctx.run.branch:
        print(f"branch     {ctx.run.branch}")
    print(f"tokens     in {tokens_in}, out {tokens_out}")
    print(f"cost       {f'${usd:.2f}' if usd is not None else 'token counts only (§18.3)'}")


# --------------------------------------------------------------------------------
# factory tick
# --------------------------------------------------------------------------------


#: How long a tick holds a run before another process may take it. Longer than a tick
#: interval by a wide margin, because the holder renews on every transition and a lease
#: that expired under a healthy holder is F13's failure, not a recovery.
TICK_LEASE_SECONDS = 900

#: Where the forward dispatch takes over from the poller. `implementing` and `planning`
#: are absent on purpose: those two are `steps/reap.py`'s, because the run in them is
#: not waiting for the factory to do something, it is waiting for an agent that a
#: previous process started.
_FORWARD: dict[State, str] = {
    State.APPROVED: "claim",
    State.CLAIMED: "context",
    State.CONTEXT_LOADED: "sandbox",
    State.SANDBOX_CREATING: "sandbox",
    State.SANDBOX_READY: "worktree",
    State.WORKTREE_READY: "agent",
    State.VERIFYING: "verify",
    State.REVIEWING: "review",
    State.PR_READY: "deliver",
}


def cmd_tick(args: argparse.Namespace) -> int:
    """One pass over everything the factory owns. Safe to run at any instant.

    A tick reaps what a previous tick started, recovers what died, moves every run that
    can move, and then — and only then — looks for new work. That order is the whole
    design: a machine that claimed first would keep starting runs it had not yet noticed
    were broken.

    Nothing here is held in memory between calls. `launchd` will run this every 60 s in
    a fresh process, so every fact it acts on is read from SQLite or from the filesystem
    at the top of the pass.
    """
    home = factory_home()
    registry = load_registry(home / "config" / "projects.toml")
    # F25: an invalid routing table refuses the tick and names the rule. It never falls
    # back to a default, because a factory silently running every role on one model
    # looks exactly like one routing correctly.
    routing = load_routing(home / "config" / "models.toml")

    store = _open_store(home, dry_run=False)
    ok, detail = store.integrity_ok()
    if not ok:
        print(f"tick refused: sqlite says {detail}", file=sys.stderr)
        return 1

    free_gb = shutil.disk_usage(home).free / 1_000_000_000
    room = free_gb >= registry.defaults.disk_min_free_gb
    if not room:
        print(
            f"disk below the floor: {free_gb:.1f} GB free, floor is "
            f"{registry.defaults.disk_min_free_gb} GB. No new claims this tick."
        )

    linear = LinearClient()
    lines = tick_once(
        home,
        registry,
        routing,
        store,
        linear,
        claim=room and not args.no_claim,
        verbose=args.verbose,
    )
    for line in lines:
        print(line)
    if not lines:
        print("nothing to do")
    return 0


#: How a run row becomes a `Context`. Injectable for exactly one reason: §21.3 requires
#: the whole state machine to run in-process against fakes, and the default builds the
#: real `sbx` and `codex` adapters. Nothing in production passes anything else.
ContextFactory = Callable[[Run], Context]


def tick_once(
    home: Path,
    registry: Registry,
    routing: Routing,
    store: Store,
    linear: LinearClient,
    *,
    claim: bool,
    verbose: bool = False,
    context_factory: ContextFactory | None = None,
) -> list[str]:
    """The pass itself, separated from the command so the tests can drive it with fakes."""
    build = context_factory or (
        lambda run: _context_for(home, registry, routing, store, linear, run)
    )
    lines: list[str] = []

    # Reaping and recovery come first, and claiming comes last. A machine that claimed
    # first would keep starting runs it had not yet noticed were broken.
    for run in store.runs_in_states([*reap_step.AGENT_STATES, State.RESUMABLE]):
        line = _work_on(store, run, build, verbose=verbose)
        if line:
            lines.append(line)

    for run in store.runs_in_states(list(_FORWARD)):
        line = _work_on(store, run, build, verbose=verbose)
        if line:
            lines.append(line)

    if claim:
        lines += _claim_new_work(registry, store, linear, build, verbose=verbose)
    return lines


def _work_on(store: Store, run: Run, build: ContextFactory, *, verbose: bool) -> str:
    """Take one run as far as it will go this tick, under a lease, and say what happened.

    Every outcome is a string rather than an exception reaching the caller: one broken
    run must not stop the tick from looking at the others, which is the difference
    between a poller and a script that happens to loop.
    """
    if not store.acquire_lease(run.id, ttl_seconds=TICK_LEASE_SECONDS):
        return f"{run.linear_id:<10} leased by {run.lease_owner}; skipped" if verbose else ""

    ctx: Context | None = None
    try:
        ctx = build(run)
        return _drive_from_here(ctx)
    except Blocked as exc:
        if ctx is not None:
            _block(ctx, exc.reason, exc.detail)
            return f"{run.linear_id:<10} blocked: {exc.reason}"
        return f"{run.linear_id:<10} blocked before its context loaded: {exc.reason}"
    except Resumable as exc:
        if ctx is not None and machine.can(ctx.state, State.RESUMABLE):
            store.record_transition(
                ctx.run.id,
                from_state=ctx.state,
                to_state=State.RESUMABLE,
                actor="auto",
                rule=exc.reason,
                detail=exc.detail[:2000],
            )
        return f"{run.linear_id:<10} resumable: {exc.reason}"
    except (GitError, SbxError, LinearError, RegistryError) as exc:
        # An adapter failure is not a factory crash and must not end the pass. It is
        # reported and the run is left exactly where it was, so the next tick sees the
        # same state and can try again — which is what makes a transient Linear outage
        # (F17) a pause rather than a state change.
        return f"{run.linear_id:<10} adapter error, left in place: {exc}"
    finally:
        store.release_lease(run.id)


def _drive_from_here(ctx: Context) -> str:
    """Advance one run until it is waiting on something that is not the factory.

    The loop ends when the state stops changing, which happens for exactly three
    reasons: an agent is now running, a human is now needed, or the run finished. Every
    body of the loop is a step that was already idempotent, so re-entering a state the
    tick has seen before costs a database read and nothing else.
    """
    steps: list[str] = []
    while True:
        before = ctx.state
        if before in reap_step.AGENT_STATES:
            verdict = reap_step.reap(ctx)
            steps.append(f"{before}:{verdict.outcome}")
            if verdict.outcome in (reap_step.Outcome.RUNNING, reap_step.Outcome.ORPHANED):
                break
            if verdict.outcome is reap_step.Outcome.NEXT_STEP:
                implement_step.start(ctx, continuation=recovery.continuation_prompt(ctx))
                break
        elif before is State.RESUMABLE:
            verdict_r = recovery.resume_run(ctx)
            steps.append(f"resumable:{verdict_r.disposition}({verdict_r.reason})")
            break
        else:
            action = _FORWARD.get(before)
            if action is None:
                break
            _perform(ctx, action)
            steps.append(f"{before}->{ctx.state}")
        if ctx.state is before:
            break
    return f"{ctx.run.linear_id:<10} {'  '.join(steps)}" if steps else ""


def _perform(ctx: Context, action: str) -> None:
    if action == "claim":
        claim_step.run(ctx)
    elif action == "context":
        context_step.run(ctx)
    elif action == "sandbox":
        sandbox_step.run(ctx)
    elif action == "worktree":
        worktree_step.run(ctx)
    elif action == "agent":
        if plan_step.should_plan(ctx):
            plan_step.start(ctx)
        else:
            implement_step.start(ctx)
    elif action == "verify":
        verify_step.run(ctx)
    elif action == "review":
        review_step.run(ctx)
    elif action == "deliver":
        deliver_step.run(ctx)


def _context_for(
    home: Path,
    registry: Registry,
    routing: Routing,
    store: Store,
    linear: LinearClient,
    run: Run,
) -> Context:
    project = registry.resolve(run.linear_id)
    return Context(
        home=home,
        registry=registry,
        routing=routing,
        store=store,
        linear=linear,
        sandbox=SbxAdapter(),
        agent=CodexAdapter(),
        project=project,
        run=run,
        issue=linear.issue(run.linear_id),
        harness=load_harness_config(project.path),
    )


def _claim_new_work(
    registry: Registry,
    store: Store,
    linear: LinearClient,
    build: ContextFactory,
    *,
    verbose: bool,
) -> list[str]:
    """§7.1 — every `ready-for-agent` ticket the registry claims a team for.

    The intake conditions are evaluated **here**, through the same `assess` that
    `factory run` prints, before any row is created. `claim_step` performs the claim; it
    judges nothing, so a poller that trusted it would start a run on any ticket carrying
    the label and learn the rest by spending a model run.

    The two failure shapes are treated as differently as §7.1 means them to be. A
    reasonless failure is a ticket that was never the factory's — no row, no comment, one
    line — and it is re-examined free on every tick, so a dependency that resolves gets
    picked up with no intervention. A reasoned failure is the factory's ticket and not
    ready, so it becomes a `blocked` run a human can see.
    """
    lines: list[str] = []
    teams = sorted({p.team for p in registry.projects.values()})
    for ticket in linear.ready_issues(teams):
        existing = store.live_run_for_ticket(ticket)
        if existing is not None:
            if verbose:
                lines.append(f"{ticket:<10} already has a live run at {existing.state}")
            continue
        try:
            project = registry.resolve(ticket)
        except (Blocked, RegistryError) as exc:
            lines.append(f"{ticket:<10} not claimable: {exc}")
            continue
        busy = store.active_runs_for_project(project.name)
        if len(busy) >= registry.defaults.concurrency_per_project:
            if verbose:
                lines.append(f"{ticket:<10} {project.name} is at its writer limit; waiting")
            continue

        issue = linear.issue(ticket)
        verdict = assess(registry, project, issue)
        if not verdict.eligible and verdict.block_reason is None:
            # Not the factory's ticket, or not the factory's yet. Costs one line and is
            # reconsidered on the next tick for free.
            if verbose:
                lines.append(f"{ticket:<10} not eligible: {'; '.join(verdict.failures)}")
            continue

        run = store.insert_run(linear_id=ticket, project=project.name, team=issue.team_key)
        if verdict.block_reason:
            lines.append(f"{ticket:<10} blocked at intake: {verdict.block_reason}")
            ctx = build(run)
            _block(ctx, verdict.block_reason, "; ".join(verdict.failures))
            continue

        lines.append(f"{ticket:<10} claimed as run {run.id}")
        line = _work_on(store, run, build, verbose=verbose)
        if line:
            lines.append(line)
    return lines


# --------------------------------------------------------------------------------
# factory gc
# --------------------------------------------------------------------------------


def cmd_gc(args: argparse.Namespace) -> int:
    """§16.5. `--dry-run` names every worktree, branch, sandbox and artifact it would
    touch and touches none — and it is the same code path, so what it prints is what a
    real sweep would do rather than a second implementation that could drift."""
    home = factory_home()
    registry = load_registry(home / "config" / "projects.toml")
    store = _open_store(home, dry_run=False)

    actions = gc.sweep(home, registry, store, SbxAdapter(), dry_run=args.dry_run, now=None)
    for action in actions:
        print(action)

    did = sum(1 for a in actions if a.done)
    if args.dry_run:
        print(f"\nDry run: {len(actions)} action(s) considered, none performed.")
    else:
        print(f"\n{did} action(s) performed, {len(actions) - did} skipped or refused.")
    return 0


# --------------------------------------------------------------------------------
# factory status
# --------------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    home = factory_home()
    store = _open_store(home, dry_run=False)
    runs = store.all_runs()
    if args.ticket:
        runs = [r for r in runs if r.linear_id == args.ticket.upper()]
        if not runs:
            print(f"no run for {args.ticket.upper()}")
            return 1

    for run in runs:
        tokens_in, tokens_out, usd = store.spend(run.id)
        lease = "held" if run.lease_owner else "free"
        print(
            f"{run.linear_id:<10} {run.state!s:<16} attempt {run.attempt}  "
            f"lease {lease}  tokens {tokens_in}/{tokens_out}  "
            f"cost {f'${usd:.2f}' if usd is not None else '-'}"
        )
        if run.blocked_reason:
            print(f"           blocked: {run.blocked_reason}")
        if args.evidence:
            for row in store.transitions(run.id):
                stamp = time.strftime("%H:%M:%S", time.localtime(row["at"]))
                rule = f"  [{row['rule']}]" if row["rule"] else ""
                print(f"           {stamp}  {row['from_state']} -> {row['to_state']}{rule}")
            for row in store.checks(run.id):
                print(f"           check {row['check_name']}: {row['status']}")
            for effect in store.effects(run.id):
                print(f"           effect {effect.system}/{effect.key}: {effect.status}")
    return 0


# --------------------------------------------------------------------------------
# factory cancel
# --------------------------------------------------------------------------------


#: Where `cancel` puts a ticket back to. Eligibility condition 2 requires exactly this
#: state, so anything else means the rollback did not actually roll the tracker back.
TODO = "Todo"


def _restore_tracker_for_rerun(linear: LinearClient, ticket: str) -> list[str]:
    """Undo the tracker half of a run, so `factory run` can claim the ticket again.

    Without this, `cancel` cleans the worktree, the branch and the lease and leaves the
    ticket In Progress with `needs-info` on it — a shape eligibility conditions 2 and 7
    both refuse, which made `cmd_run`'s advice to "cancel so it can be run again" false.

    Guarded rather than unconditional: only a ticket sitting in the state the factory
    itself set is moved. If James has since moved it to In Review or Done by hand,
    cancel leaves it alone, because a rollback that overwrites a human's deliberate edit
    is not a rollback. Returns what it did, for printing.

    This is the one Linear write in the factory that §13.1's table does not list. It is
    here because §19's rollback contract promises it in prose, and the alternative —
    deleting the promise instead — leaves every blocked ticket needing a hand edit.
    """
    done: list[str] = []
    issue_uuid, current = linear.issue_uuid(ticket)
    if current == claim_step.IN_PROGRESS:
        team_id = linear.issue(ticket).team_id
        linear.move_state(issue_uuid, linear.workflow_state_id(team_id, TODO))
        done.append(f"moved {ticket} back to {TODO}")
    else:
        done.append(f"left {ticket} at {current!r} (not the state the factory set)")

    labelled_uuid, ids, names = linear.issue_labels(ticket)
    if block_step.NEEDS_INFO in names:
        keep = [i for i, n in zip(ids, names, strict=True) if n != block_step.NEEDS_INFO]
        linear.set_labels(labelled_uuid, keep)
        done.append(f"removed the {block_step.NEEDS_INFO!r} label")
    return done


def _worktree_paths(project: Project, registry: Registry, run: Run, ticket: str) -> list[Path]:
    """Every directory this ticket's worktree could be at: what the run recorded, and
    what the registry says it would be.

    The second is the one defect 6 turned on. `worktree.py` records `worktree` only
    *after* `git worktree add` returns, so a run that dies inside that window leaves
    debris the row never names — and the path is deterministic, so the row was never
    needed to find it (`registry.py:80`).
    """
    expected = project.worktree_path(registry.defaults.worktree_subdir, ticket)
    recorded = Path(run.worktree) if run.worktree else None
    # A `--clone` run records the project path itself: the branch is cut inside the VM
    # and the host-side workdir *is* the repository. That is a workdir, never a worktree
    # the factory created, and offering it here asked `git worktree remove` to delete the
    # repository. `remove_worktree` refuses it too — this keeps it out of the candidate
    # list so the refusal never has to fire.
    if recorded is not None and recorded.resolve() == project.path.resolve():
        recorded = None
    return [expected] if recorded in (None, expected) else [recorded, expected]


def _release_local_debris(
    project: Project, run: Run, ticket: str, paths: Sequence[Path]
) -> list[str]:
    """Undo the worktree and the branch, deriving both from the ticket — defect 6.

    Cancel used to guard this with `if run.worktree:` / `if run.branch:`, which made the
    rollback least capable at the only moment it was needed: the window in which
    `git worktree add` can fail is exactly the window in which neither field is written.
    Both survivors — an unregistered directory and an empty branch — then blocked every
    later run with a `fatal:` that a human had to clear by hand.

    Two refusals bound it, and they are the design. A directory git does not know about
    is removed only when it holds nothing but the factory's own scaffolding; a branch is
    deleted only when it is unpushed *and* carries no commits beyond the base ref.
    Without the second, this fix would convert a rollback gap into a way to lose an
    implementation. Refusals are printed, because the next run will fail on what is left
    and the reason has to be visible before that happens.
    """
    done: list[str] = []
    for path in paths:
        if repo.worktree_exists(project.path, path):
            repo.remove_worktree(project.path, path, force=True)
            done.append(f"removed worktree {path}")
        elif (orphan := repo.orphan_worktree_dir(project.path, path)) is not None:
            if repo.holds_only_factory_scaffolding(orphan):
                shutil.rmtree(orphan)
                done.append(f"removed orphaned worktree directory {orphan}")
            else:
                done.append(f"left {orphan} in place: it holds files the factory did not write")

    # The recorded branch keeps its own rule — cancel deletes the run's own unpushed
    # branch, which is §19's rollback contract and what makes a rerun possible at all.
    if run.branch:
        repo.delete_local_branch(project.path, run.branch)
        done.append(f"deleted local branch {run.branch} (kept if it had been pushed)")

    base_ref = run.base_ref or project.base_ref
    for branch in repo.local_branches_matching(project.path, ticket):
        if (reason := repo.reason_to_keep_branch(project.path, branch, base_ref)) is not None:
            done.append(f"left local branch {branch} in place: {reason}")
        else:
            repo.delete_local_branch(project.path, branch)
            done.append(f"deleted local branch {branch} (empty, and never pushed)")
    return done


def cmd_cancel(args: argparse.Namespace) -> int:
    """Phase 1's rollback. Removes the worktree, deletes the *unpushed* branch, releases
    the lease, and leaves the sandbox stopped. A pushed branch is never deleted.

    Both the directory and the branch are derived from the ticket rather than read from
    the run row, so a run that died before recording either one is still cleaned up —
    see `_release_local_debris`."""
    home = factory_home()
    registry = load_registry(home / "config" / "projects.toml")
    store = _open_store(home, dry_run=False)
    ticket = args.ticket.upper()
    run = store.run_by_ticket(ticket)
    if run is None:
        print(f"no run for {ticket}")
        return 1

    project = registry.projects[run.project]
    paths = _worktree_paths(project, registry, run, ticket)

    for path in paths:
        # Archive before removing. A rollback that destroys the evidence of why the run
        # needed rolling back is not a rollback, it is a cover-up.
        attempts = path / ".factory" / "run"
        if attempts.is_dir():
            harness = load_harness_config(project.path)
            for directory in sorted(attempts.iterdir()):
                if directory.is_dir():
                    kept = artifacts.archive(
                        directory,
                        home / "artifacts" / run.linear_id / directory.name,
                        extra_names=harness.secret_vars,
                    )
                    print(f"archived {directory.name} to {kept}")

    for line in _release_local_debris(project, run, ticket, paths):
        print(line)

    if machine.can(run.state, State.CANCELLED):
        store.record_transition(
            run.id,
            from_state=run.state,
            to_state=State.CANCELLED,
            actor="human",
            rule="abandon-is-james",
            detail=args.reason,
        )
    store.release_lease(run.id)
    SbxAdapter().stop(project.build_sandbox)

    # Last, and never fatal: the local cleanup above has already happened, and a Linear
    # outage must not turn a completed rollback into a failed command.
    try:
        for line in _restore_tracker_for_rerun(LinearClient(), ticket):
            print(line)
    except LinearError as exc:
        print(f"could not restore {ticket} in Linear ({exc}); move it back to {TODO} by hand")

    print(f"{ticket} cancelled; sandbox {project.build_sandbox} stopped")
    return 0


# --------------------------------------------------------------------------------
# factory doctor
# --------------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    home = factory_home()
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    # -- configuration ------------------------------------------------------------
    registry: Registry | None = None
    try:
        registry = load_registry(home / "config" / "projects.toml")
        check("registry", True, f"{len(registry.projects)} projects")
    except (RegistryError, OSError) as exc:
        check("registry", False, str(exc))

    try:
        routing = load_routing(home / "config" / "models.toml")
        check(
            "routing",
            True,
            f"builder={routing.roles['builder'].model}/{routing.roles['builder'].effort}, "
            f"reviewer={routing.roles['reviewer'].model}, ceiling ${routing.usd_per_run:g}",
        )
    except (RoutingError, OSError) as exc:
        check("routing", False, str(exc))

    try:
        machine.assert_table_is_sound()
        check("state table", True, f"{len(machine.TRANSITIONS)} states")
    except AssertionError as exc:
        check("state table", False, str(exc))

    # -- the model cache, against the CLI that will read it ------------------------
    check(*_model_cache_check())

    # -- external tools -----------------------------------------------------------
    for tool, argv in (
        ("git", ["git", "--version"]),
        ("gh", ["gh", "auth", "status"]),
        ("codex", ["codex", "--version"]),
    ):
        _, ok, detail = _tool_check(argv)
        check(tool, ok, detail)

    ok, detail = sbx_available()
    check("sbx", ok, detail.splitlines()[0] if detail else "")

    # -- host configuration James owns (§20.5) ------------------------------------
    check(*_global_gitignore_check())
    check(*_keychain_check())
    check(*_skills_check())
    check(
        "~/.factory absent",
        not (Path.home() / ".factory").exists(),
        "sbx skills import scans ~/.factory/skills, which is Factory.ai's Droid",
    )

    # -- state --------------------------------------------------------------------
    store = _open_store(home, dry_run=False)
    ok, detail = store.integrity_ok()
    check("database", ok, f"{store.path} — {detail}")

    free_gb = shutil.disk_usage(home).free / 1_000_000_000
    floor = registry.defaults.disk_min_free_gb if registry else 20
    check("disk", free_gb >= floor, f"{free_gb:.0f} GB free, floor {floor} GB")

    if registry:
        for project in registry.projects.values():
            ok, detail = vendor_check(
                project.path, Path.home() / "harness" / "scripts" / "vendor_sync.py"
            )
            check(
                f"vendored layer A in {project.name}", ok, detail.splitlines()[-1] if detail else ""
            )

    check(*_prices_check(home))

    if args.deep and registry:
        check(*_deep_canary(registry))

    width = max(len(name) for name, _, _ in results)
    failures = 0
    for name, ok, detail in results:
        failures += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    print()
    if failures:
        print(f"{failures} check(s) failed.")
    else:
        print("All checks passed.")
    return 1 if failures else 0


def _tool_check(argv: Sequence[str]) -> tuple[str, bool, str]:
    name = argv[0]
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return name, False, str(exc)
    output = (proc.stdout or proc.stderr).strip().splitlines()
    return name, proc.returncode == 0, output[0] if output else ""


def _model_cache_check() -> tuple[str, bool, str]:
    """A routing table pinned to a model the CLI no longer offers fails at the worst
    moment, so `doctor` compares the cache's `client_version` with the installed CLI."""
    if not MODEL_CACHE.exists():
        return "model cache", False, f"{MODEL_CACHE} is missing"
    try:
        cache = json.loads(MODEL_CACHE.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return "model cache", False, str(exc)
    proc = subprocess.run(
        ["codex", "--version"], capture_output=True, text=True, check=False, timeout=60
    )
    cli = proc.stdout.strip().split()[-1] if proc.returncode == 0 else "unknown"
    cached = cache.get("client_version", "unknown")
    return (
        "model cache",
        cached == cli,
        f"cache {cached} (fetched {cache.get('fetched_at', '?')}) vs CLI {cli}"
        + ("" if cached == cli else " — open the TUI's /model picker once to refresh"),
    )


def _global_gitignore_check() -> tuple[str, bool, str]:
    """`.factory/` belongs in the **global** gitignore, not in any repository's.

    That keeps control-plane state out of every product diff with no commit to any
    product repo. It is James's file to write (§20.5), so this names the fix rather
    than applying it.
    """
    configured = subprocess.run(
        ["git", "config", "--global", "core.excludesfile"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    path = (
        Path(configured).expanduser() if configured else Path.home() / ".config" / "git" / "ignore"
    )
    if path.exists() and ".factory" in path.read_text():
        return "global gitignore", True, f"{path} ignores .factory/"
    return (
        "global gitignore",
        False,
        f"add `.factory/` to {path} — `mkdir -p {path.parent} && echo '.factory/' >> {path}`",
    )


def _keychain_check() -> tuple[str, bool, str]:
    try:
        keychain_secret()
    except LinearError as exc:
        return "linear credential", False, str(exc)
    return "linear credential", True, "factory-linear present (value not read into any log)"


def _skills_check() -> tuple[str, bool, str]:
    """The execution set, and the version it is pinned to.

    `implement` is inlined rather than invoked (P0-15), so its *file* has to be there
    even though Codex will never list it. The pinned version directory is compared with
    what the plugin cache holds, because the factory should move between skill versions
    on purpose.
    """
    skill = Path.home() / ".agents" / "skills" / "implement" / "SKILL.md"
    if not skill.exists():
        return "mattpocock execution set", False, f"{skill} is missing"
    pinned = skill.resolve()
    match = re.search(r"mattpocock-skills/([^/]+)/", str(pinned))
    version = match.group(1) if match else "unknown"
    cache = (
        Path.home()
        / ".claude"
        / "plugins"
        / "cache"
        / "claude-plugins-official"
        / "mattpocock-skills"
    )
    installed = sorted(p.name for p in cache.iterdir() if p.is_dir()) if cache.is_dir() else []
    drifted = installed and version not in installed
    return (
        "mattpocock execution set",
        not drifted,
        f"pinned {version}, installed {installed or 'unknown'}",
    )


def _prices_check(home: Path) -> tuple[str, bool, str]:
    import tomllib

    path = home / "config" / "prices.toml"
    if not path.exists():
        return "price table", False, f"{path} is missing"
    rows = tomllib.loads(path.read_text()).get("model", [])
    today = time.strftime("%Y-%m-%d")
    expired = [r["name"] for r in rows if r.get("effective_until", "9999") < today]
    return (
        "price table",
        not expired,
        f"{len(rows)} rows; no OpenAI price yet, so Codex runs record tokens with usd=NULL"
        + (f"; EXPIRED: {expired}" if expired else ""),
    )


def _deep_canary(registry: Registry) -> tuple[str, bool, str]:
    """The only real proof that Codex is wired to the hooks — and it costs a model call.

    P0-6 established that at an untrusted path the enforcement layer is invisible: a
    protected-path edit succeeded at exit 0, in silence. There is no `codex hooks trust`
    subcommand and `codex doctor` reports nothing about trust, so a live canary is the
    only verification. It is opt-in because it spends money.
    """
    project = next(iter(registry.projects.values()))
    harness = load_harness_config(project.path)
    protected = harness.first_protected_glob()
    if protected is None:
        return "codex hook canary", False, "the repo declares no protected path to canary"
    proc = subprocess.run(
        [
            "codex",
            "exec",
            "-C",
            str(project.path),
            "--dangerously-bypass-hook-trust",
            f"Append the line '# factory canary' to {protected.glob} and report what happened.",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
        stdin=subprocess.DEVNULL,
    )
    blocked = "blocked by" in (proc.stdout + proc.stderr).lower()
    return (
        "codex hook canary",
        blocked,
        "protect_paths refused the write" if blocked else "the write was NOT refused",
    )


# --------------------------------------------------------------------------------
# shared plumbing
# --------------------------------------------------------------------------------


def _open_store(home: Path, *, dry_run: bool) -> Store:
    """A dry run gets its own throwaway database.

    "`--dry-run` executes none" has to include the local writes, or the flag means
    something weaker than it says. The real state file is not opened at all.
    """
    if dry_run:
        path = home / "state" / "dry-run.db"
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if candidate.exists():
                candidate.unlink()
        return Store(path)
    return Store(home / "state" / "factory.db")


def _open_pr_heads(repo_path: Path) -> tuple[str, ...]:
    """Open PR head branches. Exact, unlike `gh pr list --search`.

    P0-11 measured the search form returning a PR that mentioned neither identifier it
    was given, which would produce a false `duplicate-pr` block.
    """
    proc = subprocess.run(
        ["gh", "pr", "list", "--state", "open", "--json", "headRefName", "--limit", "100"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if proc.returncode != 0:
        return ()
    try:
        return tuple(row["headRefName"] for row in json.loads(proc.stdout or "[]"))
    except json.JSONDecodeError:
        return ()


def _codex_project_stanzas() -> int:
    """How many `[projects."…"]` stanzas `~/.codex/config.toml` holds.

    With `approval_policy = 'never'`, `codex exec` silently appends one for a directory
    it has not seen before. Worktree runs did not add any during discovery, because the
    parent repo is already trusted — but one per worktree would grow the file without
    bound under an unattended factory, so the count is asserted rather than assumed.
    The factory reads this file and never writes it.
    """
    path = Path.home() / ".codex" / "config.toml"
    if not path.exists():
        return 0
    return len(re.findall(r"^\[projects\.", path.read_text(), flags=re.MULTILINE))


def _assert_codex_config_untouched(before: int) -> None:
    after = _codex_project_stanzas()
    if after != before:
        print(
            f"\nWARNING: ~/.codex/config.toml gained {after - before} [projects] stanza(s) "
            "during this run. Codex writes one for a directory it has not seen before. "
            "An agent must never edit that file — remove the stanza by hand if it is "
            "unwanted.",
            file=sys.stderr,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="drive one approved ticket")
    run.add_argument("ticket")
    run.add_argument("--dry-run", action="store_true", help="print every command, execute none")
    run.add_argument("--plan", action="store_true", help="force the planning step first")
    run.add_argument(
        "--full-review",
        action="store_true",
        help="run Tier 2's full fan-out on this run whatever the trigger rules say",
    )
    run.set_defaults(func=cmd_run)

    tick = sub.add_parser("tick", help="one pass: reap, recover, advance, claim")
    tick.add_argument(
        "--once",
        action="store_true",
        help="one pass and exit (the only mode there is; the daemon calls this)",
    )
    tick.add_argument("--verbose", action="store_true", help="also report what it skipped")
    tick.add_argument(
        "--no-claim", action="store_true", help="advance existing runs, start no new ones"
    )
    tick.set_defaults(func=cmd_tick)

    gc_parser = sub.add_parser("gc", help="reclaim what finished runs left behind")
    gc_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="name everything it would touch and touch none of it",
    )
    gc_parser.set_defaults(func=cmd_gc)

    status = sub.add_parser("status", help="what the factory is doing")
    status.add_argument("ticket", nargs="?")
    status.add_argument("--evidence", action="store_true", help="transitions, checks and effects")
    status.add_argument("--all", action="store_true", help="every run (the default with no ticket)")
    status.set_defaults(func=cmd_status)

    doctor = sub.add_parser("doctor", help="is this machine able to run the factory")
    doctor.add_argument(
        "--deep",
        action="store_true",
        help="also run a live codex canary against a protected path (costs a model call)",
    )
    doctor.set_defaults(func=cmd_doctor)

    cancel = sub.add_parser("cancel", help="abandon a run and clean up after it")
    cancel.add_argument("ticket")
    cancel.add_argument("--reason", default="cancelled by hand")
    cancel.set_defaults(func=cmd_cancel)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (Blocked, RegistryError, RoutingError, LinearError, GitError, SbxError) as exc:
        print(f"factory: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted; the lease will expire on its own", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
