"""`factory serve` — the §18.5 operator console. Loopback-only, read-mostly.

One server-rendered page per view, server-sent events for the live tail, no frontend
build step and no bundler. The page holds no credential of its own: it reads the same
SQLite file the daemon writes, through `console.views`, and every control it offers goes
back through the same `recovery`/`policy` machinery the CLI uses (`cli.dispatch_control`),
so a console action and a typed command cannot diverge.

**There is no Merge button.** Merging happens on GitHub, by James; the console links out
to the pull request and stops there (§18.5, and `tests/*/test_console.py` asserts the
string is absent from every rendered page).

`cli` imports `console.views`, so everything this module needs from `cli` is imported
inside the function that needs it — the cycle is real and the lazy import is the seam.
"""

from __future__ import annotations

import asyncio
import html
import json
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from factory import routing as routing_module
from factory.console import views as console_views
from factory.console.assets import BundleMiddleware, RenderBundle, current_bundle
from factory.intake.linear import LinearClient
from factory.machine import Blocked, State
from factory.registry import Registry, load_registry
from factory.routing import Routing, RoutingError, load_routing
from factory.store import Store

__all__ = ["create_app"]

#: The loopback interface, and the only one §18.5 permits. Off-machine access is a Phase 7
#: decision with its own auth story, not a flag on this one.
LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 7717

#: How often the SSE stream re-reads the board. The daemon ticks every 60 s; a console
#: that refreshed slower than the thing it watches would show a stale board, and one that
#: refreshed much faster would spend the whole interval re-reading an unchanged file.
SSE_INTERVAL_SECONDS = 2

#: How much of the event stream the page tail shows. A full transcript belongs in
#: `factory logs <TICKET> --follow`, not in a page that re-sends it every two seconds.
TAIL_LINES = 40


def _e(value: object) -> str:
    """Escape for HTML. Every interpolation goes through this — run titles, block reasons
    and review summaries are all model- or tracker-authored text."""
    return html.escape(str(value), quote=True)


#: Where the server-rendered pages live — §19 names `console/templates/*.html`, one page
#: per view. They are `string.Template` files rather than a Jinja environment: the console
#: renders half a dozen static shells, and a template engine is a dependency (and a
#: sandbox-escape surface) bought for nothing. `$name` is substituted; `$$` is a literal.
TEMPLATES = Path(__file__).parent / "templates"


def _render(name: str, /, **fields: str) -> str:
    return current_bundle().render(name, **fields)


def _page(title: str, body: str, *, ticket: str | None = None, view: str = "") -> str:
    """The shell every view shares: head, stylesheet, nav, body."""
    links = [
        ("runs", "/", "Runs"),
        ("projects", "/projects", "Projects"),
        ("runtimes", "/runtimes", "Runtimes"),
        ("configuration", "/config", "Configuration"),
    ]
    current = view or title
    navigation = "".join(
        f'<a href="{href}"' + (' aria-current="page"' if key == current else "") + f">{label}</a>"
        for key, href, label in links
    )
    run_navigation = ""
    if ticket:
        run_links = [
            ("detail", f"/runs/{ticket}", "Run details"),
            ("timeline", f"/runs/{ticket}/timeline", "Run timeline"),
            ("settings", f"/settings/runs/{ticket}", "Run settings"),
        ]
        run_navigation = (
            '<nav class="run-nav" aria-label="Current run">'
            + "".join(
                f'<a href="{_e(href)}"'
                + (' aria-current="page"' if key == current else "")
                + f">{label}</a>"
                for key, href, label in run_links
            )
            + "</nav>"
        )
    return _render(
        "page.html",
        title=_e(title),
        style=current_bundle().style,
        build_identity=_e(current_bundle().identity),
        started_at=_e(current_bundle().started_at),
        body=body,
        navigation=navigation,
        run_navigation=run_navigation,
    )


def _pct_cell(pct: float | None, reason: str | None) -> str:
    """The context percentage, or the reason it is hidden. §18.5: never estimated — a
    hidden percentage says why, so the operator can tell 'no window on file' from 'the
    agent has not finished a turn'."""
    if pct is None:
        return f'<span class="muted" title="{_e(reason or "current context unavailable")}">{_e(reason or "current context unavailable")}</span>'
    width = max(0, min(100, int(pct * 100)))
    cls = "fail" if width >= 85 else ("warn" if width >= 70 else "")
    return (
        f'<span class="{cls}">{pct * 100:.0f}%</span> '
        f'<span class="bar"><i style="width:{width}%"></i></span>'
    )


def _estimate_value(row: console_views.RunRow) -> str:
    """One evidence-aware amount across the board, detail cards and timeline."""
    if row.known_spend_usd is None:
        return "Unavailable"
    prefix = "" if row.spend_status == "complete" else "≥ "
    return f"{prefix}${row.known_spend_usd:.2f}"


def _estimate_cell(row: console_views.RunRow) -> str:
    return (
        _estimate_value(row)
        + f"<small>API-equivalent USD · {_e(row.spend_status)} · ${row.spend_ceiling:.0f} ceiling</small>"
    )


def _settings_form(
    action: str,
    settings: dict[str, Any],
    *,
    concurrency: int | str | None = None,
    error: str | None = None,
) -> str:
    def options_for(choices: tuple[str, ...], selected: object) -> str:
        value = str(selected)
        options = "".join(
            f'<option value="{_e(choice)}"{" selected" if choice == value else ""}>{_e(choice or "repository default")}</option>'
            for choice in choices
        )
        if error is not None and value not in choices:
            options += f'<option value="{_e(value)}" selected>{_e(value)} (invalid)</option>'
        return options

    # Number inputs discard malformed text in the browser. A rejected submission
    # uses a labeled text input so the operator can inspect and correct it.
    number_attributes = (
        'type="text" inputmode="numeric"' if error is not None else 'type="number" min="1"'
    )
    fields = []
    for key, choices, default in (
        ("mode", ("automatic", "approval"), "automatic"),
        ("model_preset", ("existing", "volume", "high-confidence"), "existing"),
        ("delivery_profile", ("", "prototype", "core", "hardening"), ""),
        ("workflow", ("existing", "diagnosis"), "existing"),
        ("test_design", ("false", "true"), "false"),
    ):
        selected = settings.get(key) if settings.get(key) is not None else default
        options = options_for(
            choices, str(selected).lower() if str(selected).lower() in choices else selected
        )
        fields.append(
            f'<label>{_e(key.replace("_", " "))} <select name="{key}">{options}</select></label> '
        )
    choices = ("inherit", "disabled", "read-only", "isolated-write")
    selected = settings.get("delegation_mode", "inherit")
    options = options_for(choices, selected)
    fields.append(f'<label>Delegation <select name="delegation_mode">{options}</select></label> ')
    for key in ("max_active_agents", "max_children_per_parent", "max_delegation_depth"):
        fields.append(
            f'<label>{_e(key.replace("_", " "))} <input name="{key}" {number_attributes} value="{_e(settings.get(key, ""))}" placeholder="inherited"></label> '
        )
    if "/projects/" in action:
        options = options_for(("manual", "automatic"), settings.get("certification_mode", "manual"))
        fields.append(
            f'<label>Certification <select name="certification_mode">{options}</select></label> '
        )
        fields.append(
            f'<label>Certification configuration <input name="certification_config" value="{_e(settings.get("certification_config", ""))}"></label> '
        )
        fields.append(
            f'<label>Concurrency <input name="concurrency" {number_attributes} value="{_e(concurrency if concurrency is not None else "")}" placeholder="inherited"></label> '
        )
        options = options_for(("shared", "per-run"), settings.get("isolation", "shared"))
        fields.append(
            f'<label>Sandbox isolation <select name="isolation">{options}</select></label> '
        )
        fields.append(
            f'<label>Isolation evidence <input name="isolation_measurement" value="{_e(settings.get("isolation_measurement", ""))}" placeholder="manifest path"></label> '
        )
    notice = (
        f'<p id="settings-error" role="alert" tabindex="-1" autofocus>Settings not saved: {_e(error)}</p>'
        if error is not None
        else ""
    )
    description = ' aria-describedby="settings-error"' if error is not None else ""
    return (
        notice
        + f'<form method="post" action="{_e(action)}"{description}>'
        + '<div class="fields">'
        + "".join(fields)
        + "</div>"
        + "<button>Save settings</button></form>"
    )


