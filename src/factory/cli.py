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

from factory import artifacts, gc, machine, policy, recovery, repo
from factory.agent.codex import CodexAdapter
from factory.console import views as console_views
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
from factory.steps import Context, advance, factory_dir_for
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

#: Where the forward dispatch takes over from the poller. `implementing`, `planning`,
#: `verifying` and `reviewing` are absent on purpose: those four are
#: `steps/reap.py`'s, because the run in them is not waiting for the factory to do
#: something, it is waiting for a detached run (a codex agent or the gate report) that
#: a previous process started — or, for verify/review just entered by the previous
#: state's collect, it is waiting for this tick to call `start`. `pr_ready` stays
#: forward: `deliver` is a short host-side push + PR, synchronous by design.
_FORWARD: dict[State, str] = {
    State.APPROVED: "claim",
    State.CLAIMED: "context",
    State.CONTEXT_LOADED: "sandbox",
    State.SANDBOX_CREATING: "sandbox",
    State.SANDBOX_READY: "worktree",
    State.WORKTREE_READY: "agent",
    State.PR_READY: "deliver",
}


def cmd_tick(args: argparse.Namespace) -> int:
    """One pass over everything the factory owns. Safe to run at any instant.

    A tick reaps what a previous tick started, recovers what died, moves every run that
    can move, and then — and only then — looks for new work. That order is the whole
    design: a machine that claimed first would keep starting runs it had not yet noticed
    were broken.

    Nothing here is held in memory between calls. `launchd` runs this every 60 s in a
    fresh process, so every fact it acts on is read from SQLite or from the filesystem
    at the top of the pass. `factory daemon` is the same pass in a foreground loop, for a
    machine without launchd; both call `_tick_pass`.
    """
    return _tick_pass(factory_home(), claim=not args.no_claim, verbose=args.verbose)


def _tick_pass(home: Path, *, claim: bool, verbose: bool) -> int:
    """One tick: load config hot (re-read every call), check the DB and the disk, run the
    pass. Separated from `cmd_tick` so `cmd_daemon` can loop it and a test can call it
    without an argparse namespace.

    F25 lives here: an invalid `models.toml` refuses the pass and names the rule. It never
    falls back to a default, because a factory silently running every role on one model
    looks exactly like one routing correctly.
    """
    registry = load_registry(home / "config" / "projects.toml")
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
    lines = tick_once(home, registry, routing, store, linear, claim=room and claim, verbose=verbose)
    for line in lines:
        print(line)
    if not lines:
        print("nothing to do")
    return 0


# --------------------------------------------------------------------------------
# factory daemon
# --------------------------------------------------------------------------------


#: The launchd plist's `StartInterval`. The daemon default matches it so the foreground
#: loop and the timer behave the same. §4.2: the tick that asks is never the tick that
#: started the run, so a 60 s cadence is a poll, not a wait.
DAEMON_INTERVAL_SECONDS = 60


def cmd_daemon(args: argparse.Namespace) -> int:
    """`factory tick --once` in a foreground loop, for a machine without launchd.

    Each pass reloads `projects.toml` and `models.toml` hot, so a config edit takes effect
    on the next tick without a restart — the same property the launchd timer has, because
    it starts a fresh process per pass. A single tick that fails (a transient Linear
    outage, a bad routing edit) is logged and skipped; the daemon keeps going, because a
    poller that dies on one bad pass is a poller that stops until a human notices. Ctrl-C
    stops it; the lease on any in-flight run expires on its own.
    """
    home = factory_home()
    # Refuse to start at all on a routing table that would silently misroute every run,
    # rather than discovering it on the first tick. A mid-run break is still caught per-tick.
    load_routing(home / "config" / "models.toml")
    interval = args.interval
    print(f"factory daemon: ticking every {interval}s. Ctrl-C to stop.", flush=True)
    while True:
        try:
            _tick_pass(home, claim=not args.no_claim, verbose=args.verbose)
        except KeyboardInterrupt:
            print("\nfactory daemon: stopped; in-flight leases expire on their own.")
            return 0
        except Exception as exc:  # one bad tick must not kill the daemon
            print(f"tick failed (will retry next interval): {exc}", file=sys.stderr)
        time.sleep(interval)


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
    for run in store.runs_in_states([*reap_step.DETACHED_STATES, State.RESUMABLE]):
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
        if before in reap_step.DETACHED_STATES:
            verdict = reap_step.reap(ctx)
            steps.append(f"{before}:{verdict.outcome}")
            if verdict.outcome in (reap_step.Outcome.RUNNING, reap_step.Outcome.ORPHANED):
                break
            if verdict.outcome is reap_step.Outcome.START_NEEDED:
                # Verify/review entered by the previous state's collect, with no
                # attempt spawned yet. Start the detached run now and break: the next
                # tick reaps it. `start` does not advance (the run is already in this
                # state), so without this branch the loop would break without spawning.
                _start_detached(ctx, before)
                break
            if verdict.outcome is reap_step.Outcome.NEXT_STEP:
                _start_next_after_gate_fail(ctx)
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


