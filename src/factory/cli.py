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

from factory import artifacts, doctor, driver, gc, machine, policy, recovery, repo
from factory.agent.codex import CodexAdapter
from factory.console import views as console_views
from factory.delivery import github
from factory.harness import load_harness_config
from factory.intake.linear import (
    Condition,
    Issue,
    LinearClient,
    LinearError,
    RepoFacts,
    eligibility_verdict,
    evaluate_eligibility,
)
from factory.machine import Blocked, Resumable, State
from factory.registry import Project, Registry, RegistryError, load_registry
from factory.repo import GitError
from factory.routing import Routing, RoutingError, load_routing
from factory.sandbox.sbx import SbxAdapter, SbxError, sbx_available
from factory.steps import Context, advance, factory_dir_for, record_stop, redphase
from factory.steps import block as block_step
from factory.steps import claim as claim_step
from factory.steps import clone as clone_step
from factory.steps import complete as complete_step
from factory.steps import reap as reap_step
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

    if args.check:
        # §7.1's verdict, and nothing else. This is what `--dry-run` promised and did not
        # deliver: everything above this line is a read, so the answer is the real one,
        # produced by the same `assess` the tick acts on rather than by a simulation of it.
        # The exit codes mirror `factory run` exactly, or it is a second opinion rather
        # than a preview.
        print("\nNothing was written: --check stops before the run row.")
        if eligible:
            print(f"{ticket} is eligible; `factory run {ticket}` would start it.")
            return 0
        if block_reason is None:
            print(f"{ticket} is not eligible: {failures}")
            return 1
        print(f"{ticket} would be blocked: {block_reason} ({failures})")
        return 2

    if not eligible:
        if block_reason is None:
            print(f"\n{ticket} is not eligible and no run was created: {failures}")
            return 1
        print(f"\n{ticket} is blocked: {block_reason} ({failures})")

    store = _open_store(home)
    run = store.insert_run(
        linear_id=ticket,
        project=project.name,
        team=issue.team_key,
        full_review=args.full_review,
        force_plan=args.plan,
    )
    if args.full_review:
        print("  NOTE  --full-review: Tier 2 runs whatever the §15.2 trigger rules decide")

    if not store.acquire_lease(run.id, ttl_seconds=LEASE_TTL_SECONDS):
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

    # The same loop the daemon runs, with `follow` on: `factory run` stays with the run
    # instead of coming back next tick. It is not a second execution model — which is
    # what buys it `reap`'s orphan detection and kill-grace, neither of which the old
    # foreground chain had (the runbook's answer was `nohup`).
    try:
        result = _drive_foreground(ctx, follow=not args.no_follow)
    except Blocked as exc:
        _block(ctx, exc.reason, exc.detail)
        _report(ctx)
        return 2
    finally:
        _assert_codex_config_untouched(codex_stanzas_before)
        ctx.store.release_lease(ctx.run.id)

    if result.outcome is driver.Outcome.STOPPED and ctx.state is State.BLOCKED:
        print(f"\nBLOCKED: {result.reason}")
        _report(ctx)
        return 2
    if result.outcome is driver.Outcome.STOPPED and ctx.state in (
        State.RESUMABLE,
        State.FAILED,
    ):
        print(f"\n{ticket} is {ctx.state}: {result.reason or result.detail}")
        _report(ctx)
        return 3

    _report(ctx)
    ctx.refresh()
    if ctx.run.pr_url:
        print(
            f"\nReview ran and a pull request is open for review: {ctx.run.pr_url}\n"
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


def _drive_foreground(
    ctx: Context, *, follow: bool, poll: float = driver.POLL_INTERVAL_SECONDS
) -> driver.Result:
    """`driver.drive`, plus the foreground's answer to an adapter failure.

    `driver` deliberately catches neither `GitError`, `SbxError` nor `LinearError`,
    because the two callers want opposite things from them: a tick reports and leaves the
    run where it was, so a transient Linear outage is a pause rather than a state change
    (F17), while a human at a terminal wants the run stopped and legible. This is the
    second answer, and it is a `Blocked` naming the state that died.

    Before this existed the exception reached `main`'s catch-all: the process exited 2
    while the run row kept whatever state it had, `blocked_reason` empty and the tracker
    never told — a stopped run that looks live to everything that reads state. BAC-4's
    `1effc543d83a459a` sat at `reviewing` that way after the red-phase replay's
    `git apply` failed.
    """
    try:
        return driver.drive(ctx, follow=follow, poll=poll)
    except (GitError, SbxError, LinearError) as exc:
        raise Blocked(f"{ctx.state}-step-failed", str(exc)) from exc


def _block(ctx: Context, reason: str, detail: str) -> None:
    """Print the block a caller is recording itself. `block.record` does the four writes.

    `driver.drive` records every block a *step* raises; this is for the two blocks the
    command line raises on its own — an ineligible ticket at intake, and an adapter
    failure the driver deliberately does not catch.
    """
    print(f"\nBLOCKED: {reason}\n  {detail}")
    block_step.record(ctx, reason, detail)


def _report(ctx: Context) -> None:
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

#: The tick's two sweeps, in the order §16 requires and derived from `machine.ENTRY`
#: rather than listed. Reaping and recovery come first because a machine that claimed
#: first would keep starting runs it had not yet noticed were broken; the forward states
#: come second; new work comes last.
#:
#: `cli._FORWARD` used to be the second list, hand-written, and the first was
#: `reap.DETACHED_STATES + [resumable]` — two lists that had to be exact complements with
#: nothing checking that they were. They are now one partition of one table.
_WATCHED = (machine.Action.REAP, machine.Action.RECOVER)
_WATCHED_STATES: list[State] = [s for s, a in machine.ENTRY.items() if a in _WATCHED]
_FORWARD_STATES: list[State] = [s for s, a in machine.ENTRY.items() if a not in _WATCHED]


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

    store = _open_store(home)
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
    for run in store.runs_in_states(_WATCHED_STATES):
        line = _work_on(store, run, build, verbose=verbose)
        if line:
            lines.append(line)

    for run in store.runs_in_states(_FORWARD_STATES):
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
        result = driver.drive(ctx)
        return f"{run.linear_id:<10} {result.detail}" if result.detail else ""
    except (GitError, SbxError, LinearError, RegistryError) as exc:
        # An adapter failure is not a factory crash and must not end the pass. It is
        # reported and the run is left exactly where it was, so the next tick sees the
        # same state and can try again — which is what makes a transient Linear outage
        # (F17) a pause rather than a state change. `factory run` answers the same
        # exception differently, which is why `driver.drive` catches neither.
        return f"{run.linear_id:<10} adapter error, left in place: {exc}"
    finally:
        store.release_lease(run.id)


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
    store = _open_store(home)

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
    store = _open_store(home)

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

    rows = console_views.runs_board(home, registry, routing, store)
    _print_runs_board(rows, _ready_to_complete(rows, _board_pr_state(registry)))
    return 0


def _board_pr_state(
    registry: Registry,
) -> Callable[[str, str], str | None]:
    """A ``pr_state(project_name, pr_url)`` closure for the board's merge check.

    ``gh pr view <url>`` resolves the repo from the URL, so the cwd only needs to
    be *some* path on this machine for `gh` to find its auth. The project's own
    checkout is the natural one; a project missing from the registry (a run whose
    project row was removed) falls back to the factory home so the row is still
    asked about rather than silently dropped.
    """

    def _state(project_name: str, pr_url: str) -> str | None:
        project = registry.projects.get(project_name)
        cwd = project.path if project is not None else factory_home()
        return github.pr_state(cwd, pr_url)

    return _state


def _ready_to_complete(
    rows: list[console_views.RunRow],
    pr_state: Callable[[str, str], str | None],
) -> set[str]:
    """Tickets at ``awaiting_human`` whose PR James has merged.

    The ``awaiting_human -> completed`` edge is ``merge-is-james``: the factory
    never takes it. But nothing told James *when* to take it, so a run with a
    merged PR sat at ``awaiting_human`` looking like every other parked run. This
    surfaces "ready to complete" without taking the edge — it is a notice, not a
    transition, and `factory complete <TICKET>` is still the human command.

    A `gh` that cannot answer (``None``) is not "merged": a transient `gh` failure
    must not look the same as a PR James has not merged, for the same reason
    `cmd_complete` refuses both.
    """
    ready: set[str] = set()
    for r in rows:
        if (
            r.state == State.AWAITING_HUMAN.value
            and r.pr_url
            and pr_state(r.project, r.pr_url) == "MERGED"
        ):
            ready.add(r.ticket)
    return ready


def _print_runs_board(rows: list[console_views.RunRow], ready: set[str]) -> None:
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
        if r.ticket in ready:
            print(f"        · PR merged — ready to complete: factory complete {r.ticket}")


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
    store = _open_store(home)
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
    store = _open_store(home)
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
    store = _open_store(home)
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
    # The clone's copy of the branch, for a `--clone` project. Before the sandbox is
    # stopped below, because releasing it needs the sandbox running — and the host call
    # above cannot reach it: that branch lives in the VM. See `clone.release_branch`.
    sbx = SbxAdapter()
    lines += clone_step.release_branch(sbx, project, run)

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
    sbx.stop(project.build_sandbox)

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
# factory complete
# --------------------------------------------------------------------------------


def cmd_complete(args: argparse.Namespace) -> int:
    """§5.3 — record that James merged the PR, so `gc` can reclaim the run.

    The one edge out of `awaiting_human` that finishes a run. `merge-is-james` reserves it,
    so this is a human command by construction; what the command adds is the evidence.
    `gh pr view --json state` must say `MERGED` before the transition is taken, because an
    unverified `completed` is worse than no command at all: it makes `gc` delete the
    worktree and branch of a run whose PR is still open.
    """
    home = factory_home()
    registry = load_registry(home / "config" / "projects.toml")
    routing = load_routing(home / "config" / "models.toml")
    store = _open_store(home)
    ticket = args.ticket.upper()
    run = store.run_by_ticket(ticket)
    if run is None:
        print(f"no run for {ticket}")
        return 1
    if run.state is State.COMPLETED:
        print(f"{ticket} is already completed; `factory gc` reclaims it")
        return 0
    if run.state is not State.AWAITING_HUMAN:
        print(
            f"{ticket} is at {run.state}, not {State.AWAITING_HUMAN}; only a run that has "
            "delivered a PR can be completed."
        )
        return 1
    if not run.pr_url:
        print(
            f"{ticket} reached {State.AWAITING_HUMAN} without opening a PR, so there is no "
            "merge to record. `factory cancel` is the command that ends it."
        )
        return 1

    project = registry.resolve(run.linear_id)
    state = github.pr_state(project.path, run.pr_url)
    if state is None:
        print(
            f"could not read {run.pr_url} with `gh` (unauthenticated, offline, or no repo "
            f"at {project.path}). Nothing was changed; this is worth retrying."
        )
        return 1
    if state != "MERGED":
        print(
            f"{run.pr_url} is {state}, not MERGED. {ticket} stays at "
            f"{State.AWAITING_HUMAN} — merging is yours (§13.2), and `completed` is only "
            "the record of it."
        )
        return 1

    if not store.acquire_lease(run.id, ttl_seconds=LEASE_TTL_SECONDS):
        print(f"\n{ticket} is leased by another process ({run.lease_owner}); wait for it.")
        return 1

    ctx = _context_for(home, registry, routing, store, LinearClient(), run)
    try:
        advance(
            ctx,
            State.COMPLETED,
            actor="human",
            rule="merge-is-james",
            detail=f"{run.pr_url} merged",
        )
        complete_step.announce_completed(ctx, pr_url=run.pr_url)
    finally:
        store.release_lease(run.id)

    print(
        f"{ticket} completed; {run.pr_url} is merged. Its worktree, branch and artifacts "
        f"are now collectable — `factory gc --dry-run` shows what that would reclaim."
    )
    return 0


# --------------------------------------------------------------------------------
# factory accept
# --------------------------------------------------------------------------------


#: The escalations a human can clear, mapped from the transition rule that parked the run.
#: Both are §15.3 companion checks, both fire before the review fan-out, and both are
#: documented there as judgement calls rather than blocks. Nothing else that reaches
#: `awaiting_human` is clearable: a host-execution deny-list hit is a security boundary,
#: and a delivered run with a review finding is completed or reopened, not accepted.
_CLEARABLE: dict[str, str] = {
    redphase.TEST_WEAKENING: "the removed assertions are accepted",
    redphase.REDPHASE_INCONCLUSIVE: "the inconclusive red-phase replay is accepted",
}


def cmd_accept(args: argparse.Namespace) -> int:
    """Clear a §15.3 escalation and let the review the guard interrupted actually run.

    The guards in `review.start` stop the run *before* Tier 1 and Tier 2, quote the hunks,
    and park at `awaiting_human`. Until this command existed the judgement they asked for
    had nowhere to go: `factory complete` refuses a run with no PR, `factory resume` refuses
    a run at `awaiting_human`, and the one built edge out of it sends the work back to
    `implementing` — which is the wrong answer when a human has just read the diff and said
    it is fine. Measured on FRO-11, 2026-08-23.

    It re-enters `reviewing`, not `pr_ready`. The escalation is spent, the review is not:
    a PR opened by skipping ahead would carry an empty Review section and claim by omission
    that the fan-out had found nothing.
    """
    home = factory_home()
    registry = load_registry(home / "config" / "projects.toml")
    routing = load_routing(home / "config" / "models.toml")
    store = _open_store(home)
    ticket = args.ticket.upper()
    run = store.run_by_ticket(ticket)
    if run is None:
        print(f"no run for {ticket}")
        return 1
    if run.state is not State.AWAITING_HUMAN:
        print(
            f"{ticket} is at {run.state}, not {State.AWAITING_HUMAN}; `accept` clears an "
            "escalation the review raised, and only a parked run has one."
        )
        return 1
    if run.pr_url:
        print(
            f"{ticket} already delivered {run.pr_url}, so it is not parked on an "
            "escalation. `factory complete` records the merge; `factory cancel` ends it."
        )
        return 1

    rule = _parked_on(store, run)
    if rule not in _CLEARABLE:
        print(
            f"{ticket} is parked on `{rule or 'nothing recorded'}`, which `accept` does not "
            f"clear (it clears {', '.join(sorted(_CLEARABLE))}). Nothing was changed."
        )
        return 1

    if not store.acquire_lease(run.id, ttl_seconds=LEASE_TTL_SECONDS):
        print(f"\n{ticket} is leased by another process ({run.lease_owner}); wait for it.")
        return 1

    ctx = _context_for(home, registry, routing, store, LinearClient(), run)
    note = args.note or _CLEARABLE[rule]
    try:
        # The record first, then the edge. `review.start` reads the check row on its way
        # back through the guard, so an advance that landed without one would re-escalate
        # on the same hunks and park the run again — the crash-safe order is this one.
        store.record_check(
            run.id,
            run.attempt,
            redphase.ESCALATION_ACCEPTED,
            "accepted",
            reason=rule,
            detail=note,
        )
        advance(
            ctx,
            State.REVIEWING,
            actor="human",
            rule="escalation-cleared-is-james",
            detail=f"{rule} cleared: {note}",
        )
        driver.drive(ctx)
    except Blocked as exc:
        _block(ctx, exc.reason, exc.detail)
        _report(ctx)
        return 2
    finally:
        store.release_lease(run.id)

    ctx.refresh()
    print(
        f"\n{ticket}: `{rule}` cleared, and the review it interrupted is running. "
        f"The run is at `{ctx.state}`; `factory tick` carries it to a PR."
    )
    return 0


def _parked_on(store: Store, run: Run) -> str:
    """The rule on the transition that put this run at `awaiting_human`.

    The last one, not the first: a run can be parked, reopened and parked again, and the
    escalation being cleared is the one it is sitting on now.
    """
    for row in reversed(store.transitions(run.id)):
        if State(row["to_state"]) is State.AWAITING_HUMAN:
            return str(row["rule"] or "")
    return ""


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
    store = _open_store(home)
    ticket = args.ticket.upper()
    run = store.run_by_ticket(ticket)
    if run is None:
        print(f"no run for {ticket}")
        return 1
    if run.state in machine.TERMINAL:
        print(f"{ticket} is at {run.state}; nothing to suspend")
        return 1
    if not machine.can(run.state, State.SUSPENDED):
        # Said here rather than left to `advance`, so a human who asked for something the
        # table forbids gets a sentence instead of a `blocked` run. §16.3b: suspend parks
        # a run that is *going*; a `resumable` or `blocked` one has already stopped, and
        # `resume` is the command for those.
        print(
            f"{ticket} is at {run.state}, which has no edge to {State.SUSPENDED}; "
            f"nothing to park. `factory resume {ticket}` is the command for a run that "
            "has already stopped."
        )
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
    store = _open_store(home)
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
        driver.drive(ctx)
    except Blocked as exc:
        _block(ctx, exc.reason, exc.detail)
        _report(ctx)
        return 2
    except Resumable as exc:
        print(f"\n{ticket} is resumable: {exc.reason} — {exc.detail}")
        record_stop(ctx, State.RESUMABLE, rule=exc.reason, detail=exc.detail)
        _report(ctx)
        return 3
    finally:
        store.release_lease(ctx.run.id)

    _report(ctx)
    if ctx.run.pr_url:
        print(f"\nReview ran and a pull request is open for review: {ctx.run.pr_url}\n")
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
    *,
    context_factory: ContextFactory | None = None,
) -> tuple[int, str]:
    """One §18.5 control. Acquires the lease, builds a real `Context`, dispatches through
    `recovery`/`_cancel_run` (every path writes an `actor="human"` transition — the §18.5
    contract), and releases the lease. Returns `(exit_code, message)`.

    F26: the sandbox a control touches is the run's own `factory-build-*`/`factory-review-*`
    sandbox, never a `codex-*` one. `policy.assert_factory_sandbox` guards the build sandbox
    before suspend/cancel stop it, so a misconfigured project pointing at an operator sandbox
    is refused here rather than acted on. The run controls do not take a sandbox argument;
    the run's project determines it, and the assertion is the boundary.

    `context_factory` is the same injection seam `tick_once` carries, and for the same
    reason: §21.3 requires the whole state machine to run in-process against fakes, and a
    control that always built the real `sbx` and `codex` adapters could not be tested
    without a Docker login. Nothing in production passes it.
    """
    if action not in _CONTROLS:
        return 1, f"unknown control {action!r}; one of {sorted(_CONTROLS)}"

    project = registry.projects.get(run.project)
    if project is not None:
        # F26 — refuse before acting if the run's sandbox is not a factory sandbox.
        policy.assert_factory_sandbox(project.build_sandbox)

    if not store.acquire_lease(run.id, ttl_seconds=LEASE_TTL_SECONDS):
        return 1, f"{run.linear_id} is leased by another process ({run.lease_owner})"

    build = context_factory or (lambda r: _context_for(home, registry, routing, store, linear, r))
    ctx = build(run)
    try:
        if action == "suspend":
            if run.state in machine.TERMINAL:
                return 1, f"{run.linear_id} is at {run.state}; nothing to suspend"
            if not machine.can(run.state, State.SUSPENDED):
                # The same refusal as `cmd_suspend`'s, because §18.5 says the control and
                # the command share a path — and because this is the one that fired: the
                # console suspended a `resumable` BAC-6 and recorded an edge that does
                # not exist.
                return 1, (
                    f"{run.linear_id} is at {run.state}, which has no edge to "
                    f"{State.SUSPENDED}; use Resume, not Suspend"
                )
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
        driver.drive(ctx)
    except Blocked as exc:
        _block(ctx, exc.reason, exc.detail)
        return 2, f"{run.linear_id} blocked: {exc.reason} — {exc.detail}"
    except Resumable as exc:
        record_stop(ctx, State.RESUMABLE, rule=exc.reason, detail=exc.detail)
        return 3, f"{run.linear_id} resumable: {exc.reason} — {exc.detail}"
    finally:
        store.release_lease(run.id)

    if ctx.run.pr_url:
        return 0, f"{run.linear_id} drove to {ctx.state}; PR: {ctx.run.pr_url}"
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
            f"factory serve needs the console dependencies ({exc}). Run `uv sync` and try again.",
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
    """§20.5, in four lines. The eighteen checks, the context they need and the three
    statuses live in `factory.doctor`; this owns the terminal and nothing else."""
    lines, code = doctor.report(doctor.run(factory_home(), deep=args.deep))
    for line in lines:
        print(line)
    return code


# --------------------------------------------------------------------------------
# shared plumbing
# --------------------------------------------------------------------------------


def _open_store(home: Path) -> Store:
    """The one state file. There is no second one to open."""
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
    run.add_argument(
        "--check",
        action="store_true",
        help="evaluate the intake conditions and stop; writes nothing",
    )
    run.add_argument("--plan", action="store_true", help="force the planning step first")
    run.add_argument(
        "--no-follow",
        action="store_true",
        help="start the run and return as soon as an agent is detached, rather than "
        "staying with it; `factory tick` picks it up from there",
    )
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

    complete = sub.add_parser(
        "complete", help="record that the PR was merged; lets `gc` reclaim the run"
    )
    complete.add_argument("ticket")
    complete.set_defaults(func=cmd_complete)

    accept = sub.add_parser(
        "accept", help="clear a §15.3 escalation and let the interrupted review run"
    )
    accept.add_argument("ticket")
    accept.add_argument(
        "--note",
        default=None,
        help="why it is accepted; carried into the PR body's cleared-escalations section",
    )
    accept.set_defaults(func=cmd_accept)

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