def _runtime_status(store: Store, project: str, run_id: str | None = None) -> str:
    from factory.operator_controls import status

    observed = status(store, project, run_id)
    completeness = "complete" if observed["cost_complete"] else "incomplete · known lower bound"
    estimate_prefix = "" if observed["cost_complete"] else "≥ "
    return (
        f"<h2>Runtime status</h2><p>{observed['active_agents']} active agents · "
        f"{observed['queued_children']} queued children · {observed['pending_certifications']} pending/checking certifications</p>"
        f"<p>API-equivalent estimated USD: {estimate_prefix}${observed['api_equivalent_estimate_usd']:.4f} · {completeness}. "
        "These are not Codex account charges. Queued children and certification jobs may overlap.</p>"
        + _runtime_table(
            "Children",
            ("Child", "Parent", "Status"),
            [
                (
                    child["id"],
                    child["parent_id"],
                    "running"
                    if any(
                        agent["invocation_id"] == child["child_id"] and agent["status"] == "active"
                        for agent in observed["agents"]
                    )
                    else child["status"],
                )
                for child in observed["children"]
            ],
        )
        + _runtime_table(
            "Certifications",
            ("Certification", "Status", "Failure"),
            [
                (job["id"], job["status"], job["failure"] or "—")
                for job in observed["certifications"]
            ],
        )
        + f"<details><summary>Effective limits, waiting reasons and accounting details</summary><pre>{_e(json.dumps(observed, indent=2))}</pre></details>"
    )


def _runtime_table(title: str, headings: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    if not rows:
        return f"<h3>{_e(title)}</h3><p>None recorded.</p>"
    return (
        f'<h3>{_e(title)}</h3><div class="scroll" tabindex="0" role="region" aria-label="{_e(title)} evidence"><table><thead><tr>'
        + "".join(f"<th scope='col'>{_e(label)}</th>" for label in headings)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join(f"<td>{_e(value)}</td>" for value in row) + "</tr>" for row in rows
        )
        + "</tbody></table></div>"
    )


def _invocation_cards(invocations: list[dict[str, Any]]) -> str:
    from factory.agent.telemetry import CurrentContext

    cards = []
    for invocation in invocations:
        metadata = invocation["metadata"]
        telemetry = invocation.get("telemetry") or {}
        reading = CurrentContext(**telemetry.get("context", {})).read(now=time.time())
        context = f"{reading.fraction:.0%}" if reading.fraction is not None else "unavailable"
        age = (
            f" · measured {reading.age_seconds:.0f}s ago" if reading.age_seconds is not None else ""
        )
        estimate = telemetry.get("estimate", {})
        usd = estimate.get("usd")
        spend = f"${usd:.4f}" if usd is not None else "unknown"
        if not estimate.get("complete"):
            spend += " · incomplete"
        model = telemetry.get("current_model", metadata.get("model", "unknown"))
        cards.append(
            f'<details data-key="invocation-{_e(invocation["id"])}"><summary>{_e(invocation["role"])} · attempt {invocation["attempt"]} · {_e(model)} · {context} context · {spend}</summary>'
            f"<p>Invocation ID: <code>{_e(invocation['id'])}</code></p>"
            f"<p>{_e(model)} · {_e(metadata.get('effort', 'unknown'))} · {_e(metadata.get('preset', 'existing'))}</p>"
            f"<p>Context: {context} · {_e(reading.status)}{age}</p>"
            f"<p>API-equivalent estimate: {spend}</p>"
            f"<details><summary>Usage and evidence</summary><pre>{_e(json.dumps(invocation, indent=2))}</pre></details></details>"
        )
    return "".join(cards) or "<p>No model invocations recorded.</p>"


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"


def _elapsed_cell(elapsed: float, timeout: int | None) -> str:
    text = _duration(elapsed)
    if not timeout:
        return text
    width = max(0, min(100, int((elapsed / timeout) * 100)))
    cls = "fail" if width >= 100 else ("warn" if width >= 75 else "")
    return (
        f'<span class="{cls}">{text}</span> <span class="bar"><i style="width:{width}%"></i></span>'
    )


def _state_chip(state: str) -> str:
    """Visual semantics only; action eligibility remains with machine/policy."""
    tone = (
        "fail"
        if state in {"failed", "blocked"}
        else "warn"
        if state in {"suspended", "awaiting_human"}
        else "muted"
        if state in {"approved", "cancelled", "complete"}
        else "pass"
    )
    return f'<span class="chip {tone}">{_e(state.replace("_", " ").capitalize())}</span>'


def _board_table(rows: list[console_views.RunRow]) -> str:
    """Five primary scan columns; every retained run signal stays keyboard reachable."""
    if not rows:
        return '<p class="muted">No runs yet.</p>'
    cells = []
    for r in rows:
        badge = (
            f' <span class="chip">{_e(r.badge)}</span>' if r.badge and r.badge != r.state else ""
        )
        title = f"<small>{_e(r.title)}</small>" if r.title else ""
        signals = (
            f'<details data-key="run-{_e(r.run_id)}"><summary>Run signals</summary><dl>'
            f"<dt>Project</dt><dd>{_e(r.project)}</dd>"
            f"<dt>Branch</dt><dd>{_e(r.branch or 'not recorded')}</dd>"
            f"<dt>Cost evidence</dt><dd>API-equivalent USD · {_e(r.spend_status)} · ${r.spend_ceiling:.0f} ceiling</dd>"
            f"<dt>Attempt / rung</dt><dd>{r.attempt} · {_e(r.rung)}</dd>"
            f"<dt>Elapsed / timeout</dt><dd>{_duration(r.elapsed_in_state)} / {_duration(r.timeout_seconds)}</dd>"
            f"<dt>Tokens in / out</dt><dd>{r.tokens_in:,} / {r.tokens_out:,} ({r.tokens_cached:,} cached) · {_e(r.usage_status)}</dd>"
            f"<dt>Heartbeat</dt><dd>{_duration(r.heartbeat_age)}</dd>"
            f"<dt>Context evidence</dt><dd>{_e(r.context_reason or 'Current occupancy observed')} · {_e(r.context_source or 'Source unavailable')} · {_duration(r.context_age_seconds)} ago</dd>"
            f"<dt>Activity</dt><dd>{_e(r.activity or 'not recorded')}</dd></dl>"
            f'<a data-focus-key="settings-{_e(r.run_id)}" href="/settings/runs/{_e(r.ticket)}">Run settings and controls</a>'
            + (
                f'<p><a href="{_e(r.pr_url)}">Pull request</a></p>'
                if r.pr_url
                else "<p>Pull request: not recorded</p>"
            )
            + "</details>"
        )
        cells.append(
            "<tr>"
            f'<td data-label="Ticket"><a data-focus-key="ticket-{_e(r.run_id)}" href="/runs/{_e(r.ticket)}">{_e(r.ticket)}</a>{title}{signals}</td>'
            f'<td data-label="Project"><span class="project-identity" tabindex="0" title="{_e(r.project)}">{_e(r.project)}</span></td>'
            f'<td data-label="State">{_state_chip(r.state)}{badge}'
            + (f"<small>{_e(r.blocked_reason)}</small>" if r.blocked_reason else "")
            + f'</td><td data-label="Context">{_pct_cell(r.context_pct, r.context_reason) if r.context_pct is not None else "Unavailable"}</td>'
            f'<td data-label="Estimate">{_estimate_value(r)}<small>{_e(r.spend_status) if r.spend_status != "complete" else ""}</small></td></tr>'
        )
    return (
        '<table class="board-table"><thead><tr><th>Ticket</th><th>Project</th><th>State</th><th>Context</th><th>Estimate</th></tr></thead><tbody>'
        + "".join(cells)
        + "</tbody></table>"
    )