def _start_detached(ctx: Context, state: State) -> None:
    """Spawn a detached run for a verify/review state that reap found unstarted.

    The only detached states that reach `START_NEEDED` (no attempt row) are
    `verifying` and `reviewing` — `planning`/`implementing` orphan instead, because
    their `start` is the sole entry point and a no-row row means it crashed before
    spawning. verify/review, by contrast, are entered by the previous state's
    `collect`, so a no-row row is the normal first tick, not a crash.
    """
    if state is State.VERIFYING:
        verify_step.start(ctx)
    elif state is State.REVIEWING:
        review_step.start(ctx)


def _start_next_after_gate_fail(ctx: Context) -> None:
    """The implement attempt that follows a gate-fail loop-back, chosen by the ladder.

    The loop-back (`verifying -> implementing`) is a normal transition, not a recovery, so
    without this the tick would start implement attempts unbounded — exactly the gate-fail
    loop the daemon must not run unattended. By the time the run reaches `implementing`
    here it is on rung 2 (a fresh implement) or rung 4 (the budget spent); rung 3 is
    decided earlier, in `verify.collect`, where the `verifying -> planning` edge exists.
    `recovery.next_attempt_disposition` routes the decision through `decide`, the same
    authority `resume_run` uses, so the two paths cannot disagree about when a rung
    exhausts.

    `FAIL` has no `implementing -> failed` edge, so the run parks at `resumable` and the
    next tick's `resume_run` records `resumable -> failed` under the same ladder — the
    same shape a timed-out implement attempt takes.
    """
    verdict = recovery.next_attempt_disposition(ctx)
    if verdict.disposition is recovery.Disposition.FAIL:
        advance(ctx, State.RESUMABLE, rule=verdict.reason, detail=f"rung {verdict.rung}")
        return
    # RESUME or RESTART: a fresh implement against the same worktree, rung 2.
    session = (
        ctx.store.session_id(ctx.run.id, ctx.run.attempt, State.IMPLEMENTING)
        if verdict.disposition is recovery.Disposition.RESUME
        else None
    )
    implement_step.start(
        ctx, resume_session=session, continuation=recovery.continuation_prompt(ctx)
    )


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
    """`factory status` — the §18.5 board (View 1) and run detail (View 3), terminal forms.

    With no ticket: the runs board. With `<TICKET> --evidence`: the full run detail —
    transition timeline, gate report, review findings, artifacts. The CLI is built first
    (§18.5); `factory serve` renders the same data through `console.views`."""
    home = factory_home()
    registry = load_registry(home / "config" / "projects.toml")
    routing = load_routing(home / "config" / "models.toml")
    store = _open_store(home, dry_run=False)

    if args.ticket:
        ticket = args.ticket.upper()
        run = store.live_run_for_ticket(ticket)
        if run is None:
            print(f"no run for {ticket}")
            return 1
        if args.evidence:
            _print_run_detail(console_views.run_detail(home, registry, routing, store, run))
        else:
            _print_run_detail(console_views.run_detail(home, registry, routing, store, run))
        return 0

    _print_runs_board(console_views.runs_board(home, registry, routing, store))
    return 0


