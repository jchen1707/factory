"""The `.agents/` copy of the plan must be the plan, byte for byte.

`/SOFTWARE-FACTORY-PLAN.md` is the authority; the copy exists for agents and tools that can
read `.agents/` but not the repository root. The previous file at that path was a *condensed*
rewrite — it said almost the same thing in slightly different words, so a reader could not
tell it had gone stale, and it misled two sessions before `9e33944` deleted it.

A verbatim copy can only go stale, and staleness is checkable. This is that check, in the
suite as well as in `factory doctor`, so a plan edit that forgets the copy fails CI rather
than waiting for somebody to run `doctor`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from factory import doctor
from factory.doctor import DoctorContext

HOME = Path(__file__).resolve().parents[2]
SCRIPT = HOME / "scripts" / "sync_plan_copy.py"


def test_the_agents_copy_is_the_canonical_plan() -> None:
    """The regression. If this fails, run `python3 scripts/sync_plan_copy.py`."""
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stdout + done.stderr


def test_a_copy_that_has_fallen_behind_is_detected(tmp_path: Path) -> None:
    """Drift is the only way this copy can go wrong, so it is the thing that must be caught.

    Exercised against a scratch tree rather than the repository, so the test never edits the
    plan it is protecting.
    """
    sync = _load_sync(tmp_path)
    (tmp_path / "SOFTWARE-FACTORY-PLAN.md").write_text("the plan, version one\n", encoding="utf-8")
    sync.main_write()
    assert sync.check()[0]

    (tmp_path / "SOFTWARE-FACTORY-PLAN.md").write_text("the plan, version two\n", encoding="utf-8")

    ok, detail = sync.check()
    assert not ok
    assert "fallen behind" in detail


def test_a_missing_copy_is_detected(tmp_path: Path) -> None:
    sync = _load_sync(tmp_path)
    (tmp_path / "SOFTWARE-FACTORY-PLAN.md").write_text("the plan\n", encoding="utf-8")

    ok, detail = sync.check()

    assert not ok
    assert "missing" in detail


def test_a_copy_with_no_header_is_detected(tmp_path: Path) -> None:
    """A hand-made copy carries no generated header, and a reader has no way to know it is a
    copy at all — which is precisely how the deleted one did its damage."""
    sync = _load_sync(tmp_path)
    (tmp_path / "SOFTWARE-FACTORY-PLAN.md").write_text("the plan\n", encoding="utf-8")
    copy = tmp_path / ".agents" / "plans" / "software-factory-plan.md"
    copy.parent.mkdir(parents=True)
    copy.write_text("the plan\n", encoding="utf-8")

    ok, detail = sync.check()

    assert not ok
    assert "header" in detail


def test_doctor_reports_rather_than_fails_when_the_script_is_absent(tmp_path: Path) -> None:
    """`doctor` runs against fixture homes with no scripts directory. A tree with no copy to
    keep current has nothing to be stale, and must not be reported as broken."""
    (result,) = doctor.check("plan copy").run(DoctorContext(home=tmp_path))

    assert result.name == "plan copy"
    assert result.ok
    assert "no scripts" in result.detail


def _load_sync(root: Path):  # type: ignore[no-untyped-def]
    """`sync_plan_copy` rebound to a scratch root, so the tests never touch the real plan."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"sync_plan_copy_{root.name}", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # `module` is a dynamically-loaded `ModuleType`; ROOT/CANONICAL/COPY/main_write are
    # injected here so the tests never touch the real plan. mypy cannot see runtime
    # attribute injection on a ModuleType, so these are silenced, not declared.
    module.ROOT = root  # type: ignore[attr-defined]
    module.CANONICAL = root / "SOFTWARE-FACTORY-PLAN.md"  # type: ignore[attr-defined]
    module.COPY = root / ".agents" / "plans" / "software-factory-plan.md"  # type: ignore[attr-defined]

    def main_write() -> None:
        module.COPY.parent.mkdir(parents=True, exist_ok=True)
        module.COPY.write_text(module.render(module.canonical_text()), encoding="utf-8")

    module.main_write = main_write  # type: ignore[attr-defined]
    return module
