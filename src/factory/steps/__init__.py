"""One module per state's entry action, and the context they share.

Every step is idempotent by construction, because the recovery model depends on it: a
tick that dies between two steps is re-entered, and a tick that dies *inside* one is
re-entered too. Anything with an external effect goes through the ledger, which is
committed before the call and reconciled rather than retried.

A step signals its outcome by raising: `Blocked` for "a human must look at this, and
here is the named reason", `Resumable` for "this attempt died and the next tick can
pick it up". Returning normally means the state advanced.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from factory import artifacts, policy
from factory.agent.codex import CodexAdapter
from factory.harness import HarnessConfig
from factory.intake.linear import Issue, LinearClient
from factory.machine import AUTOMATIC, Blocked, State, can
from factory.registry import Project, Registry
from factory.routing import Routing
from factory.sandbox.base import SandboxAdapter
from factory.store import Run, Store, marker

__all__ = ["Context", "advance", "record_effect"]

LEASE_TTL_SECONDS = 900


@dataclass
class Context:
    """Everything a step needs, resolved once at the top of a run.

    Mutable in exactly one respect: `run` is re-read after every transition, so a step
    never reasons about a state the database has moved past.
    """

    home: Path
    registry: Registry
    routing: Routing
    store: Store
    linear: LinearClient
    sandbox: SandboxAdapter
    agent: CodexAdapter
    project: Project
    run: Run
    issue: Issue | None = None
    harness: HarnessConfig | None = None
    dry_run: bool = False
    #: In a dry run every command that *would* have been executed lands here instead.
    #: `--dry-run` prints every command and executes none, which is only true if there
    #: is one place that decides.
    planned: list[str] = field(default_factory=list)
    #: Where a dry run has got to. The database is not written, so the real state never
    #: moves — and without somewhere to record the simulated position, the second
    #: transition would look illegal and the dry run would stop after one step while
    #: appearing to have walked the whole pipeline.
    shadow_state: State | None = None
    #: Same reason: a dry run never creates the worktree or records the branch, and the
    #: later steps have to be able to print the paths they would have used.
    shadow_worktree: Path | None = None
    shadow_branch: str | None = None

    # -- paths --------------------------------------------------------------------

    @property
    def state_dir(self) -> Path:
        """Where a run's files live before there is a worktree to put them in.

        `context_loaded` happens three states before `worktree_ready`, so the context
        files are staged here and copied into `.factory/` when the worktree appears.
        """
        return self.home / "state" / "runs" / self.run.id

    @property
    def log_dir(self) -> Path:
        return self.home / "logs"

    @property
    def artifact_root(self) -> Path:
        return self.home / "artifacts" / self.run.linear_id

    @property
    def worktree(self) -> Path:
        if self.run.worktree:
            return Path(self.run.worktree)
        if self.dry_run and self.shadow_worktree:
            return self.shadow_worktree
        raise Blocked("no-worktree", f"run {self.run.id} has no worktree recorded")

    @property
    def branch(self) -> str | None:
        return self.run.branch or (self.shadow_branch if self.dry_run else None)

    @property
    def clone_mount(self) -> Path:
        """The one writable host path a `--clone` project's VM can see.

        `sbx create --clone` replaces the bind mount with a private in-container clone of
        the repository, so nothing the agent writes under the project path reaches the
        host — which is exactly the isolation the frontend project needs, and exactly what
        breaks §4.2's filesystem protocol. This mount is the repair: an *additional* `rw`
        workspace, mounted at its identical path, whose writes the host sees immediately.
        The attempt directory and the seeded context live here rather than in the clone.

        Project-stable, not per-run. §9.1 fixes a sandbox's workspace set at creation and
        the sandbox is named once per project, so a per-run path would make the second run
        of a project fail `_assert_spec_matches` — the mistake `_review_scratch` already
        records. Per-ticket subdirectories inside it are free; the *mount* is what is fixed.
        """
        return self.home / "state" / "clone" / self.project.name

    @property
    def factory_dir(self) -> Path:
        """Where `.factory/` lives for this run — the host side of the protocol.

        For a bind-mounted project that is `<worktree>/.factory`, addressed by the same
        absolute string on both sides. For a clone project the worktree is inside the VM
        and its untracked content never reaches the host, so `.factory/` moves onto
        `clone_mount`, which keeps the identity property that matters: one path, both sides.

        Stable across `clone.fetch_back`, which repoints `ctx.worktree` at a host checkout.
        The evidence stays where it was written.

        Keyed by **run id**, not by ticket. A bind-mounted project gets a fresh tree for
        free, because `cancel` removes the worktree the evidence lives in — but the clone
        mount survives every run, so a ticket-keyed path put a second run of the same
        ticket on top of the first one's attempt directory. Measured on 2026-08-22: the
        second `factory run FRO-6` found the first run's `exit` file already present,
        returned from its wait instantly, read the first run's `last-message.json` and
        reported a four-hour-old verdict — with the first run's token counts, to the
        digit — while its own agent was still running in the sandbox. That is the exact
        failure this system exists to catch: it looked like a result and proved nothing.
        A run id in the path makes two runs of one ticket structurally unable to collide,
        and keeps the earlier run's evidence intact instead of overwriting it.
        """
        if self.project.requires_clone:
            return self.clone_mount / self.run.linear_id / self.run.id / ".factory"
        return self.worktree / ".factory"

    # -- plumbing -----------------------------------------------------------------

    @property
    def state(self) -> State:
        """Where the run is, real or simulated."""
        return self.shadow_state if self.dry_run and self.shadow_state else self.run.state

    def refresh(self) -> None:
        run = self.store.run_by_id(self.run.id)
        if run is None:
            raise Blocked("run-vanished", self.run.id)
        self.run = run

    def log(self, event: str, /, **detail: object) -> None:
        artifacts.log_event(
            self.log_dir,
            level=str(detail.pop("level", "info")),
            event=event,
            run_id=self.run.id,
            ticket=self.run.linear_id,
            state=str(self.run.state),
            attempt=self.run.attempt,
            detail=detail or None,
        )

    def would(self, description: str) -> None:
        """Record a command a dry run is not going to execute."""
        self.planned.append(description)

    def timeout_for(self, state: State) -> int:
        return self.registry.defaults.timeouts_seconds.get(str(state), 1800)


def advance(
    ctx: Context,
    to_state: State,
    *,
    actor: str = AUTOMATIC,
    rule: str | None = None,
    detail: str | None = None,
) -> None:
    """The only way a run changes state. §5.4's guards, in order.

    The verdict of `policy.requires_human` is written to the audit log with the rule
    that fired, whichever way it goes — a stop with no named rule is an unexplained
    stop, and an automatic hop past a human boundary is the failure this whole function
    exists to make impossible.
    """
    source = ctx.state
    if not can(source, to_state):
        raise Blocked(
            "illegal-transition", f"{source} -> {to_state} is not in the transition table"
        )

    human_rule = policy.requires_human(source, to_state)
    if human_rule and actor != "human":
        raise Blocked(
            "requires-human",
            f"{source} -> {to_state} is reserved for James by rule {human_rule!r}",
        )

    if not ctx.dry_run and not ctx.store.holds_lease(ctx.run.id):
        raise Blocked("lease-lost", f"run {ctx.run.id} no longer holds its lease")

    free_gb = shutil.disk_usage(ctx.home).free / 1_000_000_000
    if free_gb < ctx.registry.defaults.disk_min_free_gb:
        raise Blocked(
            "disk-below-floor",
            f"{free_gb:.1f} GB free, floor is {ctx.registry.defaults.disk_min_free_gb} GB",
        )

    if ctx.dry_run:
        ctx.would(f"transition {source} -> {to_state}")
        ctx.shadow_state = to_state
        return

    ctx.store.record_transition(
        ctx.run.id,
        from_state=source,
        to_state=to_state,
        actor=actor,
        rule=human_rule or rule,
        detail=detail,
    )
    ctx.store.renew_lease(ctx.run.id, ttl_seconds=LEASE_TTL_SECONDS)
    ctx.refresh()
    ctx.log("state.changed", **{"from": str(source), "to": str(to_state), "rule": rule})


def record_effect(
    ctx: Context,
    *,
    step: str,
    system: str,
    key: str,
    perform: Callable[[], str | None],
    reconcile: Callable[[], str | None],
) -> str | None:
    """§16.2, all four steps, in one place.

    `reconcile` answers "did this already happen?" by asking the external system, and
    it is called **first** whenever a row is already `intended` — a crash between the
    ledger write and the API call is the case this exists for, and retrying blindly
    there is how a run comments twice.
    """
    run_id, attempt = ctx.run.id, ctx.run.attempt
    existing = ctx.store.find_effect(run_id, attempt, step, system, key)
    if existing and existing.status == "confirmed":
        return existing.external_id

    if existing and existing.status == "intended":
        found = reconcile()
        if found:
            ctx.store.confirm_effect(run_id, attempt, step, system, key, str(found))
            ctx.log("effect.reconciled", step=step, system=system)
            return str(found)
    else:
        ctx.store.intend_effect(run_id, attempt, step, system, key)

    # F1/F2 — crash injection for the ledger's idempotency test. `pre` exits after the
    # `intended` row is committed but before the external write; `post` exits after the
    # write but before `confirmed`. The next call reconciles rather than retries blindly —
    # the whole reason the ledger exists. No-op in production, where the var is unset.
    _crash_if_asked(f"{system}:{key}:pre")
    external_id = perform()
    _crash_if_asked(f"{system}:{key}:post")
    ctx.store.confirm_effect(
        run_id, attempt, step, system, key, str(external_id) if external_id else None
    )
    ctx.log("effect.performed", step=step, system=system)
    return str(external_id) if external_id else None


def _crash_if_asked(point: str) -> None:
    """Raise `SystemExit` when `FACTORY_CRASH_AT` names `point`, so the ledger's
    reconcile-on-resume path can be tested against a real crash between its two halves."""
    if os.environ.get("FACTORY_CRASH_AT") == point:
        raise SystemExit(f"FACTORY_CRASH_AT={point}")


def effect_marker(ctx: Context, step: str) -> str:
    return marker(ctx.run.id, ctx.run.attempt, step)