def _print_runs_board(rows: list[console_views.RunRow]) -> None:
    if not rows:
        print("no runs")
        return
    for r in rows:
        badge = f" [{r.badge}]" if r.badge else ""
        ctx = _fmt_context(r.context_pct, r.context_reason)
        if r.spend_usd is not None:
            spend = f"${r.spend_usd:.2f}/${r.spend_ceiling:.0f}"
        else:
            spend = f"-/${r.spend_ceiling:.0f}"
        live = _fmt_duration(r.heartbeat_age) if r.heartbeat_age is not None else "-"
        print(
            f"{r.ticket:<8} {r.project:<16} {r.state}{badge}  att {r.attempt}·{r.rung}  "
            f"ctx {ctx}  tok {r.tokens_in}/{r.tokens_out}  {spend}  live {live}"
        )
        if r.blocked_reason:
            print(f"        blocked: {r.blocked_reason}")
        elif r.context_reason and r.context_pct is None:
            print(f"        ctx: {r.context_reason}")
        if r.activity:
            print(f"        · {r.activity}")


def _print_run_detail(detail: console_views.RunDetail) -> None:
    r = detail.row
    print(f"{r.ticket} ({r.project}) — {r.state}  attempt {r.attempt}·{r.rung}")
    if detail.pr_url:
        print(f"  PR: {detail.pr_url}")
    if detail.blocked_reason:
        print(f"  blocked: {detail.blocked_reason}")
    ctx = _fmt_context(r.context_pct, r.context_reason)
    if r.spend_usd is not None:
        spend = f"${r.spend_usd:.2f}/${r.spend_ceiling:.0f}"
    else:
        spend = f"-/${r.spend_ceiling:.0f}"
    print(
        f"  ctx {ctx}  tok {r.tokens_in}/{r.tokens_out} (cached {r.tokens_cached})  spend {spend}"
    )
    print("\n  transitions:")
    for t in detail.transitions:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t.at))
        rule = f"  [{t.rule}]" if t.rule else ""
        print(f"    {stamp}  {t.from_state} -> {t.to_state}  ({t.actor}){rule}")
    if detail.gates:
        print(f"\n  gate report — verdict: {detail.gate_verdict}")
        for g in detail.gates:
            caveat = f"  caveat: {g.caveat}" if g.caveat else ""
            print(f"    {g.status:<14} {g.name}{caveat}")
    if detail.review_tier2 is not None:
        # Printed even with no findings: "Tier 2 ran and found nothing" and "Tier 2 never
        # ran" are different facts, and §13.2 is emphatic that a PR body must not let the
        # second be read as the first. The same rule applies to the operator's view.
        print(f"\n  review — tier2: {detail.review_tier2}")
        if not detail.review_findings:
            print("    no findings")
        for f in detail.review_findings:
            loc = f"{f.file}:{f.line}" if f.file and f.line else (f.file or "")
            print(f"    [{f.severity or '?'}] {loc}  {f.summary}")
    if detail.artifacts:
        print("\n  artifacts:")
        for a in detail.artifacts:
            sha = a.sha256[:12] if a.sha256 else "-"
            print(f"    {sha}  {a.name}")
    if detail.events_path:
        print(f"\n  events: {detail.events_path}")
        print(f"  tail: factory logs {r.ticket} --follow")


def _fmt_context(pct: float | None, reason: str | None) -> str:
    if pct is not None:
        return f"{pct * 100:.0f}%"
    return f"— ({reason})" if reason else "—"


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60}m"


# --------------------------------------------------------------------------------
# factory logs — the live tail §18.5 names; the runbook pointed at the file until this
# existed.
# --------------------------------------------------------------------------------


def cmd_logs(args: argparse.Namespace) -> int:
    """`factory logs <TICKET> [--follow]` — tail the active attempt's `events.jsonl`.

    `--follow` blocks on the file growing, polling with a stdlib sleep (no `tail -f`
    subprocess). The active attempt is the run's current `attempt`; a resume that did not
    increment keeps reading the same directory the implementer wrote."""
    home = factory_home()
    registry = load_registry(home / "config" / "projects.toml")
    store = _open_store(home, dry_run=False)
    run = store.live_run_for_ticket(args.ticket.upper())
    if run is None:
        print(f"no run for {args.ticket.upper()}")
        return 1
    project = registry.projects.get(run.project)
    if project is None:
        print(f"no project for run {run.id}")
        return 1
    try:
        events_path = (
            factory_dir_for(home, project, run) / "run" / str(run.attempt) / "events.jsonl"
        )
    except Blocked as exc:
        print(f"factory: {exc}")
        return 2
    if not events_path.exists():
        print(f"no event stream at {events_path}")
        return 1
    return _tail(events_path, follow=args.follow)


