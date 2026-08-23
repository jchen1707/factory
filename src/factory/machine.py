"""The transition table — §5. Pure: no I/O, no clock, no subprocess.

Everything that decides *whether* a hop is legal lives here, so the whole of §5.2 can
be asserted by a table-driven test with nothing mocked. Everything that *performs* a
hop lives in `steps/`, behind an adapter.

The two exception types are here rather than in a module of their own because they
are part of the transition vocabulary: a step raises `Blocked` to say "this run needs
a human and here is the named reason", and `Resumable` to say "this attempt died in a
way the next tick can pick up". Anything else escaping a step is a factory bug and is
allowed to propagate as one.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "AUTOMATIC",
    "HUMAN_ONLY",
    "TERMINAL",
    "TRANSITIONS",
    "Blocked",
    "Resumable",
    "State",
    "assert_table_is_sound",
    "can",
    "requires_human_rule",
]


class State(StrEnum):
    """One row of §5.1. The string value is what SQLite stores."""

    APPROVED = "approved"
    CLAIMED = "claimed"
    CONTEXT_LOADED = "context_loaded"
    SANDBOX_CREATING = "sandbox_creating"
    SANDBOX_READY = "sandbox_ready"
    WORKTREE_READY = "worktree_ready"
    PLANNING = "planning"
    IMPLEMENTING = "implementing"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    BLOCKED = "blocked"
    PR_READY = "pr_ready"
    AWAITING_HUMAN = "awaiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    RESUMABLE = "resumable"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


#: States with no exit. `failed` is deliberately not here: §5.3 gives it one edge,
#: `failed -> resumable`, and that edge is James re-authorising spend.
TERMINAL: frozenset[State] = frozenset({State.COMPLETED, State.CANCELLED})

#: §5.2's workflow hops, verbatim. A hop absent from the final table cannot be performed
#: at all — there is no "unknown transition" fallback, because a fallback is how a state
#: machine quietly becomes a suggestion.
#:
#: `blocked` is added to every row below rather than written out here; see `TRANSITIONS`.
_WORKFLOW: dict[State, frozenset[State]] = {
    State.APPROVED: frozenset({State.CLAIMED, State.CANCELLED}),
    State.CLAIMED: frozenset({State.CONTEXT_LOADED, State.RESUMABLE, State.CANCELLED}),
    State.CONTEXT_LOADED: frozenset({State.SANDBOX_CREATING, State.BLOCKED, State.CANCELLED}),
    State.SANDBOX_CREATING: frozenset({State.SANDBOX_READY, State.RESUMABLE, State.CANCELLED}),
    State.SANDBOX_READY: frozenset({State.WORKTREE_READY, State.BLOCKED, State.CANCELLED}),
    State.WORKTREE_READY: frozenset(
        {State.IMPLEMENTING, State.PLANNING, State.RESUMABLE, State.CANCELLED}
    ),
    State.PLANNING: frozenset(
        {State.IMPLEMENTING, State.BLOCKED, State.RESUMABLE, State.SUSPENDED, State.CANCELLED}
    ),
    State.IMPLEMENTING: frozenset(
        {State.VERIFYING, State.RESUMABLE, State.BLOCKED, State.SUSPENDED, State.CANCELLED}
    ),
    State.VERIFYING: frozenset(
        {
            State.REVIEWING,
            State.IMPLEMENTING,
            State.PLANNING,
            State.BLOCKED,
            State.RESUMABLE,
            State.SUSPENDED,
            State.CANCELLED,
        }
    ),
    State.REVIEWING: frozenset(
        {
            State.PR_READY,
            State.IMPLEMENTING,
            State.PLANNING,
            State.BLOCKED,
            State.RESUMABLE,
            State.AWAITING_HUMAN,
            State.SUSPENDED,
            State.CANCELLED,
        }
    ),
    State.PR_READY: frozenset({State.AWAITING_HUMAN, State.BLOCKED, State.CANCELLED}),
    #: `reviewing` is the edge back out of an escalation the review itself raised. §15.3's
    #: two companion checks stop the run *before* the fan-out — the red-phase replay when it
    #: is inconclusive, the test-weakening guard when the diff deletes an assertion — and
    #: both are judgement calls, so both park at `awaiting_human` with the hunks quoted.
    #: Until this edge existed there was nothing to do with the judgement once it was made:
    #: `completed` refuses a run with no PR, `resume` refuses a run at `awaiting_human`, and
    #: `implementing` sends work back that a human has just said is fine. Measured on FRO-11,
    #: 2026-08-23, which stopped on three assertions the ticket itself had deleted the
    #: subject of. Re-entering `reviewing` rather than jumping to `pr_ready` is deliberate:
    #: the guard fires before Tier 1 and Tier 2 run, so skipping ahead would open a PR whose
    #: Review section was empty. Clearing the escalation resumes the review; it does not
    #: replace it.
    State.AWAITING_HUMAN: frozenset(
        {State.COMPLETED, State.CANCELLED, State.IMPLEMENTING, State.REVIEWING}
    ),
    #: `verifying` and `reviewing` are the two edges the live resume case needs: FRO-6 was
    #: blocked *at* `verifying` with a complete, gate-passing implementation, and the cheap
    #: repair is to re-enter the state that blocked rather than spend another 10 M-token
    #: implement. Both exits are human-gated below under the existing `unblock-is-a-judgement`
    #: rule, so widening the way *out* of `blocked` does not widen it to automatic.
    State.BLOCKED: frozenset(
        {State.IMPLEMENTING, State.PLANNING, State.VERIFYING, State.REVIEWING, State.CANCELLED}
    ),
    State.RESUMABLE: frozenset(
        {State.IMPLEMENTING, State.PLANNING, State.VERIFYING, State.REVIEWING, State.FAILED}
    ),
    State.SUSPENDED: frozenset(
        {State.IMPLEMENTING, State.PLANNING, State.VERIFYING, State.REVIEWING, State.CANCELLED}
    ),
    State.FAILED: frozenset({State.RESUMABLE, State.CANCELLED}),
    State.COMPLETED: frozenset(),
    State.CANCELLED: frozenset(),
}


#: `blocked` is reachable from every non-terminal state, for the same reason `cancelled`
#: is: it is not a step in the workflow, it is the workflow stopping. Any step can raise
#: `Blocked`, and three of the raise sites are in `steps/__init__.py` — `lease-lost`,
#: `run-vanished`, `no-worktree` — which fire from whatever state the run is in.
#:
#: Enumerating the reachable subset by hand is what §5.2's diagram does, and it was
#: wrong: measured 2026-08-21, `factory run BAC-4` raised `enforcement-disabled` from the
#: preflight, and because `sandbox_creating` had no `blocked` edge the run recorded a
#: `blocked_reason` with no transition and came to rest reporting `sandbox_creating`.
#: A state machine that cannot record a stop it just performed is not describing the run.
#:
#: Leaving `blocked` stays governed: `HUMAN_ONLY` reserves both exits from it.
#:
#: `cancelled` is derived the same way, and for the same reason twice over. §5.3's table
#: gives the edge as `* -> cancelled`, "abandoning work" — a wildcard, not a list — but
#: `_WORKFLOW` above spelled the list out by hand and missed `resumable`. Measured
#: 2026-08-21: `codex exec` failed inside the VM, the run came to rest at `resumable`,
#: and `factory cancel BAC-4` then archived the attempt, removed the worktree, deleted
#: the branch and moved Linear back to `Todo` — while `cmd_cancel`'s
#: `if machine.can(...)` guard silently skipped the transition, leaving the row at
#: `resumable`. The next `factory run` refused a ticket that cancel had reported
#: cancelling. Deriving the edge is what stops the list drifting from the wildcard a
#: third time.
def _stops(state: State) -> frozenset[State]:
    """The two ways a run stops rather than steps, minus a self-edge."""
    if state in TERMINAL:
        return frozenset()
    return frozenset({State.BLOCKED, State.CANCELLED}) - {state}


TRANSITIONS: dict[State, frozenset[State]] = {
    state: targets | _stops(state) for state, targets in _WORKFLOW.items()
}

#: §5.3 — the hops the factory must physically stop at, mapped to the rule that fires.
#: `policy.requires_human()` is the only caller; keeping the table beside the
#: transitions means a new edge cannot be added without walking past this list.
HUMAN_ONLY: dict[tuple[State, State], str] = {
    (State.AWAITING_HUMAN, State.COMPLETED): "merge-is-james",
    (State.BLOCKED, State.IMPLEMENTING): "unblock-is-a-judgement",
    (State.BLOCKED, State.PLANNING): "unblock-is-a-judgement",
    (State.BLOCKED, State.VERIFYING): "unblock-is-a-judgement",
    (State.BLOCKED, State.REVIEWING): "unblock-is-a-judgement",
    (State.AWAITING_HUMAN, State.IMPLEMENTING): "reopen-after-review-is-james",
    (State.AWAITING_HUMAN, State.REVIEWING): "escalation-cleared-is-james",
    (State.FAILED, State.RESUMABLE): "reauthorise-spend",
}

#: Reached only by an explicit human act, from wherever the run happens to be.
#: `cancelled` abandons work; `suspended` parks it. Neither is ever automatic — a run
#: the factory cannot continue goes to `blocked` or `resumable`, which are legible.
HUMAN_ONLY_DESTINATIONS: dict[State, str] = {
    State.CANCELLED: "abandon-is-james",
    State.SUSPENDED: "suspend-is-james",
}

#: The reverse of the above: leaving a park is James's too.
HUMAN_ONLY_ORIGINS: dict[State, str] = {
    State.SUSPENDED: "resume-is-james",
}

AUTOMATIC = "auto"
HUMAN = "human"


class Blocked(Exception):
    """A run needs a human. The reason is a stable slug, not prose.

    Every reason in the plan is one of these: `state-not-todo`, `no-parent-spec`,
    `empty-spec`, `no-acceptance-criteria`, `duplicate-pr`, `team-repo-mismatch`,
    `stack-mismatch`, `vault-unresolved`, `enforcement-disabled`, `schema-invalid`,
    `evidence-mismatch`, `budget-exceeded`, `vault-write-outside-allowlist`,
    `already-implemented`, `branch-exists`, `env-gate-failed`.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class Resumable(Exception):
    """This attempt died. The next tick decides resume-versus-restart (§16.3)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def can(source: State, target: State) -> bool:
    """Is `source -> target` in the table at all?"""
    return target in TRANSITIONS[source]


def requires_human_rule(source: State, target: State) -> str | None:
    """The name of the rule that reserves this hop for James, or None.

    Named rather than boolean because §5.3 requires the verdict *and the rule that
    fired* in the audit log. "Refused" without a rule name is an unexplained stop.
    """
    if rule := HUMAN_ONLY.get((source, target)):
        return rule
    if rule := HUMAN_ONLY_DESTINATIONS.get(target):
        return rule
    if rule := HUMAN_ONLY_ORIGINS.get(source):
        return rule
    return None


def assert_table_is_sound() -> None:
    """Every state is reachable from `approved`, and every non-terminal has an exit.

    Called by `factory doctor` as well as by the test, because a table edited in a
    hurry is exactly the kind of change that passes review and fails at 3 a.m.
    """
    missing = set(State) - set(TRANSITIONS)
    if missing:
        raise AssertionError(f"states with no row in TRANSITIONS: {sorted(missing)}")

    for state, targets in TRANSITIONS.items():
        if state in TERMINAL:
            if targets:
                raise AssertionError(f"terminal state {state} has exits: {sorted(targets)}")
            continue
        if not targets:
            raise AssertionError(f"non-terminal state {state} has no exit")

    seen = {State.APPROVED}
    frontier = [State.APPROVED]
    while frontier:
        for nxt in TRANSITIONS[frontier.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    unreachable = set(State) - seen
    if unreachable:
        raise AssertionError(f"unreachable states: {sorted(unreachable)}")
