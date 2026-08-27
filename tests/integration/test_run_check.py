"""`factory run <T> --check` — the promise `--dry-run` made and did not keep.

`--dry-run` was an untested simulation of the state machine: a second implementation of
every transition, never checked against the first, advertised twice in the README. What
a human actually wanted from it is the question `assess` already answers before any write
— *would this ticket run?* — and `cmd_run` already prints in its first 25 lines.

The load-bearing property is the exit codes. `--check` mirrors `factory run` exactly, or
it is a second opinion rather than a preview, and a script that trusted it would learn the
difference at the wrong moment.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pytest

from factory import cli
from factory.machine import State
from factory.steps import Context
from tests.integration.conftest import HOME


def _args(ticket: str) -> argparse.Namespace:
    return argparse.Namespace(
        ticket=ticket, check=True, plan=False, full_review=False, no_follow=False
    )


def _run_check(ctx: Context, monkeypatch: pytest.MonkeyPatch, ticket: str) -> int:
    """`cmd_run --check` against the fixture home, with the real adapters unbuilt.

    `--check` never reaches an adapter — that is the point of it — so the only things
    stubbed are the two `cmd_run` resolves before the assessment.
    """
    (ctx.home / "config" / "models.toml").write_text(
        (HOME / "config" / "models.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setenv("FACTORY_HOME", str(ctx.home))
    monkeypatch.setattr(cli, "LinearClient", lambda: ctx.linear)
    return cli.cmd_run(_args(ticket))


def test_an_eligible_ticket_checks_clean_and_writes_nothing(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 0, and — the half that matters — no run row.

    `--dry-run` opened a throwaway database and walked a simulated pipeline through it.
    `--check` stops before `insert_run`, so "nothing was written" is a property of where
    the function returns rather than of which file it wrote to.
    """
    ticket = ctx.run.linear_id
    before = {r.id for r in ctx.store.runs_in_states(list(State))}

    code = _run_check(ctx, monkeypatch, ticket)

    assert code == 0
    assert "is eligible" in capsys.readouterr().out
    assert {r.id for r in ctx.store.runs_in_states(list(State))} == before


def test_a_reasonless_failure_exits_1_the_way_the_run_does(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """§7.1's first failure shape: a ticket that was never the factory's. `cmd_run` exits
    1 and creates no row; `--check` must say the same number."""
    ctx.linear.issue_data = replace(ctx.linear.issue_data, labels=())  # type: ignore[attr-defined]

    code = _run_check(ctx, monkeypatch, ctx.run.linear_id)

    assert code == 1
    assert "not eligible" in capsys.readouterr().out


def test_a_reasoned_failure_exits_2_the_way_the_run_does(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """§7.1's second shape: the factory's ticket, not ready. `cmd_run` exits 2 after
    recording a `blocked` run — `--check` reports the same verdict and records nothing,
    which is the one place the two deliberately differ."""
    ctx.linear.issue_data = replace(  # type: ignore[attr-defined]
        ctx.linear.issue_data,  # type: ignore[attr-defined]
        state_name="Backlog",
        state_type="backlog",
    )

    code = _run_check(ctx, monkeypatch, ctx.run.linear_id)
    out = capsys.readouterr().out

    assert code == 2
    assert "would be blocked" in out
    assert "state-not-todo" in out


def test_check_says_it_wrote_nothing(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The sentence README advertised for `--dry-run`, now true by construction."""
    _run_check(ctx, monkeypatch, ctx.run.linear_id)

    assert "Nothing was written" in capsys.readouterr().out


def test_the_run_parser_no_longer_takes_dry_run() -> None:
    """The flag is gone from the interface, not just from the code path. A parser that
    still accepted it would keep the README's promise alive with nothing behind it."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "BAC-4", "--dry-run"])
    assert parser.parse_args(["run", "BAC-4", "--check"]).check is True


def test_gc_still_has_its_dry_run() -> None:
    """`gc --dry-run` is untouched: a real sweep planner, tested, whose `dry_run` is
    `gc.sweep`'s own parameter and never a `Context` field."""
    assert cli.build_parser().parse_args(["gc", "--dry-run"]).dry_run is True


def test_the_readme_does_not_advertise_a_flag_that_is_gone() -> None:
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    assert "factory run BAC-6 --dry-run" not in readme