def _tail(path: Path, *, follow: bool) -> int:
    """Print new lines from `path` as they appear. Stdlib only: a read offset and a poll."""
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            sys.stdout.write(line)
        sys.stdout.flush()
        if not follow:
            return 0
        while True:
            line = handle.readline()
            if line:
                sys.stdout.write(line)
                sys.stdout.flush()
                continue
            time.sleep(1)


# --------------------------------------------------------------------------------
# factory runtimes — View 2: sandboxes joined to runs
# --------------------------------------------------------------------------------


def cmd_runtimes(args: argparse.Namespace) -> int:
    """`factory runtimes` — `sbx ls --json` joined to the runs using each sandbox.

    A sandbox not matching `^factory-(build|review)-` is operator-owned (`codex-*`); shown
    greyed (here, prefixed `*`) and offered no control (§18.5, F26)."""
    home = factory_home()
    store = _open_store(home, dry_run=False)
    sandboxes = _sbx_ls_json()
    rows = console_views.runtimes(sandboxes, store)
    if not rows:
        print("no sandboxes (is `sbx` installed and authenticated?)")
        return 0
    for r in rows:
        marker = "*" if r.operator_owned else " "
        ports = ",".join(r.published_ports) if r.published_ports else "-"
        using = ", ".join(r.runs_using) if r.runs_using else "-"
        print(f"{marker} {r.name:<32} {r.state:<10} ports {ports}  using {using}")
        if r.last_denial:
            print(f"    last denial: {r.last_denial}")
    return 0


def _sbx_ls_json() -> list[dict[str, object]]:
    """`sbx ls --json`, parsed into the sandbox list.

    The document is an **object** with a `sandboxes` array, not a bare array — the shape
    `SbxAdapter.git_daemon_url` already reads, and the one measured against v0.38.0. A
    reader that expected a list would show an empty runtimes view on a healthy machine and
    look like "no sandboxes" rather than "parsed the wrong thing".

    Returns `[]` when `sbx` is missing or refuses (it needs a Docker login), so the console
    degrades to an empty view instead of failing the page.
    """
    if not sbx_available():
        return []
    proc = subprocess.run(
        ["sbx", "ls", "--json"], capture_output=True, text=True, check=False, timeout=30
    )
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        listing = data.get("sandboxes", [])
        return [entry for entry in listing if isinstance(entry, dict)]
    return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []


# --------------------------------------------------------------------------------
# factory config — View 4 (read form): models.toml roles and budget
# --------------------------------------------------------------------------------


