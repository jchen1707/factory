"""gc's in-VM removal script, run for real. GNU coreutils only, as in the sbx agent image."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Literal

import pytest

from factory import gc

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="the script runs inside a Linux VM; it needs GNU rm"
)


def _run(mode: Literal["list", "remove"], paths: list[Path]) -> list[str]:
    done = subprocess.run(
        gc.vm_dir_argv(mode, [str(p) for p in paths]), capture_output=True, text=True, check=True
    )
    return done.stdout.splitlines()


def _tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path.resolve()
    venvs, victim = root / "venvs", root / "workspace"
    (venvs / "BAC-1" / "lib").mkdir(parents=True)
    (venvs / "BAC-1" / "lib" / "site.py").write_text("x")
    for ticket in ("BAC-2", "BAC-3"):
        (victim / ticket).mkdir(parents=True)
        (victim / ticket / "work.py").write_text("keep")
    (venvs / "BAC-2").symlink_to(victim / "BAC-2")
    (root / "linked").symlink_to(victim)
    return venvs, victim


def test_only_a_literal_directory_is_removed(tmp_path: Path) -> None:
    venvs, victim = _tree(tmp_path)
    linked = tmp_path.resolve() / "linked" / "BAC-3"

    parent = f"{venvs}/BAC-1/.."

    out = _run(
        "remove", [Path(parent), venvs / "BAC-1", venvs / "BAC-2", linked, venvs / "BAC-404"]
    )

    assert out == [
        f"refused {parent}",
        f"removed {venvs / 'BAC-1'}",
        f"refused {venvs / 'BAC-2'}",
        f"refused {linked}",
    ]
    assert venvs.is_dir()
    assert not (venvs / "BAC-1").exists()
    assert (victim / "BAC-2" / "work.py").read_text() == "keep"
    assert (victim / "BAC-3" / "work.py").read_text() == "keep"


def test_list_mode_removes_nothing(tmp_path: Path) -> None:
    venvs, _ = _tree(tmp_path)

    assert _run("list", [venvs / "BAC-1"]) == [f"present {venvs / 'BAC-1'}"]
    assert (venvs / "BAC-1" / "lib" / "site.py").exists()
