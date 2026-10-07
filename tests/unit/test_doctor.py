"""§20.5 — `factory doctor` as a module, and the third status it needed.

The bug under test is not a wrong answer, it is a missing one: three check families were
written behind `if registry:` in an argparse handler, so a broken `projects.toml` made
them **vanish from the output**. Fewer lines, no failures, exit 0 — a doctor that reports
a cleaner machine the more broken the machine is.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest

from factory import doctor
from factory.doctor import CHECKS, DoctorContext, Result, Status
from factory.sandbox.base import Completed
from factory.sandbox.sbx import SbxError
from tests.support import plugin_cache

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
    for dependent in ("vendored layer A", "sensitive paths", "layer-A hooks", "claude live probe"):
        assert by_name[dependent].status is Status.SKIPPED, dependent
        assert "registry did not load" in by_name[dependent].detail


def test_an_unrelated_check_still_runs_when_the_registry_is_broken(tmp_path: Path) -> None:
    """A skip is scoped to what actually depends on the thing that failed. The state
    table and the plan copy do not need the registry and must still be reported."""
    names = {r.name for r in doctor.run(_broken_home(tmp_path))}

    assert "state table" in names
    assert "plan copy" in names


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
        Result("claude live probe", Status.SKIPPED, "registry did not load"),
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


def test_the_live_probe_is_opt_in(tmp_path: Path) -> None:
    """It spends model calls, so it must not run because someone typed `factory doctor`."""
    names = {r.name for r in doctor.run(_broken_home(tmp_path))}
    assert "claude live probe" not in names


def _home(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    for filename in ("projects.toml", "models.toml"):
        (tmp_path / "config" / filename).write_bytes((HOME / "config" / filename).read_bytes())
    return tmp_path


def test_the_live_probe_without_sbx_login_says_what_to_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    measured = (
        "ERROR: list sandboxes: list local runtimes: list runtimes: request failed: 401 "
        "Unauthorized: user is not authenticated to Docker: secret not found\n"
        "no valid user session found, please sign in to Docker to proceed\n\n"
        "Sign in with: sbx login"
    )
    monkeypatch.setattr(doctor, "sbx_available", lambda: (False, measured))
    ctx, _ = doctor.load_context(_home(tmp_path), deep=True)

    results = doctor.check("claude live probe").run(ctx)

    assert [(r.name, r.status) for r in results] == [("live: sandbox", Status.FAIL)]
    assert "Run `sbx login`, then `factory doctor --deep` again." in results[0].detail
    assert ctx.store is not None
    ctx.store.close()


#: `sbx secret ls` on 2026-10-07: no anthropic entry in any scope.
SBX_LISTING = """SCOPE                    TYPE      NAME     SECRET
codex-factory            service   github   (stored)
(global)                 service   openai   (oauth configured)