def cmd_config(args: argparse.Namespace) -> int:
    """`factory config models` — render `models.toml`'s roles, efforts and budget.

    Read-only here; the editable form lives in `factory serve` (View 4), which validates a
    change through `routing` before it is written so a config that puts the reviewer on the
    builder's model is rejected in the form (§18.5). `projects.toml` is read-only in both:
    its template/mount/MCP set is a sandbox spec fixed at creation."""
    home = factory_home()
    routing = load_routing(home / "config" / "models.toml")
    registry = load_registry(home / "config" / "projects.toml")
    view = console_views.config_view(routing, registry)
    print("roles:")
    for role in view.roles:
        print(f"  {role.name:<12} {role.model}  effort={role.effort}")
    print("\nbudget:")
    print(f"  per-run ceiling ${view.usd_per_run:.2f}  warn at ${view.usd_warn_at:.2f}")
    print("\nprojects (read-only — sandbox spec fixed at creation):")
    for name in view.projects_read_only:
        print(f"  {name}")
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
    see `_release_local_debris`. The work is in `_cancel_run`, shared with the console's
    Cancel control (§18.5 View 5)."""
    home = factory_home()
    registry = load_registry(home / "config" / "projects.toml")
    store = _open_store(home, dry_run=False)
    ticket = args.ticket.upper()
    run = store.run_by_ticket(ticket)
    if run is None:
        print(f"no run for {ticket}")
        return 1

    for line in _cancel_run(home, registry, store, LinearClient(), run, args.reason):
        print(line)
    return 0


def _cancel_run(
    home: Path,
    registry: Registry,
    store: Store,
    linear: LinearClient,
    run: Run,
    reason: str,
) -> list[str]:
    """The rollback core, shared by `cmd_cancel` and the console's Cancel control.

    Archives the attempt directories, removes the worktree and the unpushed branch, records
    `-> cancelled` under `actor="human"` (the §18.5 control contract), stops the build
    sandbox, and restores the Linear tracker. Returns the lines it would print. A pushed
    branch is never deleted; a Linear outage does not fail the rollback."""
    ticket = run.linear_id
    project = registry.projects[run.project]
    paths = _worktree_paths(project, registry, run, ticket)
    lines: list[str] = []

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
                    lines.append(f"archived {directory.name} to {kept}")

    lines += _release_local_debris(project, run, ticket, paths)

    if machine.can(run.state, State.CANCELLED):
        store.record_transition(
            run.id,
            from_state=run.state,
            to_state=State.CANCELLED,
            actor="human",
            rule="abandon-is-james",
            detail=reason,
        )
    store.release_lease(run.id)
    SbxAdapter().stop(project.build_sandbox)

    # Last, and never fatal: the local cleanup above has already happened, and a Linear
    # outage must not turn a completed rollback into a failed command.
    try:
        lines += _restore_tracker_for_rerun(linear, ticket)
    except LinearError as exc:
        lines.append(
            f"could not restore {ticket} in Linear ({exc}); move it back to {TODO} by hand"
        )

    lines.append(f"{ticket} cancelled; sandbox {project.build_sandbox} stopped")
    return lines


# --------------------------------------------------------------------------------
# factory suspend
# --------------------------------------------------------------------------------


def cmd_suspend(args: argparse.Namespace) -> int:
    """§16.3b — park a run. The machinery (signal, wait for `exit`, stop the sandbox if
    idle, record `-> suspended`, announce) lives in `recovery.suspend` so it runs against
    the fakes in the tests; this is the thin shell that owns the lease and the real
    adapters."""
    home = factory_home()
    registry = load_registry(home / "config" / "projects.toml")
    routing = load_routing(home / "config" / "models.toml")
    store = _open_store(home, dry_run=False)
    ticket = args.ticket.upper()
    run = store.run_by_ticket(ticket)
    if run is None:
        print(f"no run for {ticket}")
        return 1
    if run.state in machine.TERMINAL:
        print(f"{ticket} is at {run.state}; nothing to suspend")
        return 1
    if not store.acquire_lease(run.id, ttl_seconds=LEASE_TTL_SECONDS):
        print(
            f"\n{ticket} is leased by another process ({run.lease_owner}); wait for it to "
            "finish, or use `factory cancel` to abandon it."
        )
        return 1

    ctx = _context_for(home, registry, routing, store, LinearClient(), run)
    origin = recovery.suspend(ctx, reason=args.reason)
    store.release_lease(run.id)

    print(
        f"{ticket} suspended from {origin}; worktree, branch and session kept. "
        f"`factory resume {ticket}` resumes it."
    )
    return 0


# --------------------------------------------------------------------------------
# factory resume
# --------------------------------------------------------------------------------


def cmd_resume(args: argparse.Namespace) -> int:
    """§16.3b — resume a parked run. Re-enters the state it left (or `--from`), then drives
    forward through the synchronous states. An agent state starts the agent and hands the
    rest to the tick, the same way `resume_run` does; `verifying`/`reviewing` run through
    to `awaiting_human`/`pr_ready` in this command. `--authorise` re-authorises a `failed`
    run's spend (§16.4)."""
    home = factory_home()
    registry = load_registry(home / "config" / "projects.toml")
    routing = load_routing(home / "config" / "models.toml")
    store = _open_store(home, dry_run=False)
    ticket = args.ticket.upper()
    run = store.run_by_ticket(ticket)
    if run is None:
        print(f"no run for {ticket}")
        return 1
    if not store.acquire_lease(run.id, ttl_seconds=LEASE_TTL_SECONDS):
        print(f"\n{ticket} is leased by another process ({run.lease_owner}); nothing to do.")
        return 0

    if run.state is State.FAILED and not args.authorise:
        store.release_lease(run.id)
        print(f"{ticket} is `failed`; pass --authorise to re-authorise spend (§16.4).")
        return 2

    linear = LinearClient()
    ctx = _context_for(home, registry, routing, store, linear, run)

    try:
        recovery.resume(ctx, from_state=args.from_state, authorise=args.authorise)
        _drive_from_here(ctx)
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
        store.release_lease(ctx.run.id)

    _report(ctx)
    if ctx.run.pr_url:
        print(f"\nReview ran and a draft pull request is open: {ctx.run.pr_url}\n")
    elif ctx.state is State.AWAITING_HUMAN:
        print(f"\nThe run stopped at `{ctx.state}` for a human to look.")
    elif ctx.state in reap_step.DETACHED_STATES:
        print(f"\nA detached run is in progress at `{ctx.state}`; `factory tick` collects it.")
    else:
        print(f"\nThe run came to rest at `{ctx.state}`.")
    return 0


