"""The five §18.5 views as plain data, shared by the CLI (`factory status`, `factory
runtimes`, `factory config`, `factory logs`) and `factory serve`.

One producer so the terminal and the page cannot disagree about what a run is — the same
shape of defect that cost FRO-6 a stale verdict when two functions read the same input
differently. Every view is read-only and builds no adapter: it reads the SQLite file the
daemon writes, the `events.jsonl`/`gates.json`/`review-summary.json` the steps write, and
the model cache the routing reads. Nothing here holds a credential or writes a transition
— controls go through `recovery`/`policy` in the app layer, never here.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factory import operator_controls, recovery
from factory.agent.telemetry import CurrentContext
from factory.console.events import (
    ToolCallView,
    TurnView,
    context_percentage,
    lane_of,
    read_tool_calls,
    read_turn_view,
)
from factory.machine import TERMINAL, State
from factory.registry import Registry
from factory.routing import Routing
from factory.steps import factory_dir_for
from factory.store import Run, Store

__all__ = [
    "AgentCard",
    "ArtifactLink",
    "ConfigRole",
    "ConfigView",
    "GateRow",
    "ReviewFinding",
    "RunDetail",
    "RunRow",
    "RunTimeline",
    "RuntimeRow",
    "ToolCallView",
    "TransitionRow",
    "WaterfallBlock",
    "config_view",
    "run_detail",
    "run_timeline",
    "runs_board",
    "runtimes",
]


#: States a run can be "live" in — not terminal, not parked-for-human. `awaiting_human`
#: and the terminal states stay on the board (the run exists) but show a badge; `suspended`
#: and `blocked` are the badges §18.5 names.
_SUSPENDED = State.SUSPENDED
_BLOCKED = State.BLOCKED


def _badge(run: Run) -> str | None:
    if run.state is _SUSPENDED:
        return "suspended"
    if run.state is _BLOCKED:
        return "blocked"
    return None


def _rung_label(attempt: int) -> str:
    """§16.3a's name for the rung this attempt occupies (1 RESTART, 2 RESUME, 3 REWIND),
    capped at 'max' — the rung past the ladder, where the budget is spent."""
    disposition = recovery._LADDER.get(attempt, recovery.Disposition.FAIL)
    return disposition.value


@dataclass(frozen=True)
class RunRow:
    run_id: str
    ticket: str
    project: str
    branch: str | None
    state: str
    badge: str | None
    attempt: int
    rung: str
    elapsed_in_state: float
    timeout_seconds: int | None
    context_pct: float | None
    context_reason: str | None
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    spend_usd: float | None
    spend_ceiling: float
    activity: str | None
    heartbeat_age: float | None
    blocked_reason: str | None
    pr_url: str | None
    title: str | None = None
    usage_status: str = "unknown"
    spend_status: str = "unknown"
    known_spend_usd: float | None = None
    context_source: str | None = None
    context_age_seconds: float | None = None
    active_invocations: int = 0
    fresh_context_invocations: int = 0


@dataclass(frozen=True)
class UsageSummary:
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    usage_status: str
    spend_status: str
    known_spend_usd: float | None


def run_usage(store: Store, run: Run) -> UsageSummary:
    """Ledger counters are cumulative; invocation telemetry establishes completeness.

    The cost row written at admission is a placeholder, not an observation of zero.
    Complete invocation estimates already appear in the ledger; known_spend adds only
    incomplete invocation lower bounds, preventing parent/child double accounting.
    """
    costs = store.costs(run.id)
    invocations = store.runtime.invocations(run.id)
    observed = [
        item for item in invocations if isinstance((item["telemetry"] or {}).get("usage"), dict)
    ]
    placeholder_keys = {
        (item["attempt"], item["metadata"].get("cost_step", item["id"]))
        for item in invocations
        if item not in observed
    }
    observed_costs = [row for row in costs if (row["attempt"], row["step"]) not in placeholder_keys]
    available = bool(observed_costs or observed)
    status = operator_controls.status(store, run.project, run.id)
    complete = available and all(
        (item["telemetry"] or {}).get("usage_complete") is True for item in invocations
    )
    complete = complete and not any(
        (item["telemetry"] or {}).get("nested_accounting", {}).get("complete") is False
        for item in invocations
    )
    complete = (
        complete
        and not status["active_agents"]
        and not status["queued_children"]
        and not status["pending_certifications"]
    )
    priced = any(row["usd"] is not None for row in costs) or any(
        "known_usd" in (item["telemetry"] or {}).get("estimate", {}) for item in invocations
    )
    cost_complete = bool(costs) and all(row["usd"] is not None for row in costs)
    cost_complete = cost_complete and (not invocations or status["cost_complete"])
    return UsageSummary(
        sum(row["input_tokens"] or 0 for row in costs),
        sum(row["output_tokens"] or 0 for row in costs),
        sum(row["cached_tokens"] or 0 for row in costs),
        "complete" if complete else "partial" if available else "unknown",
        "complete" if cost_complete else "partial" if priced else "unknown",
        store.known_spend(run.id) if priced else None,
    )


def _invocation_context_counts(store: Store, run: Run) -> tuple[int, int]:
    active_ids = {
        row["invocation_id"]
        for row in store.runtime.db.execute(
            "SELECT invocation_id FROM agent_leases WHERE run_id=? AND status='active'", (run.id,)
        )
    }
    fresh = 0
    for invocation in store.runtime.invocations(run.id):
        if invocation["id"] not in active_ids:
            continue
        context = (invocation["telemetry"] or {}).get("context", {})
        try:
            reading = CurrentContext(**context).read(now=time.time())
        except (TypeError, ValueError):
            continue
        fresh += reading.fraction is not None
    return len(active_ids), fresh


def _ticket_title(home: Path, run: Run) -> str | None:
    try:
        with (home / "state" / "runs" / run.id / "context" / "ticket.md").open(
            encoding="utf-8"
        ) as stream:
            heading = stream.readline(8192).strip()
    except (OSError, UnicodeError):
        return None
    prefix = f"# {run.linear_id} — "
    return heading.removeprefix(prefix).strip() or None if heading.startswith(prefix) else None


def runs_board(
    home: Path,
    registry: Registry,
    routing: Routing,
    store: Store,
    *,
    include_terminal: bool = False,
) -> list[RunRow]:
    """View 1 — one row per run, the live board. Everything is read back from disk: the
    transition log for elapsed-in-state, the attempt directory for liveness and the event
    stream, the costs table for tokens and spend."""
    rows: list[RunRow] = []
    for run in store.all_runs():
        # §18.5's board is "every non-terminal run": a finished or abandoned run is
        # history, and a board that listed every one of them would bury the two runs
        # actually in flight. `include_terminal` is the archive view, not the default.
        if not include_terminal and run.state in TERMINAL:
            continue
        project = registry.projects.get(run.project)
        transitions = store.transitions(run.id)
        elapsed = (time.time() - transitions[-1]["at"]) if transitions else 0.0
        timeout = registry.defaults.timeouts_seconds.get(run.state.value)

        view: TurnView | None = None
        heartbeat_age: float | None = None
        if project is not None:
            try:
                attempt_dir = factory_dir_for(home, project, run) / "run" / str(run.attempt)
            except Exception:
                attempt_dir = None
            if attempt_dir is not None:
                view = read_turn_view(attempt_dir / "events.jsonl")
                heartbeat = attempt_dir / "heartbeat"
                if heartbeat.exists():
                    heartbeat_age = time.time() - heartbeat.stat().st_mtime

        context_pct, context_reason = context_percentage(view, run.state, routing)
        tokens_in, tokens_out, usd = store.spend(run.id)
        usage = run_usage(store, run)
        cached = usage.tokens_cached
        active_invocations, fresh_context_invocations = _invocation_context_counts(store, run)

        rows.append(
            RunRow(
                run_id=run.id,
                ticket=run.linear_id,
                project=run.project,
                branch=run.branch,
                state=run.state.value,
                badge=_badge(run),
                attempt=run.attempt,
                rung=_rung_label(run.attempt),
                elapsed_in_state=elapsed,
                timeout_seconds=timeout,
                context_pct=context_pct,
                context_reason=context_reason,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                tokens_cached=cached,
                spend_usd=usd,
                spend_ceiling=routing.usd_per_run,
                activity=view.activity if view is not None else None,
                heartbeat_age=heartbeat_age,
                blocked_reason=run.blocked_reason,
                pr_url=run.pr_url,
                title=_ticket_title(home, run),
                active_invocations=active_invocations,
                fresh_context_invocations=fresh_context_invocations,
                usage_status=usage.usage_status,
                spend_status=usage.spend_status,
                known_spend_usd=usage.known_spend_usd,
                context_source="current attempt events.jsonl"
                if view is not None and view.context.observed_at is not None
                else None,
                context_age_seconds=max(0.0, time.time() - view.context.observed_at)
                if view is not None and view.context.observed_at is not None
                else None,
            )
        )
    return rows


@dataclass(frozen=True)
class TransitionRow:
    at: int
    from_state: str
    to_state: str
    actor: str
    rule: str | None
    detail: str | None


@dataclass(frozen=True)
class GateRow:
    name: str
    status: str
    caveat: str | None


@dataclass(frozen=True)
class ReviewFinding:
    severity: str | None
    file: str | None
    line: int | None
    summary: str | None


@dataclass(frozen=True)
class ArtifactLink:
    name: str
    path: str
    sha256: str | None


@dataclass(frozen=True)
class RunDetail:
    row: RunRow
    transitions: list[TransitionRow]
    gate_verdict: str | None
    gates: list[GateRow]
    review_tier2: str | None
    review_findings: list[ReviewFinding]
    artifacts: list[ArtifactLink]
    events_path: str | None
    pr_url: str | None
    blocked_reason: str | None


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def run_detail(
    home: Path, registry: Registry, routing: Routing, store: Store, run: Run
) -> RunDetail:
    """View 3 — the transition timeline, the gate report, the ranked review findings, and
    the artifact list with shas. Reads the attempt directory for gates/manifest and the
    run's state directory for the review summary (the review writes there, not into the
    attempt dir — §13.2)."""
    project = registry.projects.get(run.project)
    transitions = [
        TransitionRow(
            at=int(row["at"]),
            from_state=str(row["from_state"]),
            to_state=str(row["to_state"]),
            actor=str(row["actor"]),
            rule=row["rule"],
            detail=row["detail"],
        )
        for row in store.transitions(run.id)
    ]

    gate_verdict: str | None = None
    gates: list[GateRow] = []
    review_tier2: str | None = None
    review_findings: list[ReviewFinding] = []
    artifacts: list[ArtifactLink] = []
    events_path: str | None = None

    if project is not None:
        try:
            attempt_dir = factory_dir_for(home, project, run) / "run" / str(run.attempt)
        except Exception:
            attempt_dir = None
        if attempt_dir is not None:
            events_path = str(attempt_dir / "events.jsonl")
            gates_doc = _read_json(attempt_dir / "gates.json")
            if gates_doc is not None:
                gate_verdict = gates_doc.get("verdict")
                for gate in gates_doc.get("gates", []) or []:
                    if isinstance(gate, dict):
                        gates.append(
                            GateRow(
                                name=str(gate.get("name", "")),
                                status=str(gate.get("status", "")),
                                caveat=gate.get("caveat"),
                            )
                        )
            manifest = _read_json(attempt_dir / "manifest.json")
            if manifest is not None and isinstance(manifest.get("files"), list):
                for entry in manifest["files"]:
                    if isinstance(entry, dict):
                        artifacts.append(
                            ArtifactLink(
                                name=str(entry.get("name", "")),
                                path=str(entry.get("path", "")),
                                sha256=entry.get("sha256"),
                            )
                        )

        review_doc = _read_json(home / "state" / "runs" / run.id / "review" / "review-summary.json")
        if review_doc is not None:
            review_tier2 = review_doc.get("tier2")
            for finding in review_doc.get("findings", []) or []:
                if isinstance(finding, dict):
                    review_findings.append(
                        ReviewFinding(
                            severity=finding.get("severity"),
                            file=finding.get("file"),
                            line=finding.get("line"),
                            summary=finding.get("summary"),
                        )
                    )

    # `include_terminal` because a detail page is asked for by ticket: a cancelled or
    # completed run still has a page, and the board's non-terminal filter would drop its
    # row here and silently fall back to the zeroed placeholder below.
    rows = runs_board(home, registry, routing, store, include_terminal=True)
    row = next(
        (r for r in rows if r.run_id == run.id),
        RunRow(
            run_id=run.id,
            ticket=run.linear_id,
            project=run.project,
            branch=run.branch,
            state=run.state.value,
            badge=_badge(run),
            attempt=run.attempt,
            rung=_rung_label(run.attempt),
            elapsed_in_state=0.0,
            timeout_seconds=None,
            context_pct=None,
            context_reason=None,
            tokens_in=0,
            tokens_out=0,
            tokens_cached=0,
            spend_usd=None,
            spend_ceiling=routing.usd_per_run,
            activity=None,
            heartbeat_age=None,
            blocked_reason=run.blocked_reason,
            pr_url=run.pr_url,
        ),
    )
    return RunDetail(
        row=row,
        transitions=transitions,
        gate_verdict=gate_verdict,
        gates=gates,
        review_tier2=review_tier2,
        review_findings=review_findings,
        artifacts=artifacts,
        events_path=events_path,
        pr_url=run.pr_url,
        blocked_reason=run.blocked_reason,
    )


@dataclass(frozen=True)
class InventoryResult:
    sandboxes: list[dict[str, Any]]
    status: str = "available"
    reason: str | None = None


@dataclass(frozen=True)
class RuntimeAssociation:
    ticket: str
    run_id: str
    source: str
    reference: str
    current: bool


@dataclass(frozen=True)
class RuntimeRow:
    name: str
    state: str
    workspace: str
    published_ports: list[str]
    template: str
    operator_owned: bool
    runs_using: list[str] = field(default_factory=list)
    last_denial: str | None = None
    associations: list[RuntimeAssociation] = field(default_factory=list)
    layout: str = "Unavailable — no recorded execution layout"
    compatibility: str = "Unavailable — current runtime identity unobserved"
    agent: str = "Unavailable"
    recorded_compatibility: list[dict[str, Any]] = field(default_factory=list)


def _ports_of(sandbox: dict[str, Any]) -> list[str]:
    """The published ports of one `sbx ls --json` entry, as `host->sandbox` strings.

    The measured shape (v0.38.0, and what `SbxAdapter.git_daemon_url` reads) is a `ports`
    array of `{host_ip, host_port, sandbox_port}` objects, not a list of strings. The host
    port is reassigned on every start, which is exactly why the console shows it rather
    than letting an operator assume the one they saw last time still holds.
    """
    out: list[str] = []
    ports = sandbox.get("ports")
    for port in ports if isinstance(ports, list) else []:
        if not isinstance(port, dict):
            if isinstance(port, str):
                out.append(port)
            continue
        host_port = port.get("host_port")
        sandbox_port = port.get("sandbox_port")
        if host_port is None and sandbox_port is None:
            continue
        if type(host_port) is int and type(sandbox_port) is int:
            out.append(f"{host_port}->{sandbox_port}")
    return out


def _display(value: Any) -> str:
    return value if isinstance(value, str) and value.strip() else "Unavailable"


def _runtime_associations(store: Store) -> dict[str, list[RuntimeAssociation]]:
    associations: dict[str, list[RuntimeAssociation]] = {}
    for run in store.all_runs():

        def add(name: Any, source: str, reference: str, current: bool, owner: Run = run) -> None:
            if isinstance(name, str) and name:
                association = RuntimeAssociation(
                    owner.linear_id, owner.id, source, reference, current
                )
                if association not in associations.setdefault(name, []):
                    associations[name].append(association)

        for attempt in store.runtime.db.execute("SELECT * FROM attempts WHERE run_id=?", (run.id,)):
            add(
                attempt["sandbox"],
                "attempt",
                f"{attempt['attempt']} · {attempt['state']}",
                attempt["attempt"] == run.attempt
                and attempt["state"] == run.state.value
                and attempt["ended_at"] is None
                and run.state not in TERMINAL,
            )
        active = {
            row["invocation_id"]
            for row in store.runtime.db.execute(
                "SELECT invocation_id FROM agent_leases WHERE run_id=? AND status='active'",
                (run.id,),
            )
        }
        for effect in store.effects(run.id):
            if effect.system not in {
                "agent-launch",
                "agent-preparation",
                "delegation-mailbox",
                "child-execution",
            }:
                continue
            try:
                payload = json.loads(effect.external_id or "{}")
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and effect.system == "delegation-mailbox":
                spec = payload.get("spec", {})
                if isinstance(spec, dict):
                    add(spec.get("name"), "prepared specification", effect.step, False)
            if isinstance(payload, dict) and effect.system == "child-execution":
                add(payload.get("sandbox"), "child preparation", effect.step, False)
            handle = payload.get("handle", {}) if isinstance(payload, dict) else {}
            if isinstance(handle, dict):
                add(handle.get("sandbox"), "invocation", effect.step, effect.step in active)
        for invocation in store.runtime.invocations(run.id):
            metadata = invocation["metadata"]
            handle = (metadata.get("preparation") or {}).get("handle", {})
            add(handle.get("sandbox"), "invocation", invocation["id"], invocation["id"] in active)
        for job in store.runtime.db.execute(
            "SELECT * FROM runtime_certifications WHERE run_id=?", (run.id,)
        ):
            identity = json.loads(job["identity"])
            add(identity.get("sandbox"), "certification " + job["status"], job["id"], False)
    return associations


def _recorded_compatibility(store: Store) -> dict[str, list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = {}
    for run in store.all_runs():
        for row in store.runtime.db.execute(
            "SELECT * FROM runtime_certifications WHERE run_id=?", (run.id,)
        ):
            identity = json.loads(row["identity"])
            sandbox = identity.get("sandbox")
            if not isinstance(sandbox, str):
                continue
            evidence = json.loads(row["evidence"]) if row["evidence"] else None
            records.setdefault(sandbox, []).append(
                {
                    "source": "recorded certification",
                    "ticket": run.linear_id,
                    "job_id": row["id"],
                    "status": row["status"],
                    "identity": identity,
                    "fingerprint": row["fingerprint"],
                    "failure": row["failure"],
                    "evidence": evidence,
                }
            )
        for invocation in store.runtime.invocations(run.id):
            report = invocation["metadata"].get("runtime_compatibility")
            if not isinstance(report, dict):
                continue
            certification = report.get("certification", {})
            identity = certification.get("identity", {}) if isinstance(certification, dict) else {}
            sandbox = (
                identity.get("sandbox", report.get("sandbox"))
                if isinstance(identity, dict)
                else report.get("sandbox")
            )
            if isinstance(sandbox, str):
                records.setdefault(sandbox, []).append(
                    {
                        "source": "recorded invocation compatibility",
                        "ticket": run.linear_id,
                        "invocation_id": invocation["id"],
                        "report": report,
                    }
                )
    return records


def _recorded_layouts(store: Store) -> dict[str, set[str]]:
    """Only retained specifications establish bind/clone; registry defaults do not."""
    layouts: dict[str, set[str]] = {}
    for run in store.all_runs():
        for effect in store.effects(run.id):
            if effect.system != "delegation-mailbox" or effect.status != "confirmed":
                continue
            try:
                payload = json.loads(effect.external_id or "{}")
            except ValueError:
                continue
            spec = payload.get("spec", {}) if isinstance(payload, dict) else {}
            if (
                not isinstance(spec, dict)
                or not isinstance(spec.get("name"), str)
                or type(spec.get("clone")) is not bool
            ):
                continue
            label = "clone" if spec["clone"] else "bind"
            layouts.setdefault(spec["name"], set()).add(
                f"{label} · recorded {run.linear_id} attempt {effect.attempt}"
            )
    return layouts


def runtimes(
    sandboxes: list[dict[str, Any]],
    store: Store,
    namespace: str = "factory-",
    *,
    registry: Registry | None = None,
) -> list[RuntimeRow]:
    """Normalize inventory and join recorded uses, without inferring activity from names.

    Certification records describe their retained identity. Listing an identically named
    sandbox does not observe its runtime generation, so it cannot establish certification.
    """
    recorded = _runtime_associations(store)
    layouts = _recorded_layouts(store)
    compatibility = _recorded_compatibility(store)
    rows: list[RuntimeRow] = []
    for sbx in sandboxes:
        if not isinstance(sbx, dict):
            continue
        name = _display(sbx.get("name"))
        is_factory = name.startswith(("factory-build-", "factory-review-"))
        associations = list(recorded.get(name, []))
        if not associations and registry is not None:
            for project in registry.projects.values():
                if name in (project.build_sandbox, project.review_sandbox):
                    associations.append(
                        RuntimeAssociation("", "", "registry default", project.name, False)
                    )
        workspaces = sbx.get("workspaces", sbx.get("workspace"))
        workspace = (
            ", ".join(workspaces)
            if isinstance(workspaces, list)
            and workspaces
            and all(isinstance(item, str) for item in workspaces)
            else _display(workspaces)
        )
        rows.append(
            RuntimeRow(
                name=name,
                state=_display(sbx.get("status", sbx.get("state"))),
                workspace=workspace,
                published_ports=_ports_of(sbx),
                template=_display(sbx.get("template")),
                operator_owned=not is_factory,
                runs_using=sorted(
                    {item.ticket for item in associations if item.current and item.ticket}
                ),
                last_denial=sbx.get("last_denial")
                if isinstance(sbx.get("last_denial"), str)
                else None,
                associations=associations,
                recorded_compatibility=compatibility.get(name, []),
                layout="; ".join(sorted(layouts[name]))
                if name in layouts
                else "Unavailable — no recorded execution layout",
                agent=_display(sbx.get("agent")),
            )
        )
    return rows


@dataclass(frozen=True)
class ConfigRole:
    name: str
    model: str
    effort: str


@dataclass(frozen=True)
class ConfigView:
    roles: list[ConfigRole]
    usd_per_run: float
    usd_warn_at: float
    projects_read_only: list[str]


def config_view(routing: Routing, registry: Registry) -> ConfigView:
    """View 4 — the editable `models.toml` (roles, efforts, the two budget numbers) and
    the read-only `projects.toml` (project names, listed only — changing a project's
    template/mount/MCP set changes a sandbox spec fixed at creation, §18.5)."""
    roles = [
        ConfigRole(name=name, model=role.model, effort=role.effort)
        for name, role in routing.roles.items()
    ]
    return ConfigView(
        roles=roles,
        usd_per_run=routing.usd_per_run,
        usd_warn_at=routing.usd_warn_at,
        projects_read_only=list(registry.projects),
    )


# --------------------------------------------------------------------------------
# View 6 — the run timeline (agent status + runtime waterfall). §18.5 extension.
#
# Three bands, summary before detail: a head strip (the same `RunRow` the board and the
# run detail render), one card per agent role (the Gmail-MCP "installed server / authed"
# analogue), a swim-lane waterfall of the run's states on a time axis (the SSSF
# visualizer), and a per-tool-call drill-down folded from `events.jsonl`.
#
# The honesty table in `.agents/plans/console-run-timeline-plan.md` decides every field:
# only what the factory actually records is shown. The waterfall's block widths come from
# `Run.created_at` and the transitions' `at`; the cards' context % comes from
# `context_percentage`; the tool calls come from `read_tool_calls`. Per-call duration and
# per-role token attribution are **absent** — `events.jsonl` carries no timestamp (P0-7),
# and a number the console cannot defend is the one thing §18.5 refuses to show.
# --------------------------------------------------------------------------------


#: The agent roles, in forward order, with the state each runs in. The state machine's
#: `_AGENT_ROLE` (events.py) maps the other way; this is its inverse for the card row.
_AGENT_ROLES: tuple[tuple[str, State], ...] = (
    ("planner", State.PLANNING),
    ("builder", State.IMPLEMENTING),
    ("reviewer", State.REVIEWING),
)


@dataclass(frozen=True)
class WaterfallBlock:
    """One state the run occupied, laid on the time axis. `start`/`end` are unix seconds
    from `Run.created_at` and the transitions' `at`; the last block runs to `now` and is
    `live`. `dead` marks a block the run left via `→ resumable`/`→ failed` (the hatched
    ghost of a dead attempt); `attempt` is `run.attempt` for live/resume blocks and the
    rung below for a dead one. `lane` is the actor identity (engineer / planner / builder
    / reviewer / code), or `None` for a marker state (`blocked`, `resumable`, …)."""

    state: str
    lane: str | None
    start: int
    end: int
    duration_s: float
    attempt: int
    dead: bool
    live: bool


@dataclass(frozen=True)
class AgentCard:
    """One agent role's status. `status` is `live` (the role running in the current
    state), `done` (the role's state was entered — it ran), or `idle` (not yet reached).
    `context_pct`/`context_reason` come from `context_percentage` for the live role only;
    a non-live role has `context_pct = None` and a reason that says so — the honesty
    table's central call is that the stream is never attributed to a role it cannot be
    attributed to. `activity` and `heartbeat_age` are live-role-only too."""

    role: str
    model: str
    effort: str
    status: str
    context_pct: float | None
    context_reason: str | None
    activity: str | None
    heartbeat_age: float | None


@dataclass(frozen=True)
class RunTimeline:
    row: RunRow
    blocks: list[WaterfallBlock]
    cards: list[AgentCard]
    tool_calls: list[ToolCallView]
    elapsed_total_s: float


def _fallback_row(run: Run, routing: Routing) -> RunRow:
    """The row for a run `runs_board` did not return one for — the same defensive shape
    `run_detail` uses, so a timeline page never 500s on a run whose project has left the
    registry."""
    return RunRow(
        run_id=run.id,
        ticket=run.linear_id,
        project=run.project,
        branch=run.branch,
        state=run.state.value,
        badge=_badge(run),
        attempt=run.attempt,
        rung=_rung_label(run.attempt),
        elapsed_in_state=0.0,
        timeout_seconds=None,
        context_pct=None,
        context_reason=None,
        tokens_in=0,
        tokens_out=0,
        tokens_cached=0,
        spend_usd=None,
        spend_ceiling=routing.usd_per_run,
        activity=None,
        heartbeat_age=None,
        blocked_reason=run.blocked_reason,
        pr_url=run.pr_url,
    )


def _role_status(role_state: State, run: Run, transitions: list[sqlite3.Row]) -> str:
    """`live` / `done` / `idle` for one role against the run's state and history.

    `live` is the role running in the current state. `done` is a role whose state was
    *entered* at some point — it ran, even if a rewind has since taken the run back past
    it (`reviewing → implementing` leaves the reviewer `done`, not `idle`). `idle` is a
    role the run has not reached. The `entered` flag is the honest signal: a forward-
    progress rank would call planning `done` on a run that skipped straight to
    implementing (`auto = false`), but planning never ran there, so it is `idle`.
    """
    if run.state is role_state:
        return "live"
    entered = any(State(str(t["to_state"])) is role_state for t in transitions)
    return "done" if entered else "idle"


def run_timeline(
    home: Path, registry: Registry, routing: Routing, store: Store, run: Run
) -> RunTimeline:
    """View 6 — the run timeline. One producer, shared with nothing yet (the page is the
    first consumer); built to be reusable by a future `factory timeline` CLI so the
    terminal and the page cannot disagree, the same property the other views hold.

    Read-only, no credential, no transition written — the same boundaries as every other
    view. Blocks come from the transition log, cards from `routing` + the live attempt's
    event stream, tool calls from `read_tool_calls`.
    """
    now = time.time()
    transitions = store.transitions(run.id)

    # ---- band 3: the waterfall ----
    entries: list[tuple[State, int]] = [(State.APPROVED, run.created_at)]
    for t in transitions:
        entries.append((State(str(t["to_state"])), int(t["at"])))
    blocks: list[WaterfallBlock] = []
    for i, (state, start) in enumerate(entries):
        end = entries[i + 1][1] if i + 1 < len(entries) else int(now)
        live = i == len(entries) - 1
        next_state = entries[i + 1][0] if i + 1 < len(entries) else None
        dead = (not live) and next_state in (State.RESUMABLE, State.FAILED)
        blocks.append(
            WaterfallBlock(
                state=state.value,
                lane=lane_of(state),
                start=start,
                end=end,
                duration_s=max(0.0, float(end - start)),
                attempt=max(0, run.attempt - 1) if dead else run.attempt,
                dead=dead,
                live=live,
            )
        )

    # ---- the live attempt's event stream + heartbeat, shared by bands 2 and 4 ----
    project = registry.projects.get(run.project)
    attempt_dir: Path | None = None
    if project is not None:
        try:
            attempt_dir = factory_dir_for(home, project, run) / "run" / str(run.attempt)
        except Exception:
            attempt_dir = None
    view = read_turn_view(attempt_dir / "events.jsonl") if attempt_dir is not None else None
    heartbeat_age: float | None = None
    if attempt_dir is not None:
        heartbeat = attempt_dir / "heartbeat"
        if heartbeat.exists():
            heartbeat_age = now - heartbeat.stat().st_mtime

    # ---- band 2: the agent cards ----
    cards: list[AgentCard] = []
    for role, role_state in _AGENT_ROLES:
        role_cfg = routing.roles.get(role)
        if role_cfg is None:
            continue  # an unfamiliar role is not the console's to invent
        status = _role_status(role_state, run, transitions)
        if status == "live":
            context_pct, context_reason = context_percentage(view, run.state, routing)
            activity = view.activity if view is not None else None
            hb = heartbeat_age
        else:
            context_pct = None
            context_reason = "role already ran" if status == "done" else "role not yet reached"
            activity = None
            hb = None
        cards.append(
            AgentCard(
                role=role,
                model=role_cfg.model,
                effort=role_cfg.effort,
                status=status,
                context_pct=context_pct,
                context_reason=context_reason,
                activity=activity,
                heartbeat_age=hb,
            )
        )

    # ---- band 4: the tool-call drill-down (the whole attempt's calls — Phase 1) ----
    tool_calls = read_tool_calls(attempt_dir / "events.jsonl") if attempt_dir is not None else []

    # ---- band 1: the head strip (reuse the board row) ----
    rows = runs_board(home, registry, routing, store, include_terminal=True)
    row = next((r for r in rows if r.run_id == run.id), _fallback_row(run, routing))

    return RunTimeline(
        row=row,
        blocks=blocks,
        cards=cards,
        tool_calls=tool_calls,
        elapsed_total_s=max(0.0, now - run.created_at),
    )