CUSTOM SECRETS
SCOPE                        TARGETS          ENV                    PLACEHOLDER               SECRET
factory-build-nemoclaw-dev   172.18.194.183   FACTORY_GITLAB_TOKEN   sbx-cs-veqNHLin40GtZzi2   glpat-***
"""


def test_no_global_anthropic_row_is_unknown_not_a_failure() -> None:
    result = doctor.anthropic_credential(SBX_LISTING)
    assert result.status is Status.SKIPPED
    assert "unmeasured" in result.detail
    assert "factory doctor --deep" in result.detail


def test_a_global_anthropic_row_passes_without_claiming_the_token_works() -> None:
    listed = SBX_LISTING.replace(
        "(global)                 service   openai",
        "(global)                 service   anthropic   (stored)\n(global) service openai",
    )
    result = doctor.anthropic_credential(listed)
    assert result.status is Status.OK
    assert "(global) service anthropic (stored)" in result.detail
    assert "validity" in result.detail


def test_an_anthropic_row_scoped_to_one_sandbox_does_not_pass() -> None:
    scoped = SBX_LISTING.replace(
        "codex-factory            service   github",
        "factory-build-x          service   anthropic",
    )
    result = doctor.anthropic_credential(scoped)
    assert result.status is Status.SKIPPED
    assert "scoped to one sandbox only (factory-build-x service anthropic" in result.detail


def test_a_custom_secret_named_anthropic_is_not_the_model_credential() -> None:
    custom = SBX_LISTING + "(global)   api.example   anthropic   sbx-cs-abc   ***\n"
    assert doctor.anthropic_credential(custom).status is Status.SKIPPED


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@x.invalid", *args],
        check=True,
        capture_output=True,
    )


def test_the_hooks_row_fails_a_project_whose_base_ref_does_not_wire_them(tmp_path: Path) -> None:
    (tmp_path / "home").mkdir()
    ctx, _ = doctor.load_context(_home(tmp_path / "home"))
    assert ctx.registry is not None
    assert ctx.store is not None
    wired, unwired = tmp_path / "wired", tmp_path / "unwired"
    for repo in (wired, unwired):
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
    (wired / ".claude").mkdir()
    (wired / ".claude" / "settings.json").write_bytes((HOME / ".claude/settings.json").read_bytes())
    _git(wired, "add", "-A")
    for repo in (wired, unwired):
        _git(repo, "commit", "-q", "--allow-empty", "-m", "seed")
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    first = next(iter(ctx.registry.projects.values()))
    projects = {
        "wired": dataclasses.replace(first, name="wired", path=wired, base_branch="main"),
        "unwired": dataclasses.replace(first, name="unwired", path=unwired, base_branch="main"),
        "unfetched": dataclasses.replace(first, name="unfetched", path=unwired, base_branch="x"),
    }
    registry = dataclasses.replace(ctx.registry, projects=projects)

    rows = {
        r.name: r
        for r in doctor.check("layer-A hooks").run(dataclasses.replace(ctx, registry=registry))
    }

    assert rows["layer-A hooks in wired"].status is Status.OK
    assert rows["layer-A hooks in unwired"].status is Status.FAIL
    assert ".claude/settings.json is absent" in rows["layer-A hooks in unwired"].detail
    assert rows["layer-A hooks in unfetched"].status is Status.FAIL
    assert "origin/x does not resolve" in rows["layer-A hooks in unfetched"].detail
    ctx.store.close()


@pytest.mark.parametrize(
    ("version", "status", "detail"),
    [
        ("1.2.3", Status.OK, "2 skills from mattpocock-skills 1.2.3"),
        ("9.9.9", Status.FAIL, "mattpocock-skills 9.9.9 is not installed"),
    ],
)
def test_the_doctrine_row_checks_every_declared_skill_against_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, status: Status, detail: str
) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "doctrine.toml").write_text(
        "[sources.mattpocock-skills]\nmarketplace = 'claude-plugins-official'\n"
        f"version = '{version}'\nskills = ['tdd', 'code-review']\n"
    )
    monkeypatch.setattr(doctor, "PLUGIN_CACHE", plugin_cache.mattpocock(tmp_path / "cache"))

    rows = {r.name: r for r in doctor.check("doctrine").run(DoctorContext(home=tmp_path))}

    assert rows["doctrine"].status is status
    assert detail in rows["doctrine"].detail


class _BuildVMs:
    """`sbx ls` and `sbx exec` for the build-VM disk row, without a VM."""

    def __init__(self, existing: set[str], stdout: str, returncode: int = 0) -> None:
        self.existing, self.stdout, self.returncode = existing, stdout, returncode
        self.probed: list[str] = []

    def names(self) -> set[str]:
        return self.existing

    def exec_sync(self, name: str, argv: list[str], **_: object) -> Completed:
        self.probed.append(name)
        return Completed(tuple(argv), self.returncode, self.stdout, "")


def _vm_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vms: _BuildVMs) -> dict[str, Result]:
    ctx, _ = doctor.load_context(_home(tmp_path))
    assert ctx.registry is not None
    assert ctx.store is not None
    first = next(iter(ctx.registry.projects.values()))
    registry = dataclasses.replace(
        ctx.registry,
        projects={
            "full": dataclasses.replace(first, name="full", build_sandbox="factory-build-full"),
            "new": dataclasses.replace(first, name="new", build_sandbox="factory-build-new"),
        },
    )
    monkeypatch.setattr(doctor, "SbxAdapter", lambda: vms)
    rows = doctor.check("build VM disk").run(dataclasses.replace(ctx, registry=registry))
    ctx.store.close()
    return {r.name: r for r in rows}


def test_the_build_vm_row_fails_an_inode_exhausted_vm_and_passes_an_absent_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exhausted = (HOME / "tests" / "fixtures" / "sbx" / "df-inodes-exhausted.txt").read_text()
    vms = _BuildVMs({"factory-build-full"}, exhausted)

    rows = _vm_rows(tmp_path, monkeypatch, vms)

    assert rows["build VM disk for full"].status is Status.FAIL
    assert "/home/agent on /: 0 inodes free, floor 50,000" in rows["build VM disk for full"].detail
    assert rows["build VM disk for new"].status is Status.OK
    assert vms.probed == ["factory-build-full"]


def test_a_build_vm_that_cannot_be_measured_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Measured: `sbx exec` on a stopped VM at zero free inodes could not start it.
    vms = _BuildVMs({"factory-build-full"}, "", returncode=1)
    rows = _vm_rows(tmp_path, monkeypatch, vms)
    assert rows["build VM disk for full"].status is Status.FAIL
    assert "exited 1" in rows["build VM disk for full"].detail


def test_the_build_vm_row_is_skipped_without_sbx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _NoSbx:
        def names(self) -> set[str]:
            raise SbxError("sbx ls failed: not signed in")

    monkeypatch.setattr(doctor, "SbxAdapter", _NoSbx)
    ctx, _ = doctor.load_context(_home(tmp_path))
    rows = doctor.check("build VM disk").run(ctx)
    assert ctx.store is not None
    ctx.store.close()
    assert rows
    assert {r.status for r in rows} == {Status.SKIPPED}
    assert "not signed in" in rows[0].detail