# --------------------------------------------------------------------------------
# §18.5 View 5 — operator controls, shared with `factory serve`
# --------------------------------------------------------------------------------


#: The §18.5 controls, by the URL slug the console POSTs. There is deliberately no `merge`:
#: §18.5 names "no Merge button" — merging happens on GitHub, by James — and its absence is
#: asserted by a test that greps the templates.
_CONTROLS: set[str] = {"suspend", "resume", "resume-planning", "cancel", "retry"}


def dispatch_control(
    action: str,
    home: Path,
    registry: Registry,
    routing: Routing,
    store: Store,
    linear: LinearClient,
    run: Run,
) -> tuple[int, str]:
    """One §18.5 control. Acquires the lease, builds a real `Context`, dispatches through
    `recovery`/`_cancel_run` (every path writes an `actor="human"` transition — the §18.5
    contract), and releases the lease. Returns `(exit_code, message)`.

    F26: the sandbox a control touches is the run's own `factory-build-*`/`factory-review-*`
    sandbox, never a `codex-*` one. `policy.assert_factory_sandbox` guards the build sandbox
    before suspend/cancel stop it, so a misconfigured project pointing at an operator sandbox
    is refused here rather than acted on. The run controls do not take a sandbox argument;
    the run's project determines it, and the assertion is the boundary.
    """
    if action not in _CONTROLS:
        return 1, f"unknown control {action!r}; one of {sorted(_CONTROLS)}"

    project = registry.projects.get(run.project)
    if project is not None:
        # F26 — refuse before acting if the run's sandbox is not a factory sandbox.
        policy.assert_factory_sandbox(project.build_sandbox)

    if not store.acquire_lease(run.id, ttl_seconds=LEASE_TTL_SECONDS):
        return 1, f"{run.linear_id} is leased by another process ({run.lease_owner})"

    ctx = _context_for(home, registry, routing, store, linear, run)
    try:
        if action == "suspend":
            if run.state in machine.TERMINAL:
                return 1, f"{run.linear_id} is at {run.state}; nothing to suspend"
            origin = recovery.suspend(ctx, reason="suspended via console")
            return 0, f"{run.linear_id} suspended from {origin}"
        if action == "cancel":
            lines = _cancel_run(home, registry, store, linear, run, "cancelled via console")
            return 0, "\n".join(lines)
        if action == "resume-planning":
            recovery.resume(ctx, from_state="planning")
        elif action == "retry":
            if run.state is not State.RESUMABLE:
                return 1, f"{run.linear_id} is {run.state}; retry is for a resumable run"
            recovery.resume_run(ctx, skip_backoff=True)
        else:  # resume
            recovery.resume(ctx)
        _drive_from_here(ctx)
    except Blocked as exc:
        _block(ctx, exc.reason, exc.detail)
        return 2, f"{run.linear_id} blocked: {exc.reason} — {exc.detail}"
    except Resumable as exc:
        ctx.store.record_transition(
            ctx.run.id,
            from_state=ctx.state,
            to_state=State.RESUMABLE,
            actor="auto",
            rule=exc.reason,
            detail=exc.detail[:2000],
        )
        return 3, f"{run.linear_id} resumable: {exc.reason} — {exc.detail}"
    finally:
        store.release_lease(run.id)

    if ctx.run.pr_url:
        return 0, f"{run.linear_id} drove to {ctx.state}; draft PR: {ctx.run.pr_url}"
    return 0, f"{run.linear_id} drove to {ctx.state}"