def _metric(label: str, value: str, scope: str) -> str:
    return f'<div class="stat"><span>{_e(label)}</span><strong>{value}</strong><small>{_e(scope)}</small></div>'


def _usage_cards(rows: list[console_views.RunRow]) -> str:
    observed = [r for r in rows if r.usage_status != "unknown"]
    tokens = f"{sum(r.tokens_in + r.tokens_out for r in observed):,}" if observed else "Unavailable"
    token_status = (
        "complete" if rows and all(r.usage_status == "complete" for r in rows) else "incomplete"
    )
    costs = [r.known_spend_usd for r in rows if r.known_spend_usd is not None]
    complete = bool(rows) and all(r.spend_status == "complete" for r in rows)
    cost = (("" if complete else "≥ ") + f"${sum(costs):.2f}") if costs else "Unavailable"
    return (
        '<div class="stats">'
        + _metric("Open runs", str(len(rows)), "Displayed runs")
        + _metric(
            "Context availability",
            str(sum(r.fresh_context_invocations for r in rows)),
            "Live invocations with fresh context observations",
        )
        + _metric("Cumulative tokens", tokens, f"Displayed runs · {token_status}")
        + _metric(
            "Estimated cost",
            cost,
            "Displayed runs · API-equivalent USD · "
            + ("complete" if complete else "incomplete / lower bound"),
        )
        + "</div>"
    )


def _board_overview(
    rows: list[console_views.RunRow], waiting: dict[str, str], pending: dict[str, str]
) -> str:
    """Render observed holds before the queue; never infer activity or aggregate context."""
    attention = [
        r
        for r in rows
        if r.state in {"blocked", "suspended", "awaiting_human", "failed"}
        or r.run_id in waiting
        or r.run_id in pending
    ]
    counts = [
        (
            "Active invocations",
            sum(r.active_invocations for r in rows),
        ),
        ("Blocked", sum(r.state == "blocked" for r in rows)),
        ("Awaiting invocation approval", len(waiting)),
        ("Pending agent launch", len(pending)),
        ("Approved, unclaimed", sum(r.state == "approved" for r in rows)),
    ]
    summary = (
        '<div class="ops-strip" aria-label="Observed run counts">'
        + "".join(
            f"<div><span>{label}</span><strong>{count}</strong></div>" for label, count in counts
        )
        + "</div>"
    )
    holds = ""
    for index, row in enumerate(attention):
        reason = (
            row.blocked_reason
            or waiting.get(row.run_id)
            or pending.get(row.run_id)
            or "No reason recorded."
        )
        launch_status = (
            "Approval required"
            if row.run_id in waiting
            else "Pending agent launch"
            if row.run_id in pending
            else ""
        )
        destination = (
            f"/settings/runs/{row.ticket}" if row.run_id in waiting else f"/runs/{row.ticket}"
        )
        if index == 3:
            holds += f'<details data-key="more-attention"><summary>Show {len(attention) - 3} more runs needing attention</summary>'
        holds += (
            f'<article class="attention-item"><a data-focus-key="attention-{_e(row.run_id)}" href="{_e(destination)}">{_e(row.ticket)}</a>'
            f"{_state_chip(row.state)}"
            + (f"<p>{_e(launch_status)}</p>" if launch_status else "")
            + f"<p>{_e(reason)}</p>"
            + (
                f'<a class="attention-action" href="{_e(row.pr_url)}">Review pull request →</a>'
                if row.pr_url and row.state == "awaiting_human"
                else f'<a class="attention-action" href="{_e(destination)}">{"Review approval" if row.run_id in waiting else "Inspect run"} →</a>'
            )
            + "</article>"
        )
    if len(attention) > 3:
        holds += "</details>"
    if not holds:
        holds = '<p class="muted">No blocked, suspended, failed or pending agent runs.</p>'
    return (
        summary
        + _usage_cards(rows)
        + f'<div class="operations-grid{"" if attention else " no-attention"}"><section class="panel attention" '
        'aria-labelledby="attention-heading"><h2 id="attention-heading">Needs attention</h2>'
        + f'<p class="muted">{len(attention)} runs need attention</p>'
        + holds
        + '</section><section class="panel" aria-labelledby="queue-heading">'
        '<h2 id="queue-heading">Work in progress</h2><p class="table-hint muted">Current context · estimates in API-equivalent USD. Expand Run signals for full evidence and controls.</p>'
        + _board_table(rows)
        + "</section></div>"
    )


#: The controls §18.5 View 5 names, as (slug, label). **No Merge**: it is not omitted by
#: accident, and `_CONTROLS` in `cli.py` refuses the slug even if a form invented one.
_CONTROL_BUTTONS: tuple[tuple[str, str], ...] = (
    ("suspend", "Suspend"),
    ("resume", "Resume"),
    ("resume-planning", "Resume from planning"),
    ("cancel", "Cancel"),
    ("retry", "Retry now"),
)


def _controls(ticket: str) -> str:
    buttons = "".join(
        f'<form class="control" method="post" action="/runs/{_e(ticket)}/{slug}">'
        f"<button type=submit>{_e(label)}</button></form> "
        for slug, label in _CONTROL_BUTTONS
    )
    return _render("controls.html", buttons=buttons)


# --------------------------------------------------------------------------------
# View 6 — the run timeline (agent status + runtime waterfall). The data comes from
# `console_views.run_timeline`; everything below is rendering. The waterfall positions
# blocks by `left = (start - t0)/span`, `width = duration/span` — the same transition `at`
# values the run-detail timeline table shows, so the two views cannot disagree.
# --------------------------------------------------------------------------------

_WF_LANES: tuple[str, ...] = ("engineer", "planner", "builder", "reviewer", "code")


def _timeline_head(r: console_views.RunRow, tl: console_views.RunTimeline) -> str:
    """Band 1 — the summary strip. The same `RunRow` fields the run-detail head renders, so
    the page's header and `/runs/{ticket}`'s header are one producer apart."""
    badge = f' <span class="chip">{_e(r.badge)}</span>' if r.badge and r.badge != r.state else ""
    head = (
        f"<h1>{_e(r.ticket)} <span class='muted'>{_e(r.project)}</span></h1>"
        f'<p class="sub">{_e(r.state)}{badge} · attempt {r.attempt} · rung {_e(r.rung)} · '
        f"elapsed {_duration(tl.elapsed_total_s)} · context "
        f"{_pct_cell(r.context_pct, r.context_reason)} · "
        f"spend {_estimate_cell(r)}</p>"
    )
    if r.pr_url:
        head += (
            f'<p>Pull request: <a href="{_e(r.pr_url)}" target="_blank" '
            f'rel="noreferrer">{_e(r.pr_url)}</a> '
            '<span class="muted">— merging happens on GitHub.</span></p>'
        )
    if r.blocked_reason:
        head += f'<p class="fail">blocked: {_e(r.blocked_reason)}</p>'
    return head


