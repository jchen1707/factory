"""§20.5 — `factory doctor` as a module, and the third status it needed.

The bug under test is not a wrong answer, it is a missing one: three check families were
written behind `if registry:` in an argparse handler, so a broken `projects.toml` made
them **vanish from the output**. Fewer lines, no failures, exit 0 — a doctor that reports
a cleaner machine the more broken the machine is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory import doctor
from factory.doctor import CHECKS, DoctorContext, Result, Status

HOME = Path(__file__).resolve().parents[2]


def _broken_home(tmp_path: Path) -> Path:
    """A home whose `projects.toml` will not parse, and whose `models.toml` is the real one.

    Deliberately only the registry: the point is that *one* config failure must not take
    unrelated rows out of the report with it.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "projects.toml").write_text("[projects.p\nname = ", encoding="utf-8")
    (tmp_path / "config" / "models.toml").write_text(
        (HOME / "config" / "models.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path


# --------------------------------------------------------------------------------
# One shape
# --------------------------------------------------------------------------------


def test_every_check_has_the_same_shape(tmp_path: Path) -> None:
    """The coverage the heterogeneous functions could not express.

    They returned four different things — `(name, ok, detail)`, `(ok, detail)`, a bare
    bool, and a fan-out over projects — so there was no way to say "run them all and
    assert something about every result". There is now, and this is it.
    """
    ctx = DoctorContext(home=tmp_path)
    for candidate in CHECKS:
        if candidate.deep or candidate.needs:
            continue
        results = candidate.run(ctx)
        assert results, f"{candidate.name} produced no result"
        for result in results:
            assert isinstance(result, Result)
            assert result.name
            assert result.status in (Status.OK, Status.FAIL, Status.SKIPPED)


def test_check_is_looked_up_by_name_and_says_so_when_it_is_not_there() -> None:
    assert doctor.check("plan copy").name == "plan copy"
    with pytest.raises(KeyError, match="no doctor check named"):
        doctor.check("no such check")


# --------------------------------------------------------------------------------
# The third status
# --------------------------------------------------------------------------------


def test_a_broken_registry_skips_its_dependents_rather_than_hiding_them(
    tmp_path: Path,
) -> None:
    """The defect, stated directly.

    With `projects.toml` unparseable, the vendored-layer-A, sensitive-path and canary
    rows used to not be printed at all. They must appear, as `skipped`, naming what did
    not load — because "I could not check this" and "this is fine" are different answers
    and the old output could not tell them apart.
    """
    results = doctor.run(_broken_home(tmp_path), deep=True)
    by_name = {r.name: r for r in results}

    assert by_name["registry"].status is Status.FAIL
    for dependent in ("vendored layer A", "sensitive paths", "codex hook canary"):
        assert by_name[dependent].status is Status.SKIPPED, dependent
        assert "registry did not load" in by_name[dependent].detail


def test_an_unrelated_check_still_runs_when_the_registry_is_broken(tmp_path: Path) -> None:
    """A skip is scoped to what actually depends on the thing that failed. The state
    table and the price table do not need the registry and must still be reported."""
    names = {r.name for r in doctor.run(_broken_home(tmp_path))}

    assert "state table" in names
    assert "price table" in names


def test_a_skip_does_not_change_the_exit_code(tmp_path: Path) -> None:
    """Exit 1 on failures only, unchanged. A machine with no `sbx` installed is
    unmeasured, not broken, and an exit code that could not tell those apart would make
    the command unusable on a fresh checkout."""
    skipped_only = [
        Result("registry", Status.OK, "2 projects"),
        Result("sensitive paths", Status.SKIPPED, "registry did not load"),
    ]

    lines, code = doctor.report(skipped_only)

    assert code == 0
    assert lines[-1] == "All checks that ran passed; 1 skipped."


def test_the_summary_cannot_be_read_as_a_clean_bill_of_health(tmp_path: Path) -> None:
    """The counterweight to the exit code: the line a human reads names both numbers."""
    mixed = [
        Result("registry", Status.FAIL, "unparseable"),
        Result("routing", Status.FAIL, "no roles"),
        Result("state table", Status.FAIL, "unreachable"),
        Result("vendored layer A", Status.SKIPPED, "registry did not load"),
        Result("sensitive paths", Status.SKIPPED, "registry did not load"),
        Result("codex hook canary", Status.SKIPPED, "registry did not load"),
        Result("disk", Status.SKIPPED, "registry did not load"),
    ]

    lines, code = doctor.report(mixed)

    assert code == 1
    assert lines[-1] == "3 check(s) failed, 4 skipped."


def test_the_status_word_is_on_every_line(tmp_path: Path) -> None:
    """A skipped row that printed like a passing row would be the same bug wearing a
    different face."""
    lines, _ = doctor.report(
        [
            Result("registry", Status.OK, "2 projects"),
            Result("sensitive paths", Status.SKIPPED, "registry did not load"),
            Result("disk", Status.FAIL, "3 GB free"),
        ]
    )

    assert lines[0].startswith("OK")
    assert lines[1].startswith("SKIPPED")
    assert lines[2].startswith("FAIL")


# --------------------------------------------------------------------------------
# The config checks produce the context
# --------------------------------------------------------------------------------


def test_load_context_reports_its_own_checks_and_hands_back_what_it_loaded(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    for filename in ("projects.toml", "models.toml"):
        (tmp_path / "config" / filename).write_bytes((HOME / "config" / filename).read_bytes())
    ctx, results = doctor.load_context(tmp_path)

    assert [r.name for r in results] == ["registry", "routing", "state table", "database"]
    assert ctx.registry is not None
    assert ctx.store is not None
    assert ctx.home == tmp_path
    ctx.store.close()


def test_the_deep_canary_is_opt_in(tmp_path: Path) -> None:
    """It spends a model call, so it must not run because someone typed `factory doctor`."""
    names = {r.name for r in doctor.run(_broken_home(tmp_path))}
    assert "codex hook canary" not in names
