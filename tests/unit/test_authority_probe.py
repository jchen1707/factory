"""The in-VM write probe for captured authority, read from real captures and run for real.

`tests/fixtures/sbx/authority-*.json` are the probe's stdout from a throwaway `sbx create
claude` sandbox on 2026-10-07, with a `:ro` share, a writable share, a mode-0444 file on the
`:ro` share, and a writable bind mount nested under it. `EROFS` cannot be produced on a
host that does not mount read-only, so the script's other outcomes are proved by running it
here against a temporary directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from factory.sandbox import authority_probe
from factory.sandbox.authority_probe import ProbeError
from factory.sandbox.base import Completed

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sbx"


def _fixture(name: str) -> str:
    return (FIXTURES / f"authority-{name}.json").read_text(encoding="utf-8")


def _paths(stdout: str) -> list[str]:
    return list(json.loads(stdout))


@pytest.mark.parametrize(
    ("capture", "unprotected"),
    [
        ("read-only", []),
        ("read-only-mode-0444", ["readonly-perm.md: EACCES"]),
        (
            "writable",
            ["readonly-perm.md: EACCES", "snapshot.json: writable", "./: writable"],
        ),
        ("nested-writable", [".claude/settings.json: writable", ".claude/: writable"]),
    ],
)
def test_only_erofs_on_every_path_passes(capture: str, unprotected: list[str]) -> None:
    stdout = _fixture(capture)
    observed = authority_probe.parse(stdout, _paths(stdout))
    assert authority_probe.unprotected(observed) == unprotected


def test_targets_are_every_file_then_every_directory_holding_one() -> None:
    assert authority_probe.targets([".claude/settings.json", "snapshot.json", "a/b/c.md"]) == [
        ".claude/settings.json",
        "a/b/c.md",
        "snapshot.json",
        "./",
        ".claude/",
        "a/",
        "a/b/",
    ]


def test_a_start_up_line_before_the_answer_is_not_the_probe_s() -> None:
    stdout = _fixture("read-only")
    started = "Sandbox factory-x started successfully\n" + stdout
    assert authority_probe.parse(started, _paths(stdout)) == json.loads(stdout)


@pytest.mark.parametrize(
    "stdout",
    ["", "node: bad option\n", '["EROFS"]\n', '{"snapshot.json": 30}\n'],
    ids=["empty", "not-json", "not-an-object", "not-a-string"],
)
def test_an_unreadable_answer_is_an_error_never_a_pass(stdout: str) -> None:
    with pytest.raises(ProbeError, match="printed no answer"):
        authority_probe.parse(stdout, ["snapshot.json"])


def test_an_answer_for_other_paths_is_an_error_never_a_pass() -> None:
    stdout = _fixture("read-only")
    with pytest.raises(ProbeError, match="answered for 5 path"):
        authority_probe.parse(stdout, [*_paths(stdout), "extra.md"])


class _Sandbox:
    def __init__(self, result: Completed | subprocess.TimeoutExpired) -> None:
        self.result = result
        self.stdin: str | None = None

    def exec_sync(self, name: str, argv: list[str], **kwargs: object) -> Completed:
        self.stdin = kwargs.get("stdin")  # type: ignore[assignment]
        if isinstance(self.result, subprocess.TimeoutExpired):
            raise self.result
        return self.result


def test_observe_sends_the_root_and_paths_and_parses_the_answer() -> None:
    stdout = _fixture("read-only")
    sandbox = _Sandbox(Completed((), 0, stdout, ""))
    observed = authority_probe.observe(sandbox, "factory-x", "/state/authority", _paths(stdout))  # type: ignore[arg-type]
    assert json.loads(sandbox.stdin or "") == {"root": "/state/authority", "paths": _paths(stdout)}
    assert authority_probe.unprotected(observed) == []


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (Completed((), 127, "", "sh: 1: node: not found\n"), "exited 127: sh: 1: node: not found"),
        (subprocess.TimeoutExpired(["sbx"], 120), "timed out after 120s"),
    ],
    ids=["no-node", "timeout"],
)
def test_a_probe_that_did_not_run_is_an_error(
    result: Completed | subprocess.TimeoutExpired, message: str
) -> None:
    with pytest.raises(ProbeError, match=message):
        authority_probe.observe(_Sandbox(result), "factory-x", "/r", ["snapshot.json"])  # type: ignore[arg-type]


def _digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else "dir"
        for path in sorted(root.rglob("*"))
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="the probe runs under node")
def test_the_real_script_reports_a_writable_tree_and_leaves_it_as_it_was(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}\n")
    (tmp_path / "snapshot.json").write_text('{"frozen": true}\n')
    (tmp_path / "linked.md").symlink_to(tmp_path / "snapshot.json")
    (tmp_path / "not-a-dir").write_text("file\n")
    paths = [
        *authority_probe.targets([".claude/settings.json", "snapshot.json", "linked.md"]),
        "missing.md",
        "not-a-dir/",
    ]
    before = _digest(tmp_path)

    done = subprocess.run(
        authority_probe.probe_argv(),
        input=json.dumps({"root": str(tmp_path), "paths": paths}),
        capture_output=True,
        text=True,
        check=True,
    )

    assert authority_probe.parse(done.stdout, paths) == {
        ".claude/settings.json": "writable",
        "snapshot.json": "writable",
        "linked.md": "not-a-file",
        "./": "writable",
        ".claude/": "writable",
        "missing.md": "ENOENT",
        "not-a-dir/": "not-a-directory",
    }
    assert _digest(tmp_path) == before


@pytest.mark.skipif(shutil.which("node") is None, reason="the probe runs under node")
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores mode bits")
def test_the_real_script_reports_mode_bits_as_eacces_not_as_read_only(tmp_path: Path) -> None:
    frozen = tmp_path / "frozen.md"
    frozen.write_text("frozen\n")
    frozen.chmod(0o444)

    done = subprocess.run(
        authority_probe.probe_argv(),
        input=json.dumps({"root": str(tmp_path), "paths": ["frozen.md"]}),
        capture_output=True,
        text=True,
        check=True,
    )

    assert authority_probe.unprotected(authority_probe.parse(done.stdout, ["frozen.md"])) == [
        "frozen.md: EACCES"
    ]