def _cards_html(cards: list[console_views.AgentCard]) -> str:
    """Band 2 — one card per agent role. The left stripe is the lane colour; the status
    pill is the Gmail-MCP 'authed' analogue. Context is shown for the live role only;
    a done/idle role shows the reason it has no number, the same hide-with-reason rule the
    board holds for the run-level percentage."""
    if not cards:
        return ""
    items: list[str] = []
    for c in cards:
        if c.status == "live":
            ctx = (
                '<div class="cardctx"><span class="k">context</span>'
                f"{_pct_cell(c.context_pct, c.context_reason)}</div>"
            )
            hb = f'<div class="cardrow"><span class="k">heartbeat</span> {_duration(c.heartbeat_age)}</div>'
            act_label = "now"
        else:
            ctx = (
                '<div class="cardctx muted"><span class="k">context</span> '
                f"{_e(c.context_reason)}</div>"
            )
            hb = ""
            act_label = "last"
        act = (
            '<div class="cardrow"><span class="k">' + act_label + "</span> "
            f'<span class="v">{_e(c.activity) if c.activity else "—"}</span></div>'
        )
        items.append(
            f'<div class="card lane-{c.role}">'
            f'<div class="cardhead"><span><b>{_e(c.role)}</b> '
            f'<span class="muted">{_e(c.model)} · {_e(c.effort)}</span></span>'
            f'<span class="status st-{c.status}">{_e(c.status)}</span></div>'
            f"{ctx}{act}{hb}</div>"
        )
    return '<div class="cards">' + "".join(items) + "</div>"


def _waterfall_html(blocks: list[console_views.WaterfallBlock]) -> str:
    """Band 3 — the swim-lane waterfall. Block widths are runtime (from transition
    timestamps); colour is lane identity. A block under ~6% of the span is too thin to
    label, so it shows a `title` tooltip and no inline text — the axis never lets an
    18-second 'label' block look as long as a 12-minute implement."""
    if not blocks:
        return '<p class="muted">No transitions yet.</p>'
    t0 = blocks[0].start
    span = max(1, blocks[-1].end - t0)

    def pos(b: console_views.WaterfallBlock) -> tuple[float, float]:
        left = (b.start - t0) / span * 100.0
        width = (b.end - b.start) / span * 100.0
        return max(0.0, left), max(0.0, width)

    ticks = "".join(
        f'<span class="wftick" style="left:{f * 100:.1f}%">{_duration(f * span)}</span>'
        for f in (0.0, 0.25, 0.5, 0.75, 1.0)
    )
    axis = f'<div class="wfaxis">{ticks}<span class="wfnow" style="left:100%"></span></div>'

    # A marker state (resumable/blocked/…) has lane None; it renders in the lane of the
    # block that entered it, so a dead attempt's 'resumable' sits in the builder row.
    rows: list[str] = []
    render_lane = "code"
    lane_blocks: dict[str, list[console_views.WaterfallBlock]] = {ln: [] for ln in _WF_LANES}
    for b in blocks:
        rl = b.lane or render_lane
        if rl not in lane_blocks:
            rl = "code"
        lane_blocks[rl].append(b)
        render_lane = rl
    for ln in _WF_LANES:
        cells: list[str] = []
        for b in lane_blocks[ln]:
            left, width = pos(b)
            classes = f"wfblk lane-{b.lane or ln}"
            if b.dead:
                classes += " dead"
            if b.live:
                classes += " live"
            title = (
                f"{_e(b.state)} · {_duration(b.duration_s)}{' — dead attempt' if b.dead else ''}"
            )
            if width < 6:
                cells.append(
                    f'<span class="{classes}" style="left:{left:.2f}%;'
                    f'width:max({width:.2f}%,3px)" title="{title}"></span>'
                )
            else:
                cells.append(
                    f'<span class="{classes}" style="left:{left:.2f}%;width:{width:.2f}%" '
                    f'title="{title}"><span class="wflabel">{_e(b.state)}</span>'
                    f'<span class="wfdur">{_duration(b.duration_s)}</span></span>'
                )
        rows.append(
            f'<div class="wflane"><span class="wflab"><i class="wfsw lane-{ln}"></i>'
            f"{_e(ln)}</span>{''.join(cells)}</div>"
        )
    legend = (
        '<div class="wflegend">'
        + "".join(f'<span><i class="wfsw lane-{ln}"></i>{_e(ln)}</span>' for ln in _WF_LANES)
        + '<span><i class="wfsw dead"></i>dead attempt</span>'
        + "</div>"
    )
    return f'<div class="waterfall" tabindex="0" role="region" data-scroll-key="waterfall" aria-label="Runtime waterfall">{axis}{"".join(rows)}{legend}</div>'


def _tool_calls_html(calls: list[console_views.ToolCallView]) -> str:
    """Band 4 — the per-tool-call drill-down. The `#` ordinal, type, summary and exit code
    are always shown; a `dur` column appears only when the Phase 2 `events.timings.jsonl`
    sidecar defended a duration for at least one call — so a run with no sidecar keeps the
    Phase 1 look (no column of em dashes), and a run with one gains the column honestly."""
    if not calls:
        return (
            '<h2>Tool calls <span class="muted">this attempt</span></h2>'
            '<p class="muted">No completed calls yet.</p>'
        )
    has_dur = any(c.duration_s is not None for c in calls)
    rows = "".join(
        f"<tr><td class='mono'>{c.index}</td>"
        f'<td><span class="ttype t-{_e(c.kind)}">{_e(c.kind)}</span></td>'
        f'<td class="wrap">{_e(c.summary)}</td>'
        + (f'<td class="mono">{_duration(c.duration_s)}</td>' if has_dur else "")
        + f'<td class="mono exit '
        f'{"ok" if c.exit_code == 0 else "bad" if c.exit_code is not None else "na"}">'
        f"{_e(c.exit_code) if c.exit_code is not None else '—'}</td></tr>"
        for c in calls
    )
    head = (
        '<h2>Tool calls <span class="muted">this attempt</span></h2>'
        '<div class="scroll" tabindex="0" role="region" aria-label="Scrollable evidence table"><table><thead><tr><th>#</th><th>type</th>'
        "<th>command / summary</th>"
        + ("<th>dur</th>" if has_dur else "")
        + "<th>exit</th></tr></thead><tbody>"
    )
    return head + rows + "</tbody></table></div>"


def _timeline_html(tl: console_views.RunTimeline, ticket: str, transitions: str = "") -> str:
    """The full inner timeline, wrapped in `#timeline` so the SSE stream can swap it."""
    return (
        '<div id="timeline">'
        + _timeline_head(tl.row, tl)
        + '<section class="panel"><h2>Agent activity</h2>'
        + (_cards_html(tl.cards) or "<p>No agent activity recorded.</p>")
        + '</section><section class="panel"><h2 class="wf-title">Runtime · swim-lane waterfall</h2>'
        + _waterfall_html(tl.blocks)
        + '</section><section class="panel">'
        + _tool_calls_html(tl.tool_calls)
        + '</section><section class="panel"><h2>Execution transitions</h2>'
        + transitions
        + "</section></div>"
    )


def _transitions_html(store: Store, run_id: str) -> str:
    rows = store.transitions(run_id)
    return _runtime_table(
        "Transition history",
        ("At", "Transition", "Actor", "Rule"),
        [
            (
                time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(row["at"])),
                f"{row['from_state']} → {row['to_state']}",
                str(row["actor"]),
                str(row["rule"] or "not recorded"),
            )
            for row in rows
        ],
    )


