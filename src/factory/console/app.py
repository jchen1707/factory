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
import tomllib
from dataclasses import replace
from functools import cache
from pathlib import Path
from string import Template
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from factory.console import views as console_views
from factory.intake.linear import LinearClient
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


@cache
def _template(name: str) -> Template:
    """One template, read once and cached. Cached because a served page should not hit the
    filesystem per request, and these files never change while the process is alive."""
    return Template((TEMPLATES / name).read_text(encoding="utf-8"))


def _render(name: str, /, **fields: str) -> str:
    return _template(name).substitute(**fields)


def _page(title: str, body: str) -> str:
    """The shell every view shares: head, stylesheet, nav, body."""
    return _render(
        "page.html",
        title=_e(title),
        style=(TEMPLATES / "console.css").read_text(encoding="utf-8"),
        body=body,
    )


def _pct_cell(pct: float | None, reason: str | None) -> str:
    """The context percentage, or the reason it is hidden. §18.5: never estimated — a
    hidden percentage says why, so the operator can tell 'no window on file' from 'the
    agent has not finished a turn'."""
    if pct is None:
        return f'<span class="muted" title="{_e(reason or "")}">—</span>'
    width = max(0, min(100, int(pct * 100)))
    cls = "fail" if width >= 85 else ("warn" if width >= 70 else "")
    return (
        f'<span class="{cls}">{width}%</span> '
        f'<span class="bar"><i style="width:{width}%"></i></span>'
    )


def _spend_cell(usd: float | None, ceiling: float) -> str:
    if usd is None:
        return f'<span class="muted">— / ${ceiling:.0f}</span>'
    width = max(0, min(100, int((usd / ceiling) * 100))) if ceiling else 0
    cls = "fail" if usd >= ceiling else ("warn" if width >= 60 else "")
    return (
        f'<span class="{cls}">${usd:.2f}</span> <span class="muted">/ ${ceiling:.0f}</span> '
        f'<span class="bar"><i style="width:{width}%"></i></span>'
    )


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


