"""The build VM's free bytes and inodes, read from real `df` captures.

`tests/fixtures/sbx/` holds the probe's stdout from a throwaway `sbx create claude`
sandbox on 2026-10-07 (Ubuntu 26.04, GNU coreutils 9.7), with the host workspace path
shortened. `df-inodes-exhausted.txt` was taken after filling that VM's root with empty
files: 0 inodes free and 19.8 GB still available, the BAC-60 shape.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from factory.sandbox import vm_disk
from factory.sandbox.base import Completed
from factory.sandbox.vm_disk import Free, ProbeError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sbx"
PATHS = ("/private/tmp/inode-probe-ws", vm_disk.VM_HOME)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_reads_available_bytes_and_free_inodes_per_path() -> None:
    assert vm_disk.parse(_fixture("df-fresh.txt"), PATHS) == (
        Free(PATHS[0], "/private/tmp/inode-probe-ws", 126611120 * 1024, 1266110280),
        Free(vm_disk.VM_HOME, "/", 19357880 * 1024, 1310620),
    )


def test_an_inode_exhausted_vm_with_bytes_to_spare_is_below_the_floor() -> None:
    rows = vm_disk.parse(_fixture("df-inodes-exhausted.txt"), PATHS)
    assert vm_disk.shortfalls(rows, min_free_gb=2, min_free_inodes=50_000) == [
        "/home/agent on /: 0 inodes free, floor 50,000"
    ]


def test_a_fresh_vm_clears_both_floors() -> None:
    rows = vm_disk.parse(_fixture("df-fresh.txt"), PATHS)
    assert vm_disk.shortfalls(rows, min_free_gb=2, min_free_inodes=50_000) == []


def test_the_byte_floor_is_checked_on_its_own() -> None:
    rows = vm_disk.parse(_fixture("df-fresh.txt"), PATHS)
    assert vm_disk.shortfalls(rows, min_free_gb=20, min_free_inodes=0) == [
        "/home/agent on /: 19.8 GB free, floor 20 GB"
    ]


def test_a_clone_workspace_on_the_root_mount_is_reported_once() -> None:
    lines = _fixture("df-inodes-exhausted.txt").splitlines()
    lines[1], lines[4] = lines[2], lines[5]
    rows = vm_disk.parse("\n".join(lines), ("/Users/x/project", vm_disk.VM_HOME))
    assert vm_disk.shortfalls(rows, min_free_gb=2, min_free_inodes=50_000) == [
        "/Users/x/project on /: 0 inodes free, floor 50,000"
    ]
    assert vm_disk.summary(rows) == "/: 19.8 GB and 0 inodes free"


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        _fixture("df-fresh.txt").split("Filesystem         Inodes")[0],
        "\n".join(_fixture("df-fresh.txt").splitlines()[:-1]),
        _fixture("df-fresh.txt").replace("1310620", "-"),
        _fixture("df-fresh.txt").replace("1024-blocks", "512-blocks"),
    ],
    ids=["empty", "no-inode-table", "missing-row", "non-numeric", "wrong-unit"],
)
def test_output_that_is_not_two_complete_tables_is_refused(stdout: str) -> None:
    with pytest.raises(ProbeError):
        vm_disk.parse(stdout, PATHS)


class _Sandbox:
    def __init__(self, completed: Completed) -> None:
        self.completed = completed
        self.argv: Sequence[str] = ()

    def exec_sync(
        self,
        name: str,
        argv: Sequence[str],
        *,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int | None = None,
        stdin: str | None = None,
    ) -> Completed:
        self.argv = argv
        return self.completed


def test_measure_runs_one_df_over_every_path() -> None:
    sandbox = _Sandbox(Completed((), 0, _fixture("df-fresh.txt"), ""))
    rows = vm_disk.measure(sandbox, "factory-build-x", PATHS)  # type: ignore[arg-type]
    assert [row.path for row in rows] == list(PATHS)
    assert sandbox.argv == ["/bin/sh", "-c", 'df -Pk "$@" && df -Pi "$@"', "sh", *PATHS]


def test_a_failed_df_is_an_error_not_a_pass() -> None:
    # Measured: a missing path makes `df -Pk` exit 1, so `&&` skips the inode table.
    failed = Completed((), 1, "", "df: /nonexistent: No such file or directory\n")
    with pytest.raises(ProbeError, match="exited 1: df: /nonexistent"):
        vm_disk.measure(_Sandbox(failed), "factory-build-x", PATHS)  # type: ignore[arg-type]