def create_app(
    home: Path,
    registry: Registry | None = None,
    routing: Routing | None = None,
    store: Store | None = None,
    linear: LinearClient | None = None,
    context_factory: Any | None = None,
) -> FastAPI:
    """The console app. Config is re-read per request unless injected.

    Injection exists for the tests (§21.3: the whole thing runs in-process against fakes);
    production passes nothing and each request re-reads `projects.toml`/`models.toml`, so
    a config edit takes effect without a restart — the same hot-reload property the tick has.
    `context_factory` threads the same seam into the controls, so a POST can be driven
    against a `FakeSandbox` rather than needing a Docker login.
    """
    app = FastAPI(title="factory console", docs_url=None, redoc_url=None)
    app.add_middleware(
        BundleMiddleware, bundle=RenderBundle.load(TEMPLATES, code_namespace=globals())
    )

    def _cfg() -> tuple[Registry, Routing, Store, LinearClient]:
        """Resolve config and store for one request (or one SSE tick).

        A **fresh `Store` per call**, and that is load-bearing rather than wasteful: a
        `sqlite3.Connection` may only be used on the thread that created it, and this app
        is served on a thread pool — uvicorn runs sync endpoints in a worker, and the SSE
        endpoints push their blocking reads into one deliberately. Opening the connection
        where it is used is what keeps that legal without loosening `Store`'s own
        single-connection guarantee, which the daemon's single-writer rule depends on.

        An injected store is honoured only when it belongs to this thread; otherwise a
        connection to the same file is opened here. That keeps the tests' in-process
        wiring intact (§21.3) while behaving correctly under a real server.

        Re-reading `projects.toml`/`models.toml` per call gives the console the same
        hot-reload property the tick has — a config edit takes effect without a restart.
        """
        from factory.cli import _open_store

        reg = registry or load_registry(home / "config" / "projects.toml")
        rt = routing or load_routing(home / "config" / "models.toml")
        st = _store_for_this_thread(store, home, _open_store)
        ln = linear or LinearClient()
        return reg, rt, st, ln

    def _board_content(reg: Registry, rt: Routing, st: Store) -> str:
        rows = console_views.runs_board(home, reg, rt, st)
        waiting = {}
        pending = {}
        for row in rows:
            effective = st.runtime.effective(row.project, row.run_id)
            invocation = effective.get("waiting_invocation")
            approved = st.runtime.settings("run", row.run_id).get("approved_invocation")
            if (
                invocation
                and effective.get("mode", "automatic") == "approval"
                and approved != invocation
            ):
                waiting[row.run_id] = str(invocation)
            elif invocation:
                pending[row.run_id] = str(invocation)
        return _board_overview(rows, waiting, pending)

    @app.get("/", response_class=HTMLResponse)
    def board() -> HTMLResponse:
        reg, rt, st, _ = _cfg()
        body = _render("board.html", table=_board_content(reg, rt, st))
        return HTMLResponse(_page("runs", body))

    @app.get("/projects", response_class=HTMLResponse)
    def projects() -> HTMLResponse:
        reg, _, st, _ = _cfg()
        from factory.operator_controls import status

        inventory = []
        for name, project in reg.projects.items():
            settings = st.runtime.settings("project", name)
            explicit = settings.get("concurrency", project.concurrency_per_project)
            occupied = st.runtime.db.execute(
                "SELECT COUNT(*) FROM project_slots WHERE project=?", (name,)
            ).fetchone()[0]
            observed = status(st, name)
            try:
                config = json.loads((project.path / "harness.config.json").read_text())
                profile = (
                    settings.get("delivery_profile")
                    or config.get("delivery", {}).get("default")
                    or "Not declared"
                )
            except (OSError, ValueError):
                profile = "Unavailable"
            inventory.append(
                f'<tr><td data-label="Project"><a href="#project-{_e(name)}">{_e(name)}</a></td><td data-label="Run slots">{occupied} / {explicit or reg.concurrency_for(project)}</td><td data-label="Delivery profile">{_e(profile)}</td><td data-label="Agent slots">{observed["active_agents"]} / {observed["effective"]["max_active_agents"]}</td><td data-label="Waiting">{observed["queued_children"]} children · {observed["pending_certifications"]} certifications<small>May overlap</small></td></tr>'
            )
        body = (
            '<h1>Projects</h1><p class="sub">Registered projects, capacity and defaults for future work.</p><section class="panel" id="project-inventory"><h2>Registered projects</h2><div class="scroll" tabindex="0" role="region" aria-label="Project inventory"><table class="inventory-table project-inventory"><thead><tr><th>Project</th><th>Run slots</th><th>Delivery profile</th><th>Agent slots</th><th>Waiting</th></tr></thead><tbody>'
            + "".join(inventory)
            + '</tbody></table></div></section><section id="project-defaults"><h2>Project defaults</h2>'
        )
        for name, project in reg.projects.items():
            settings = st.runtime.settings("project", name)
            explicit = settings.get("concurrency", project.concurrency_per_project)
            limit = explicit or reg.concurrency_for(project)
            runs = [r for r in st.all_runs() if r.project == name]
            occupied = st.runtime.db.execute(
                "SELECT COUNT(*) FROM project_slots WHERE project=?", (name,)
            ).fetchone()[0]
            queued = sum(r.state is State.APPROVED for r in runs)
            body += f'<details class="panel project-default" id="project-{_e(name)}"><summary>{_e(name)} · defaults and runtime evidence</summary><p>{occupied} / {limit} slots · {queued} queued · '
            body += f"{'inherited' if explicit is None else 'explicit'} concurrency</p>"
            try:
                config = json.loads((project.path / "harness.config.json").read_text())
                delivery = config.get("delivery", {})
                profile = settings.get("delivery_profile") or delivery.get("default")
                declared = delivery.get("profiles", {}).get(profile, {})
                body += f"<p>Effective delivery profile: {_e(profile or 'not declared')}</p>"
                deferrals = declared.get("deferrals", [])
                if deferrals:
                    body += (
                        "<ul>"
                        + "".join(
                            f"<li>{_e(item.get('requirement', ''))}: {_e(item.get('rationale', ''))} "
                            f"— revisit: {_e(item.get('revisit', ''))}</li>"
                            for item in deferrals
                        )
                        + "</ul>"
                    )
                else:
                    body += "<p>No project deferrals declared.</p>"
            except (OSError, ValueError):
                body += "<p>Delivery policy unavailable.</p>"
            body += _settings_form(f"/settings/projects/{name}", settings, concurrency=explicit)
            body += (
                "<details><summary>Runtime and certification history</summary>"
                + _runtime_status(st, name)
                + "</details></details>"
            )
        return HTMLResponse(_page("projects", body + "</section>"))

    @app.get("/settings/runs/{ticket}", response_class=HTMLResponse)
    def run_settings(ticket: str) -> HTMLResponse:
        _, _, st, _ = _cfg()
        run = st.run_by_ticket(ticket.upper())
        if run is None:
            return HTMLResponse("Run not found", status_code=404)
        settings = st.runtime.effective(run.project, run.id)
        policy = st.runtime.policy(run.id)
        if policy:
            settings["delivery_profile"] = policy["profile"]
        body = f"<h1>{_e(run.linear_id)} · Run settings</h1>"
        waiting = settings.get("waiting_invocation") or ""
        approved = st.runtime.settings("run", run.id).get("approved_invocation")
        admission = (
            "Approval required"
            if waiting and settings.get("mode") == "approval" and approved != waiting
            else "Pending agent launch"
            if waiting
            else "No invocation awaiting admission"
        )
        body += f'<section class="panel" id="invocation-admission"><h2>Invocation admission</h2><p>{admission}</p>'
        body += f'<form method="post" action="/settings/approve/{_e(run.linear_id)}"><label>Next invocation ID <input name="invocation" value="{_e(waiting)}" required></label><button>Approve next attempt</button></form></section>'
        body += '<section class="panel" id="effective-settings"><h2>Effective settings</h2>'
        form_settings = settings | {
            key: st.runtime.settings("run", run.id).get(key)
            for key in (
                "delegation_mode",
                "max_active_agents",
                "max_children_per_parent",
                "max_delegation_depth",
            )
        }
        form_settings = {key: value for key, value in form_settings.items() if value is not None}
        body += (
            '<h2>Effective limits</h2><dl class="effective-limits">'
            + "".join(
                f"<div><dt>{label}</dt><dd>{_e(settings.get(key, default))}</dd></div>"
                for key, label, default in (
                    ("delegation_mode", "Delegation", "disabled"),
                    ("max_active_agents", "Active agent limit", 8),
                    ("max_children_per_parent", "Children per parent", 2),
                    ("max_delegation_depth", "Delegation depth", 1),
                )
            )
            + "</dl>"
        )
        body += _settings_form(f"/settings/runs/{run.linear_id}", form_settings)
        body += '</section><section class="panel"><h2>Effective delivery policy</h2>'
        if policy:
            body += f"<p>Frozen profile: {_e(policy.get('profile', 'not recorded'))}</p>"
            body += f"<details><summary>Frozen policy evidence</summary><pre>{_e(json.dumps(policy, indent=2))}</pre></details>"
        else:
            body += "<p>No frozen policy recorded.</p>"
        body += "<p>Replacing a policy requires an explicit operator action and new verification and review.</p>"
        body += f'<form method="post" action="/settings/replace-policy/{_e(run.linear_id)}"><label>Replacement profile <select name="profile"><option>prototype</option><option>core</option><option>hardening</option></select></label><button>Replace paused run policy</button></form>'
        body += f'<p><a href="/runs/{_e(run.linear_id)}">Run and Suspend controls</a></p></section>'
        invocations = st.runtime.invocations(run.id)
        body += f'<section class="panel"><h2>Invocations · {len(invocations)}</h2><p>API-equivalent estimated USD. These are not Codex account charges.</p>'
        body += _invocation_cards(invocations[-3:])
        if len(invocations) > 3:
            body += (
                f'<details data-key="invocation-history"><summary>Earlier invocations ({len(invocations) - 3})</summary>'
                + _invocation_cards(invocations[:-3])
                + "</details>"
            )
        body += (
            '</section><section class="panel"><details><summary>Runtime and certification history</summary>'
            + _runtime_status(st, run.project, run.id)
            + "</details></section>"
        )
        return HTMLResponse(_page("run controls", body, ticket=run.linear_id, view="settings"))

    @app.post("/settings/{scope}/{owner}")
    async def settings_write(scope: str, owner: str, request: Request) -> HTMLResponse:
        from factory import operator_controls

        reg, _, st, _ = _cfg()
        form = _parse_form(await request.body())
        run = (
            st.run_by_ticket(owner.upper())
            if scope in {"runs", "approve", "replace-policy"}
            else None
        )
        try:
            if scope == "replace-policy":
                from factory import authority
                from factory.harness import load_harness_config
                from factory.isolation import project_for_run

                if run is None or run.state not in {
                    State.SUSPENDED,
                    State.BLOCKED,
                    State.AWAITING_HUMAN,
                }:
                    raise Blocked(
                        "policy-replacement-needs-paused-run",
                        "Suspend the run before replacing its policy",
                    )
                if not st.acquire_lease(run.id, ttl_seconds=300):
                    raise Blocked("run-leased", "The run is owned by another process")
                try:
                    project = project_for_run(reg.resolve(run.linear_id), run, st)
                    load_harness_config(project.path)
                    snapshot_ctx = authority.SnapshotContext(home, project, run, st)
                    authority.snapshot(snapshot_ctx, profile=form["profile"], replace=True)
                finally:
                    st.release_lease(run.id)
            elif scope == "approve" and run:
                st.runtime.approve(run.id, str(form["invocation"]))
            else:
                target = run.id if run else owner
                if (
                    scope not in {"runs", "projects"}
                    or (scope == "runs" and not run)
                    or (scope == "projects" and owner not in reg.projects)
                ):
                    raise ValueError("Unknown run or project")
                changes = {key: value for key, value in form.items() if value != ""}
                if "delivery_profile" in form:
                    changes["delivery_profile"] = form["delivery_profile"] or None
                if "concurrency" in form:
                    changes["concurrency"] = (
                        int(form["concurrency"]) if form["concurrency"] else None
                    )
                for key in ("max_active_agents", "max_children_per_parent", "max_delegation_depth"):
                    if key in form:
                        changes[key] = int(form[key]) if form[key] else None
                if changes.get("delegation_mode") == "inherit":
                    changes["delegation_mode"] = None
                if "test_design" in changes:
                    changes["test_design"] = changes["test_design"] == "true"
                operator_controls.configure(
                    st, "run" if run else "project", target, changes, registry=reg
                )
        except (ValueError, KeyError, Blocked) as exc:
            if (scope == "runs" and run) or (scope == "projects" and owner in reg.projects):
                body = _settings_form(
                    f"/settings/{scope}/{owner}",
                    form,
                    concurrency=form.get("concurrency"),
                    error=str(exc),
                )
            else:
                body = f"<p>{_e(str(exc))}</p>"
            return HTMLResponse(_page("settings refused", body), status_code=409)
        return HTMLResponse(
            _page(
                "settings saved",
                '<p>Settings saved. Current work continues. <a href="/projects">Projects</a></p>',
            )
        )

    @app.get("/sse/board")
    async def board_stream() -> StreamingResponse:
        """The live board, as server-sent events. Re-reads the store each tick — nothing is
        held between them, the same property §4.2 requires of the tick itself."""

        def read_board() -> str:
            """Blocking: it opens SQLite and stats attempt directories. Off the loop."""
            reg, rt, st, _ = _cfg()
            return _board_content(reg, rt, st)

        async def events() -> Any:
            while True:
                payload = json.dumps({"html": await asyncio.to_thread(read_board)})
                yield f"data: {payload}\n\n"
                await asyncio.sleep(SSE_INTERVAL_SECONDS)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/runs/{ticket}", response_class=HTMLResponse)
    def run_detail(ticket: str) -> HTMLResponse:
        reg, rt, st, _ = _cfg()
        run = st.run_by_ticket(ticket.upper())
        if run is None:
            return HTMLResponse(
                _page(ticket, f"<h1>{_e(ticket)}</h1><p>No run.</p>"), status_code=404
            )
        detail = console_views.run_detail(home, reg, rt, st, run)
        r = detail.row

        head = f"<h1>{_e(r.ticket)} <span class='muted'>{_e(r.project)}</span></h1>"
        if r.title:
            head += f'<p class="sub">{_e(r.title)}</p>'
        if detail.pr_url:
            head += (
                f'<p>Pull request: <a href="{_e(detail.pr_url)}" target="_blank" '
                f'rel="noreferrer">{_e(detail.pr_url)}</a> '
                '<span class="muted">— merging happens on GitHub.</span></p>'
            )
        if detail.blocked_reason:
            head += f'<p class="fail">blocked: {_e(detail.blocked_reason)}</p>'

        timeline = "".join(
            "<tr>"
            f"<td>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t.at))}</td>"
            f"<td>{_e(t.from_state)} → {_e(t.to_state)}</td>"
            f"<td>{_e(t.actor)}</td>"
            f'<td class="wrap">{_e(t.rule) if t.rule else ""}</td>'
            "</tr>"
            for t in detail.transitions
        )
        timeline_html = (
            '<h2>Transitions</h2><div class="scroll" tabindex="0" role="region" aria-label="Scrollable evidence table"><table><thead><tr><th>at</th>'
            "<th>hop</th><th>actor</th><th>rule</th></tr></thead><tbody>"
            + timeline
            + "</tbody></table></div>"
        )

        gates_html = "<h2>Verification</h2><p>No gate report recorded.</p>"
        if detail.gates:
            gate_rows = "".join(
                "<tr>"
                f'<td><span class="{"pass" if g.status == "pass" else "fail" if g.status == "fail" else "muted"}">'
                f"{_e(g.status)}</span></td>"
                f"<td>{_e(g.name)}</td>"
                f'<td class="wrap muted">{_e(g.caveat) if g.caveat else ""}</td>'
                "</tr>"
                for g in detail.gates
            )
            verdict_cls = "pass" if detail.gate_verdict == "pass" else "fail"
            gates_html = (
                f'<h2>Verification · gate report — <span class="{verdict_cls}">{_e(detail.gate_verdict)}</span></h2>'
                '<div class="scroll" tabindex="0" role="region" aria-label="Scrollable evidence table"><table><thead><tr><th>status</th><th>gate</th>'
                "<th>caveat</th></tr></thead><tbody>" + gate_rows + "</tbody></table></div>"
            )

        review_html = "<h2>Review</h2><p>No review evidence recorded.</p>"
        if detail.review_findings or detail.review_tier2:
            order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            ranked = sorted(
                detail.review_findings, key=lambda f: order.get((f.severity or "").lower(), 9)
            )
            finding_rows = "".join(
                "<tr>"
                f'<td><span class="chip">{_e(f.severity or "?")}</span></td>'
                f"<td>{_e(f.file or '')}{f':{f.line}' if f.line else ''}</td>"
                f'<td class="wrap">{_e(f.summary or "")}</td>'
                "</tr>"
                for f in ranked
            )
            review_html = (
                f"<h2>Review <span class='muted'>tier 2: {_e(detail.review_tier2)}</span></h2>"
                + (
                    '<div class="scroll" tabindex="0" role="region" aria-label="Scrollable evidence table"><table><thead><tr><th>severity</th><th>where</th>'
                    "<th>finding</th></tr></thead><tbody>" + finding_rows + "</tbody></table></div>"
                    if ranked
                    else '<p class="muted">No findings.</p>'
                )
            )

        artifacts_html = "<h2>Retained artifacts</h2><p>No artifacts recorded.</p>"
        if detail.artifacts:
            items = "".join(
                f"<tr><td class='muted'><code>{_e(a.sha256 or 'not recorded')}</code></td><td>{_e(a.name)}<small>{_e(a.path)}</small></td></tr>"
                for a in detail.artifacts
            )
            artifacts_html = (
                '<h2>Retained artifacts</h2><div class="scroll" tabindex="0" role="region" aria-label="Scrollable evidence table"><table><thead><tr><th>sha256</th>'
                "<th>file</th></tr></thead><tbody>" + items + "</tbody></table></div>"
            )

        tail_html = "<h2>Live tail</h2><p>No event stream recorded.</p>"
        if detail.events_path:
            tail_html = (
                '<h2>Live tail</h2><label class="follow-control"><input type="checkbox" id="tail-follow"> Follow new events</label>'
                f'<pre id="tail" class="muted" tabindex="0" role="region" aria-label="Recent event log">reading {_e(detail.events_path)}…</pre>'
                "<script>"
                f"const t=new EventSource('/sse/tail/{_e(r.ticket)}');"
                "t.onmessage=e=>{const p=document.getElementById('tail');"
                "const top=p.scrollTop;p.textContent=JSON.parse(e.data).text;p.scrollTop=document.getElementById('tail-follow').checked?p.scrollHeight:top;};"
                "</script>"
            )

        body = _render(
            "run_detail.html",
            head=head,
            controls=_controls(r.ticket),
            metrics='<div class="stats">'
            + _metric("State / attempt", _e(r.state), f"Attempt {r.attempt} · {r.rung}")
            + _metric(
                "Current context",
                _pct_cell(r.context_pct, r.context_reason)
                if r.context_pct is not None
                else "Unavailable",
                f"{r.context_source or 'Source unavailable'} · observed {_duration(r.context_age_seconds)} ago · {r.context_reason or 'current live invocation'}",
            )
            + _metric(
                "Cumulative tokens",
                f"{r.tokens_in + r.tokens_out:,}" if r.usage_status != "unknown" else "Unavailable",
                f"Run total · {r.usage_status} · {r.tokens_cached:,} cached input",
            )
            + _metric(
                "Estimated run cost",
                _estimate_value(r),
                f"API-equivalent USD · {r.spend_status} · ${r.spend_ceiling:.0f} ceiling",
            )
            + "</div>",
            current=f"<h2>Current attempt</h2><dl><dt>Branch</dt><dd>{_e(r.branch or 'not recorded')}</dd><dt>Activity</dt><dd>{_e(r.activity or 'No activity recorded')}</dd><dt>Time in state / timeout</dt><dd>{_duration(r.elapsed_in_state)} / {_duration(r.timeout_seconds)}</dd><dt>Heartbeat age</dt><dd>{_duration(r.heartbeat_age)}</dd></dl>",
            timeline=timeline_html,
            gates=gates_html,
            review=review_html,
            artifacts=artifacts_html,
            tail=tail_html,
        )
        return HTMLResponse(_page(r.ticket, body, ticket=r.ticket, view="detail"))

    @app.get("/runs/{ticket}/timeline", response_class=HTMLResponse)
    def run_timeline_view(ticket: str) -> HTMLResponse:
        """View 6 — the run timeline. Reached one click deeper than the run detail (the
        detail page's head links here), matching the depth of the evidence views."""
        reg, rt, st, _ = _cfg()
        run = st.run_by_ticket(ticket.upper())
        if run is None:
            return HTMLResponse(
                _page(ticket, f"<h1>{_e(ticket)}</h1><p>No run.</p>"), status_code=404
            )
        tl = console_views.run_timeline(home, reg, rt, st, run)
        body = _render(
            "run_timeline.html",
            timeline=_timeline_html(tl, ticket, _transitions_html(st, run.id)),
            ticket=_e(ticket.upper()),
        )
        return HTMLResponse(
            _page(f"{ticket.upper()} timeline", body, ticket=ticket.upper(), view="timeline")
        )

    @app.get("/sse/timeline/{ticket}")
    async def timeline_stream(ticket: str) -> StreamingResponse:
        """The live timeline, as server-sent events. Re-reads the store each tick and
        re-renders the whole inner timeline — the waterfall blocks, the agent cards and the
        tool-call table all advance on the same poll, the same `board_stream` pattern. No
        new transport; the page swaps `#timeline` on each message."""

        def read_timeline() -> str:
            reg, rt, st, _ = _cfg()
            run = st.run_by_ticket(ticket.upper())
            if run is None:
                return ""
            tl = console_views.run_timeline(home, reg, rt, st, run)
            return _timeline_html(tl, ticket, _transitions_html(st, run.id))

        async def events() -> Any:
            while True:
                payload = json.dumps({"html": await asyncio.to_thread(read_timeline)})
                yield f"data: {payload}\n\n"
                await asyncio.sleep(SSE_INTERVAL_SECONDS)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/sse/tail/{ticket}")
    async def tail_stream(ticket: str) -> StreamingResponse:
        """The `events.jsonl` live tail (§18.5 View 3). The last lines only — a full
        transcript belongs in `factory logs`, not in a page."""

        def read_tail() -> str:
            """The blocking half, run in a worker thread. SQLite and the filesystem are
            both blocking, and doing either on the event loop would stall every other
            connection the console is serving.

            Config and store are resolved *inside* this function, not captured from the
            request: a `sqlite3.Connection` may only be used on the thread that created it,
            so a connection opened on the request thread and read here raises
            `ProgrammingError`. Re-resolving per tick also means the stream picks up a run
            that moved state, which is the whole point of a live tail.
            """
            reg, rt, st, _ = _cfg()
            run = st.run_by_ticket(ticket.upper())
            if run is None:
                return f"no run for {ticket.upper()}"
            detail = console_views.run_detail(home, reg, rt, st, run)
            if not detail.events_path:
                return "no event stream yet"
            path = Path(detail.events_path)
            if not path.exists():
                return f"no event stream at {path}"
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(lines[-TAIL_LINES:])

        async def events() -> Any:
            while True:
                text = await asyncio.to_thread(read_tail)
                yield f"data: {json.dumps({'text': text})}\n\n"
                await asyncio.sleep(SSE_INTERVAL_SECONDS)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/runs/{ticket}/{action}")
    def control(ticket: str, action: str) -> RedirectResponse:
        """§18.5 View 5. Straight through `cli.dispatch_control`, which owns the lease,
        the policy gate and the `actor="human"` transition — the console adds no second
        opinion about what a control is allowed to do."""
        from factory.cli import dispatch_control

        reg, rt, st, ln = _cfg()
        run = st.run_by_ticket(ticket.upper())
        if run is None:
            return RedirectResponse("/", status_code=303)
        # The context factory is handed the thread-local store, not the one it closed
        # over: `_cfg` may have reconnected for this thread, and a `Context` carrying the
        # other thread's `sqlite3.Connection` raises on its first query.
        factory = context_factory
        if factory is not None:
            inner = factory
            factory = lambda r: replace(inner(r), store=st)  # noqa: E731
        dispatch_control(action, home, reg, rt, st, ln, run, context_factory=factory)
        return RedirectResponse(f"/runs/{ticket.upper()}", status_code=303)

    @app.get("/runtimes", response_class=HTMLResponse)
    def runtimes_view() -> HTMLResponse:
        from factory.cli import _sbx_inventory

        reg, _, st, _ = _cfg()
        inventory = _sbx_inventory()
        rows = console_views.runtimes(inventory.sandboxes, st, registry=reg)
        if inventory.status != "available":
            table = f'<p role="status">Runtime inventory unavailable · {_e(inventory.status)}: {_e(inventory.reason)}</p>'
        elif not rows:
            table = "<p>No sandboxes reported by <code>sbx ls --json</code>.</p>"
        else:
            cells = []
            for r in rows:
                associations = "".join(
                    f"<li>{_e(a.ticket)} · {_e(a.source)} · {'current' if a.current else 'historical'} · {_e(a.reference)}</li>"
                    for a in r.associations
                )
                cells.append(
                    f"<tr><td data-label='Sandbox'>{_e(r.name)}"
                    + (' <span class="chip">operator-owned</span>' if r.operator_owned else "")
                    + f"<details><summary>Runtime evidence</summary><dl><dt>Workspace</dt><dd>{_e(r.workspace)}</dd><dt>Template</dt><dd>{_e(r.template or 'Unavailable')}</dd><dt>Ports</dt><dd>{_e(', '.join(r.published_ports) or 'Unavailable')}</dd><dt>Last denial</dt><dd>{_e(r.last_denial or 'None recorded')}</dd></dl><ul>{associations}</ul></details></td><td data-label='State'>{_e(r.state)}</td><td data-label='Layout'>{_e(r.layout)}</td><td data-label='Certification'>Unverified</td><td data-label='Runs'>{_e(', '.join(r.runs_using) or 'Unassociated')}</td></tr>"
                )
            table = (
                '<div class="scroll" tabindex="0" role="region" aria-label="Runtime inventory"><table class="inventory-table runtime-inventory"><thead><tr><th>Sandbox</th><th>State</th><th>Layout</th><th>Certification</th><th>Runs</th></tr></thead><tbody>'
                + "".join(cells)
                + "</tbody></table></div>"
            )
        compatibility = (
            "".join(
                f"<details><summary>{_e(r.name)}</summary><p>{_e(r.compatibility)}</p><h3>Historical recorded evidence</h3><p>These retained reports are not a current identity attestation.</p><pre>{_e(json.dumps(r.recorded_compatibility, indent=2))}</pre></details>"
                for r in rows
            )
            or "<p>Compatibility evidence unavailable.</p>"
        )
        return HTMLResponse(
            _page("runtimes", _render("runtimes.html", table=table, compatibility=compatibility))
        )

    @app.get("/config", response_class=HTMLResponse)
    def config_view(request: Request) -> HTMLResponse:
        reg, rt, _, _ = _cfg()
        view = console_views.config_view(rt, reg)
        error = request.query_params.get("error")
        saved = request.query_params.get("saved")

        role_rows = "".join(
            "<tr>"
            f"<td>{_e(role.name)}</td>"
            f'<td><input aria-label="{_e(role.name)} model" name="model.{_e(role.name)}" value="{_e(role.model)}" size="20"></td>'
            f'<td><input aria-label="{_e(role.name)} effort" name="effort.{_e(role.name)}" value="{_e(role.effort)}" size="10"></td>'
            "</tr>"
            for role in view.roles
        )
        banner = ""
        if error:
            banner = f'<p class="fail">rejected: {_e(error)}</p>'
        elif saved:
            banner = '<p class="pass">models.toml written.</p>'

        body = _render(
            "config.html",
            banner=banner,
            role_rows=role_rows,
            usd_per_run=str(view.usd_per_run),
            usd_warn_at=str(view.usd_warn_at),
            project_rows="".join(f"<li>{_e(name)}</li>" for name in view.projects_read_only),
        )
        return HTMLResponse(_page("configuration", body))

    @app.post("/config/models")
    async def config_write(request: Request) -> RedirectResponse:
        """View 4's write. Validated **before** the file is replaced: the edit is rendered
        to TOML and run through `routing.validate`, and only a table that passes §4.5's
        rules reaches the disk. A rejected edit returns to the form with the rule that
        refused it — never a half-written `models.toml` the next tick would refuse.

        Both halves belong to `routing`, which owns reading this file: the console decides
        *when* an edit happens and renders the refusal, and knows nothing about the
        file's shape."""
        form = _parse_form(await request.body())
        path = home / "config" / "models.toml"
        try:
            text = routing_module.rewrite(path, form)
            routing_module.validate(text)
        except (RoutingError, ValueError, KeyError) as exc:
            return RedirectResponse(f"/config?error={_query(str(exc))}", status_code=303)
        path.write_text(text, encoding="utf-8")
        return RedirectResponse("/config?saved=1", status_code=303)

    return app


def _store_for_this_thread(injected: Store | None, home: Path, open_store: Any) -> Store:
    """The injected store when this thread may use it, else a fresh connection to it.

    `sqlite3` refuses a connection across threads, and a served app is multi-threaded. The
    tests inject a `Store` built on the main thread and then drive the app through
    `TestClient`, which runs it in a worker — so honouring the injection blindly raises
    `ProgrammingError`, and ignoring it would point the tests at the real `~/factory`
    database. Reconnecting to the *injected store's own path* satisfies both.
    """
    if injected is None:
        return open_store(home)
    if threading.get_ident() == getattr(injected, "_owner_thread", None):
        return injected
    try:
        injected._conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return Store(injected.path)
    return injected


def _parse_form(body: bytes) -> dict[str, Any]:
    """Parse an `application/x-www-form-urlencoded` body with the stdlib.

    Starlette's `request.form()` would need `python-multipart`, and the console posts
    nothing but flat text fields — a fifth dependency on the deliberately thin control
    plane to parse `a=1&b=2` is not a trade worth making (§18.5's dependency note).
    """
    from urllib.parse import parse_qsl

    return dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))


def _query(value: str) -> str:
    from urllib.parse import quote

    return quote(value[:400])