def _board_table(rows: list[console_views.RunRow]) -> str:
    """View 1 — the runs board. Every column §18.5 names, and no Merge control."""
    if not rows:
        return '<p class="muted">No runs yet.</p>'
    cells = []
    for r in rows:
        badge = f' <span class="chip">{_e(r.badge)}</span>' if r.badge else ""
        blocked = (
            f'<div class="muted">blocked: {_e(r.blocked_reason)}</div>' if r.blocked_reason else ""
        )
        cells.append(
            "<tr>"
            f'<td><a href="/runs/{_e(r.ticket)}">{_e(r.ticket)}</a></td>'
            f"<td>{_e(r.project)}</td>"
            f'<td class="wrap">{_e(r.state)}{badge}{blocked}</td>'
            f"<td>{r.attempt} <span class='muted'>· {_e(r.rung)}</span></td>"
            f"<td>{_elapsed_cell(r.elapsed_in_state, r.timeout_seconds)}</td>"
            f"<td>{_pct_cell(r.context_pct, r.context_reason)}</td>"
            f"<td>{r.tokens_in:,} / {r.tokens_out:,}"
            f"<span class='muted'> ({r.tokens_cached:,} cached)</span></td>"
            f"<td>{_spend_cell(r.spend_usd, r.spend_ceiling)}</td>"
            f'<td class="wrap">{_e(r.activity) if r.activity else "<span class=muted>—</span>"}</td>'
            f"<td>{_duration(r.heartbeat_age)}</td>"
            "</tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr>'
        "<th>ticket</th><th>project</th><th>state</th><th>attempt</th><th>in state</th>"
        "<th>context</th><th>tokens in / out</th><th>spend</th><th>activity</th><th>live</th>"
        "</tr></thead><tbody>" + "".join(cells) + "</tbody></table></div>"
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

    @app.get("/", response_class=HTMLResponse)
    def board() -> HTMLResponse:
        reg, rt, st, _ = _cfg()
        rows = console_views.runs_board(home, reg, rt, st)
        body = _render("board.html", table=_board_table(rows))
        return HTMLResponse(_page("runs", body))

    @app.get("/sse/board")
    async def board_stream() -> StreamingResponse:
        """The live board, as server-sent events. Re-reads the store each tick — nothing is
        held between them, the same property §4.2 requires of the tick itself."""

        def read_board() -> str:
            """Blocking: it opens SQLite and stats attempt directories. Off the loop."""
            reg, rt, st, _ = _cfg()
            return _board_table(console_views.runs_board(home, reg, rt, st))

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

        head = (
            f"<h1>{_e(r.ticket)} <span class='muted'>{_e(r.project)}</span></h1>"
            f'<p class="sub">{_e(r.state)} · attempt {r.attempt} · rung {_e(r.rung)} · '
            f"context {_pct_cell(r.context_pct, r.context_reason)} · "
            f"spend {_spend_cell(r.spend_usd, r.spend_ceiling)}</p>"
        )
        if detail.pr_url:
            head += (
                f'<p>Pull request: <a href="{_e(detail.pr_url)}" target="_blank" '
                f'rel="noreferrer">{_e(detail.pr_url)}</a> '
                '<span class="muted">— merging happens on GitHub.</span></p>'
            )
        if detail.blocked_reason:
            head += f'<p class="fail">blocked: {_e(detail.blocked_reason)}</p>'
        if r.context_pct is None and r.context_reason:
            head += f'<p class="note">context hidden: {_e(r.context_reason)}</p>'

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
            '<h2>transitions</h2><div class="scroll"><table><thead><tr><th>at</th>'
            "<th>hop</th><th>actor</th><th>rule</th></tr></thead><tbody>"
            + timeline
            + "</tbody></table></div>"
        )

        gates_html = ""
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
                f'<h2>gate report — <span class="{verdict_cls}">{_e(detail.gate_verdict)}</span></h2>'
                '<div class="scroll"><table><thead><tr><th>status</th><th>gate</th>'
                "<th>caveat</th></tr></thead><tbody>" + gate_rows + "</tbody></table></div>"
            )

        review_html = ""
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
                f"<h2>review <span class='muted'>tier 2: {_e(detail.review_tier2)}</span></h2>"
                + (
                    '<div class="scroll"><table><thead><tr><th>severity</th><th>where</th>'
                    "<th>finding</th></tr></thead><tbody>" + finding_rows + "</tbody></table></div>"
                    if ranked
                    else '<p class="muted">No findings.</p>'
                )
            )

        artifacts_html = ""
        if detail.artifacts:
            items = "".join(
                f"<tr><td class='muted'>{_e((a.sha256 or '-')[:12])}</td><td>{_e(a.name)}</td></tr>"
                for a in detail.artifacts
            )
            artifacts_html = (
                '<h2>artifacts</h2><div class="scroll"><table><thead><tr><th>sha256</th>'
                "<th>file</th></tr></thead><tbody>" + items + "</tbody></table></div>"
            )

        tail_html = ""
        if detail.events_path:
            tail_html = (
                "<h2>live tail</h2>"
                f'<pre id="tail" class="muted">reading {_e(detail.events_path)}…</pre>'
                "<script>"
                f"const t=new EventSource('/sse/tail/{_e(r.ticket)}');"
                "t.onmessage=e=>{const p=document.getElementById('tail');"
                "p.textContent=JSON.parse(e.data).text;p.scrollTop=p.scrollHeight;};"
                "</script>"
            )

        body = _render(
            "run_detail.html",
            head=head,
            controls=_controls(r.ticket),
            timeline=timeline_html,
            gates=gates_html,
            review=review_html,
            artifacts=artifacts_html,
            tail=tail_html,
        )
        return HTMLResponse(_page(r.ticket, body))

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
        from factory.cli import _sbx_ls_json

        _, _, st, _ = _cfg()
        rows = console_views.runtimes(_sbx_ls_json(), st)
        if not rows:
            table = '<p class="muted">No sandboxes reported by <code>sbx ls --json</code>.</p>'
        else:
            body_rows = "".join(
                "<tr>"
                + (
                    f'<td class="muted">{_e(r.name)} <span class="chip">operator-owned</span></td>'
                    if r.operator_owned
                    else f"<td>{_e(r.name)}</td>"
                )
                + f"<td>{_e(r.state)}</td>"
                + f'<td class="wrap">{_e(r.workspace)}</td>'
                + f"<td>{_e(', '.join(r.published_ports) or '—')}</td>"
                + f"<td>{_e(r.template)}</td>"
                + f"<td>{_e(', '.join(r.runs_using) or '—')}</td>"
                + f'<td class="wrap muted">{_e(r.last_denial or "")}</td>'
                "</tr>"
                for r in rows
            )
            table = (
                '<div class="scroll"><table><thead><tr><th>sandbox</th><th>state</th>'
                "<th>workspace</th><th>ports</th><th>template</th><th>runs</th>"
                "<th>last denial</th></tr></thead><tbody>" + body_rows + "</tbody></table></div>"
            )
        return HTMLResponse(_page("runtimes", _render("runtimes.html", table=table)))

    @app.get("/config", response_class=HTMLResponse)
    def config_view(request: Request) -> HTMLResponse:
        reg, rt, _, _ = _cfg()
        view = console_views.config_view(rt, reg)
        error = request.query_params.get("error")
        saved = request.query_params.get("saved")

        role_rows = "".join(
            "<tr>"
            f"<td>{_e(role.name)}</td>"
            f'<td><input name="model.{_e(role.name)}" value="{_e(role.model)}" size="20"></td>'
            f'<td><input name="effort.{_e(role.name)}" value="{_e(role.effort)}" size="10"></td>'
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
        to TOML, parsed back through `load_routing`, and only a table that passes §4.5's
        rules reaches the disk. A rejected edit returns to the form with the rule that
        refused it — never a half-written `models.toml` the next tick would refuse."""
        form = _parse_form(await request.body())
        path = home / "config" / "models.toml"
        try:
            text = _rewrite_models_toml(path, form)
            _validate_models_toml(text)
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
        return open_store(home, dry_run=False)
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


def _rewrite_models_toml(path: Path, form: dict[str, Any]) -> str:
    """Apply the form's role/budget edits to `models.toml`, returning the new text.

    A targeted line rewrite rather than a re-serialisation: the file carries the measured
    model catalogue and a page of comments explaining why each number is what it is, and
    round-tripping it through a TOML writer would throw all of that away. Only the values
    the form actually owns are touched.
    """
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines()
    current_role: str | None = None
    out: list[str] = []
    in_budget = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[roles."):
            current_role = stripped[len("[roles.") :].rstrip("]").strip()
            in_budget = False
        elif stripped.startswith("["):
            current_role = None
            in_budget = stripped.startswith("[budget]")

        if current_role and "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in ("model", "effort"):
                proposed = form.get(f"{key}.{current_role}")
                if proposed is not None:
                    out.append(_replace_value(line, f'"{str(proposed).strip()}"'))
                    continue
        if in_budget and "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in ("usd_per_run", "usd_warn_at"):
                proposed = form.get(key)
                if proposed is not None:
                    out.append(_replace_value(line, str(float(str(proposed).strip()))))
                    continue
        out.append(line)
    return "\n".join(out) + "\n"


def _replace_value(line: str, rendered: str) -> str:
    """Swap the value on one `key = value  # comment` line, leaving everything else byte-
    identical — including the column the comment sits in.

    A line whose value is unchanged is returned untouched rather than re-rendered. The
    alternative (always rebuilding the line) reflows the comment alignment of every role
    in the file on any edit, so a one-effort change arrives as a five-line diff and the
    next reader cannot see what actually changed.
    """
    head, _, rest = line.partition("=")
    comment_at = rest.find("#")
    current = (rest if comment_at < 0 else rest[:comment_at]).strip()
    if current == rendered:
        return line
    if comment_at < 0:
        return f"{head}= {rendered}"
    # Keep the comment in its original column where the new value still fits under it.
    comment = rest[comment_at:]
    padding = len(rest[:comment_at]) - len(f" {rendered}")
    return f"{head}= {rendered}{' ' * padding if padding > 0 else '  '}{comment}"


def _validate_models_toml(text: str) -> None:
    """Run §4.5's rules over the proposed text without writing it.

    `load_routing` reads a path, so the candidate is written to a temporary file and parsed
    from there — the validation that runs is byte-for-byte the one the daemon runs, which
    is the only way the form's "no" and the tick's "no" cannot disagree."""
    import tempfile

    tomllib.loads(text)  # a syntax error should say so before §4.5 does
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        candidate = Path(handle.name)
    try:
        load_routing(candidate)
    finally:
        candidate.unlink(missing_ok=True)
