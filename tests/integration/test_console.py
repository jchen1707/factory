"""§18.5 — the operator console, in-process against the same fakes as the pipeline.

Everything asserted here is one of §23's operator-interface acceptance rows: the board
renders every non-terminal run with the columns named; the context percentage is shown or
hidden-with-a-reason and never estimated; a configuration edit that would put the reviewer
on the builder's model is rejected **in the form**; every control writes an
`actor="human"` transition; and there is no Merge button anywhere.

The app is driven through `TestClient`, so the routes, the rendering and the control
dispatch are all exercised — the console's whole job is what a page actually says, and a
test that called the view functions directly would prove the data and skip the answer.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factory import policy
from factory.console import views as console_views
from factory.console.app import create_app
from factory.machine import State
from factory.steps import Context
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import implement as implement_step
from factory.steps import sandbox as sandbox_step
from factory.steps import worktree as worktree_step
from tests.integration.conftest import FakeSandbox


def _fake(ctx: Context) -> FakeSandbox:
    assert isinstance(ctx.sandbox, FakeSandbox)
    return ctx.sandbox


def _client(ctx: Context) -> TestClient:
    """The app wired to the test's own registry/routing/store/linear — no adapters built.

    §21.3: the whole state machine runs in-process against fakes, and the console is held
    to the same bar. Nothing here opens the real `~/factory` state database.
    """
    app = create_app(
        ctx.home,
        registry=ctx.registry,
        routing=ctx.routing,
        store=ctx.store,
        linear=ctx.linear,
        # The controls get the test's own fakes rather than the real `sbx`/`codex`
        # adapters, which would need a Docker login to so much as suspend a run.
        context_factory=lambda run: replace(ctx, run=run),
    )
    return TestClient(app)


def _to_implementing(ctx: Context) -> None:
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)
    _fake(ctx).detach_without_finishing = True
    implement_step.start(ctx)
    ctx.refresh()


# --------------------------------------------------------------------------------
# View 1 — the runs board
# --------------------------------------------------------------------------------


def test_the_board_renders_a_live_run_with_every_column(ctx: Context) -> None:
    _to_implementing(ctx)

    page = _client(ctx).get("/").text

    assert "BAC-4" in page
    assert "implementing" in page
    # attempt and the §16.3a rung it occupies
    assert "restart" in page
    # the column headers §18.5 names
    for header in ("state", "attempt", "context", "tokens in / out", "spend", "activity"):
        assert header in page, header


def test_the_board_refreshes_without_a_reload(ctx: Context) -> None:
    # "…and refreshes without a reload" — the acceptance row. The SSE endpoint is what
    # makes that true, so the page has to actually subscribe to it.
    _to_implementing(ctx)

    page = _client(ctx).get("/").text

    assert "EventSource('/sse/board')" in page


def test_a_terminal_run_is_not_on_the_board_but_still_has_a_page(ctx: Context) -> None:
    # §18.5's board is "every non-terminal run". A cancelled run is history: leaving it on
    # the board buries the runs actually in flight, but its evidence must still be reachable.
    _to_implementing(ctx)
    ctx.store.record_transition(
        ctx.run.id,
        from_state=ctx.state,
        to_state=State.CANCELLED,
        actor="human",
        rule="abandon-is-james",
    )
    ctx.refresh()
    client = _client(ctx)

    assert "BAC-4" not in client.get("/").text
    assert client.get("/runs/BAC-4").status_code == 200


def test_the_board_shows_a_blocked_badge_and_the_reason(ctx: Context) -> None:
    _to_implementing(ctx)
    ctx.store.update_run(ctx.run.id, blocked_reason="env-gate-failed")
    ctx.store.record_transition(
        ctx.run.id,
        from_state=ctx.state,
        to_state=State.BLOCKED,
        actor="auto",
        rule="env-gate-failed",
    )
    ctx.refresh()

    page = _client(ctx).get("/").text

    assert "blocked" in page
    assert "env-gate-failed" in page


# --------------------------------------------------------------------------------
# The context percentage — shown from the measurement, or hidden with the reason
# --------------------------------------------------------------------------------


def test_the_context_percentage_is_hidden_with_its_reason_not_estimated(ctx: Context) -> None:
    # The run is `implementing` and the agent has only just been spawned, so there is no
    # numerator to divide. The page must say why rather than show a number, and must not
    # invent one — the estimate is the thing §18.5 rules out.
    _to_implementing(ctx)

    rows = console_views.runs_board(ctx.home, ctx.registry, ctx.routing, ctx.store)

    row = next(r for r in rows if r.ticket == "BAC-4")
    assert row.context_pct is None
    assert row.context_reason == "no event stream for this attempt yet"
    # And the page renders the em dash carrying the reason, never a figure.
    page = _client(ctx).get("/").text
    assert 'title="no event stream for this attempt yet"' in page
    # The context cell is the em dash and the reason — no figure of any kind beside it.
    context_cell = page.split('title="no event stream for this attempt yet"')[1].split("</td>")[0]
    assert "%" not in context_cell


def test_the_context_percentage_is_shown_once_a_turn_lands(ctx: Context) -> None:
    _to_implementing(ctx)
    attempt_dir = ctx.factory_dir / "run" / str(ctx.run.attempt)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    usable = ctx.routing.models[ctx.routing.role("builder").model].usable_context
    (attempt_dir / "events.jsonl").write_text(
        json.dumps({"type": "thread.started", "thread_id": "01a0"})
        + "\n"
        + json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": usable // 2,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 10,
                    "reasoning_output_tokens": 0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rows = console_views.runs_board(ctx.home, ctx.registry, ctx.routing, ctx.store)

    row = next(r for r in rows if r.ticket == "BAC-4")
    assert row.context_reason is None
    assert row.context_pct is not None
    assert round(row.context_pct, 2) == 0.50
    assert "50%" in _client(ctx).get("/").text


# --------------------------------------------------------------------------------
# View 3 — run detail
# --------------------------------------------------------------------------------


def test_the_run_detail_shows_the_gate_table_with_its_caveats(ctx: Context) -> None:
    # §18.5: "every caveat printed beside its green result". A caveated pass is the shape
    # that hid FRO-6's lighthouse failure, so the caveat is not an optional flourish.
    _to_implementing(ctx)
    attempt_dir = ctx.factory_dir / "run" / str(ctx.run.attempt)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / "gates.json").write_text(
        json.dumps(
            {
                "verdict": "fail",
                "gates": [
                    {"name": "ruff check", "status": "pass", "caveat": None},
                    {
                        "name": "lighthouse",
                        "status": "fail",
                        "caveat": "performance scores null without Chrome",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    page = _client(ctx).get("/runs/BAC-4").text

    assert "ruff check" in page
    assert "lighthouse" in page
    assert "performance scores null without Chrome" in page


def test_the_run_detail_ranks_review_findings_by_severity(ctx: Context) -> None:
    _to_implementing(ctx)
    review_dir = ctx.home / "state" / "runs" / ctx.run.id / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "review-summary.json").write_text(
        json.dumps(
            {
                "tier2": "ran:forced",
                "findings": [
                    {"severity": "low", "file": "a.py", "line": 1, "summary": "the low one"},
                    {"severity": "critical", "file": "b.py", "line": 2, "summary": "the bad one"},
                ],
            }
        ),
        encoding="utf-8",
    )

    page = _client(ctx).get("/runs/BAC-4").text

    assert "ran:forced" in page
    assert page.index("the bad one") < page.index("the low one")  # critical first


# --------------------------------------------------------------------------------
# View 5 — controls. Every one is a human act; none of them is Merge.
# --------------------------------------------------------------------------------


def test_there_is_no_merge_button_on_any_page(ctx: Context) -> None:
    # §18.5, verbatim: "There is no Merge button." Merging happens on GitHub, by James.
    # The word appears only in the prose that says so, never as a control.
    _to_implementing(ctx)
    ctx.store.update_run(ctx.run.id, pr_url="https://github.com/x/y/pull/1")
    ctx.refresh()
    client = _client(ctx)

    for path in ("/", "/runs/BAC-4", "/runtimes", "/config"):
        page = client.get(path).text
        assert "/merge" not in page, path
        assert ">Merge<" not in page, path
    assert "merge" not in {
        slug
        for slug, _ in __import__(
            "factory.console.app", fromlist=["_CONTROL_BUTTONS"]
        )._CONTROL_BUTTONS
    }


def test_a_control_writes_a_human_actor_transition(ctx: Context) -> None:
    # "every control writes an actor = 'human' transition" — the acceptance row. Suspend is
    # the one that always applies to a running agent.
    _to_implementing(ctx)

    response = _client(ctx).post("/runs/BAC-4/suspend", follow_redirects=False)

    assert response.status_code == 303
    ctx.refresh()
    assert ctx.run.state is State.SUSPENDED
    row = ctx.store.transitions(ctx.run.id)[-1]
    assert row["actor"] == "human"
    assert row["rule"] == "suspend-is-james"


def test_resume_from_planning_works_from_the_browser(ctx: Context) -> None:
    # §19's expected output names this one specifically: "suspend and resume-from-planning
    # both work from the browser and from the CLI, and both write an `actor = \"human\"`
    # transition." Suspend is covered above; this is the other half, and it is the control
    # that rewinds a stuck run to a fresh plan (§16.3a) without resetting the worktree.
    _to_implementing(ctx)
    worktree = Path(ctx.run.worktree or "")
    (worktree / "the-agents-work.py").write_text("x = 1\n")
    _client(ctx).post("/runs/BAC-4/suspend", follow_redirects=False)
    ctx.refresh()
    assert ctx.state is State.SUSPENDED

    response = _client(ctx).post("/runs/BAC-4/resume-planning", follow_redirects=False)

    assert response.status_code == 303
    ctx.refresh()
    hops = [
        (row["from_state"], row["to_state"], row["actor"])
        for row in ctx.store.transitions(ctx.run.id)
    ]
    assert (str(State.SUSPENDED), str(State.PLANNING), "human") in hops
    # The worktree is never reset by a rewind — the agent's work is the input to the plan.
    assert (worktree / "the-agents-work.py").exists()


def test_an_unknown_control_is_refused_rather_than_dispatched(ctx: Context) -> None:
    from factory.cli import dispatch_control

    _to_implementing(ctx)
    before = len(ctx.store.transitions(ctx.run.id))

    code, message = dispatch_control(
        "merge", ctx.home, ctx.registry, ctx.routing, ctx.store, ctx.linear, ctx.run
    )

    assert code == 1
    assert "unknown control" in message
    assert len(ctx.store.transitions(ctx.run.id)) == before  # nothing happened


def test_a_control_aimed_at_an_operator_sandbox_is_refused(ctx: Context) -> None:
    # F26. The namespace assertion is the boundary: a `codex-*` sandbox is James's live
    # csbx session, and an unattended writer inside it is the failure the rule prevents.
    from dataclasses import replace

    from factory.cli import dispatch_control

    _to_implementing(ctx)
    hijacked = replace(ctx.project, build_sandbox="codex-frontend-harness")
    registry = replace(ctx.registry, projects={**ctx.registry.projects, "python-harness": hijacked})

    with pytest.raises(PermissionError):
        dispatch_control("suspend", ctx.home, registry, ctx.routing, ctx.store, ctx.linear, ctx.run)


def test_the_namespace_rule_itself_refuses_a_codex_sandbox() -> None:
    with pytest.raises(PermissionError):
        policy.assert_factory_sandbox("codex-frontend-harness")
    policy.assert_factory_sandbox("factory-build-python-harness")  # and permits its own


# --------------------------------------------------------------------------------
# View 4 — configuration, validated in the form
# --------------------------------------------------------------------------------


def _form(ctx: Context, **overrides: str) -> dict[str, str]:
    body: dict[str, str] = {}
    for name, role in ctx.routing.roles.items():
        body[f"model.{name}"] = role.model
        body[f"effort.{name}"] = role.effort
    body["usd_per_run"] = str(ctx.routing.usd_per_run)
    body["usd_warn_at"] = str(ctx.routing.usd_warn_at)
    body.update(overrides)
    return body


def _models_toml(ctx: Context) -> Path:
    """The console writes `<home>/config/models.toml`; the fixture's home has only
    `projects.toml`, so seed the real file the repository ships."""
    path = ctx.home / "config" / "models.toml"
    if not path.exists():
        source = Path(__file__).resolve().parents[2] / "config" / "models.toml"
        path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def test_a_config_edit_putting_the_reviewer_on_the_builders_model_is_rejected(
    ctx: Context,
) -> None:
    # The acceptance row: rejected "in the form, before it is written". So the assertion is
    # two-part — the response says no, and the file on disk is byte-identical.
    path = _models_toml(ctx)
    before = path.read_text(encoding="utf-8")
    builder = ctx.routing.role("builder").model

    response = _client(ctx).post(
        "/config/models", data=_form(ctx, **{"model.reviewer": builder}), follow_redirects=False
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert "reviewer.model" in response.headers["location"].replace("%20", " ").replace("%2E", ".")
    assert path.read_text(encoding="utf-8") == before  # never half-written


def test_a_valid_config_edit_is_written_and_touches_only_that_line(ctx: Context) -> None:
    # The file carries the measured catalogue and a page of comments explaining every
    # number. A writer that re-serialised the document would throw all of that away, so a
    # one-field edit has to arrive as a one-line diff.
    path = _models_toml(ctx)
    before = path.read_text(encoding="utf-8").splitlines()

    response = _client(ctx).post(
        "/config/models",
        data=_form(ctx, **{"effort.documenter": "medium"}),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "saved=1" in response.headers["location"]
    after = path.read_text(encoding="utf-8").splitlines()
    changed = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert len(changed) == 1
    assert changed[0][1].strip() == 'effort = "medium"'


def test_projects_are_rendered_read_only_with_the_reason(ctx: Context) -> None:
    # §18.5: changing a project's template/mount/MCP set changes a sandbox specification
    # fixed at creation, so the console says so rather than offering a field that would
    # silently do nothing.
    _models_toml(ctx)

    page = _client(ctx).get("/config").text

    assert "python-harness" in page
    assert "read-only" in page
    assert "fixed at creation" in page


# --------------------------------------------------------------------------------
# View 2 — runtimes
# --------------------------------------------------------------------------------


def test_an_operator_owned_sandbox_is_marked_and_offered_no_control(ctx: Context) -> None:
    rows = console_views.runtimes(
        [
            {"name": "factory-build-python-harness", "state": "running", "ports": []},
            {"name": "codex-frontend-harness", "state": "running", "ports": []},
        ],
        ctx.store,
    )

    by_name = {r.name: r for r in rows}
    assert by_name["factory-build-python-harness"].operator_owned is False
    assert by_name["codex-frontend-harness"].operator_owned is True


def test_published_ports_are_read_from_the_measured_shape(ctx: Context) -> None:
    # `sbx ls --json` reports ports as {host_ip, host_port, sandbox_port} objects, not
    # strings — the shape `SbxAdapter.git_daemon_url` already reads. A console that
    # expected strings would print nothing on a healthy machine.
    rows = console_views.runtimes(
        [
            {
                "name": "factory-build-python-harness",
                "state": "running",
                "ports": [{"host_ip": "127.0.0.1", "host_port": 49157, "sandbox_port": 9418}],
            }
        ],
        ctx.store,
    )

    assert rows[0].published_ports == ["49157->9418"]


def test_a_build_sandbox_lists_the_run_implementing_in_it(ctx: Context) -> None:
    _to_implementing(ctx)

    rows = console_views.runtimes(
        [{"name": "factory-build-python-harness", "state": "running", "ports": []}], ctx.store
    )

    assert rows[0].runs_using == ["BAC-4"]