# --------------------------------------------------------------------------------
# factory serve — the §18.5 operator console
# --------------------------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    """`factory serve` — the §18.5 console, bound to loopback.

    A read-mostly local web view of what the factory is doing and what it is costing.
    It is not an authenticated multi-user service and it holds no credential of its own:
    it reads the same SQLite file the daemon writes and shells out to the same `sbx`.
    Binding it to anything but loopback is refused here rather than left to a flag —
    §18.5 makes off-machine access a Phase 7 decision with its own auth story.

    The app is built lazily so `factory` stays importable (and every other command stays
    usable) on a machine where the web dependencies are not installed.
    """
    host = args.host
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"factory serve refuses to bind {host!r}: the console is loopback-only "
            "(§18.5). Off-machine access is a Phase 7 decision with its own auth story.",
            file=sys.stderr,
        )
        return 2

    try:
        import uvicorn

        from factory.console.app import create_app
    except ImportError as exc:  # pragma: no cover - depends on the install
        print(
            f"factory serve needs the console dependencies ({exc}). "
            "Run `uv sync` and try again.",
            file=sys.stderr,
        )
        return 2

    home = factory_home()
    # Fail fast on a routing table the console would render wrong, the same way the
    # daemon refuses to start on one rather than discovering it per request.
    load_routing(home / "config" / "models.toml")

    print(f"factory console: http://{host}:{args.port}  (loopback only; Ctrl-C to stop)")
    uvicorn.run(create_app(home), host=host, port=args.port, log_level="warning")
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

    daemon = sub.add_parser(
        "daemon",
        help="tick in a foreground loop (the launchd plist calls `tick --once` instead)",
    )
    daemon.add_argument(
        "--interval",
        type=int,
        default=DAEMON_INTERVAL_SECONDS,
        help=f"seconds between passes (default {DAEMON_INTERVAL_SECONDS})",
    )
    daemon.add_argument("--verbose", action="store_true", help="also report what it skipped")
    daemon.add_argument(
        "--no-claim", action="store_true", help="advance existing runs, start no new ones"
    )
    daemon.set_defaults(func=cmd_daemon)

    gc_parser = sub.add_parser("gc", help="reclaim what finished runs left behind")
    gc_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="name everything it would touch and touch none of it",
    )
    gc_parser.set_defaults(func=cmd_gc)

    status = sub.add_parser("status", help="what the factory is doing")
    status.add_argument("ticket", nargs="?")
    status.add_argument(
        "--evidence",
        action="store_true",
        help="full run detail: transitions, gates, review, artifacts",
    )
    status.add_argument("--all", action="store_true", help="every run (the default with no ticket)")
    status.set_defaults(func=cmd_status)

    logs = sub.add_parser("logs", help="tail a run's event stream")
    logs.add_argument("ticket")
    logs.add_argument("--follow", action="store_true", help="keep tailing as the stream grows")
    logs.set_defaults(func=cmd_logs)

    runtimes_p = sub.add_parser("runtimes", help="sandboxes joined to the runs using them")
    runtimes_p.set_defaults(func=cmd_runtimes)

    config = sub.add_parser("config", help="render models.toml / projects.toml")
    config_sub = config.add_subparsers(dest="config_command", required=False)
    models = config_sub.add_parser("models", help="roles, efforts and budget (read form)")
    models.set_defaults(func=cmd_config)
    config.set_defaults(func=cmd_config)

    serve = sub.add_parser("serve", help="the §18.5 operator console (loopback-only)")
    serve.add_argument("--port", type=int, default=7717)
    serve.add_argument("--host", default="127.0.0.1", help="loopback only (§18.5)")
    serve.set_defaults(func=cmd_serve)

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

    suspend = sub.add_parser("suspend", help="park a run; keep its worktree, branch and session")
    suspend.add_argument("ticket")
    suspend.add_argument("--reason", default="suspended by hand")
    suspend.set_defaults(func=cmd_suspend)

    resume = sub.add_parser("resume", help="resume a parked (suspended/blocked/resumable) run")
    resume.add_argument("ticket")
    resume.add_argument(
        "--from",
        dest="from_state",
        help="resume into a specific state: implementing, planning, verifying, reviewing",
    )
    resume.add_argument(
        "--authorise",
        action="store_true",
        help="re-authorise spend for a `failed` run (§16.4: `failed -> resumable`)",
    )
    resume.set_defaults(func=cmd_resume)

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
