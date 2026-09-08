"""Frozen commands use retained source, with no staged helper imports."""

import subprocess
from pathlib import Path

import pytest

from factory.agent.certified_command import command
from factory.machine import Blocked


def test_usage_command_executes_retained_helpers_after_source_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def read(path: Path) -> str:
        if path.name == "app_server_worker.py":
            return "def answer(): return 'retained helper'\n"
        assert path.name == "certification_usage_worker.py"
        return "def run(request):\n print(helpers().answer())\n return 0\n"

    monkeypatch.setattr(Path, "read_text", read)
    argv = command({"stage": "initial"}, usage=True)
    monkeypatch.setattr(Path, "read_text", lambda path: "raise RuntimeError('tampered')")
    result = subprocess.run(argv, check=True, capture_output=True, text=True, timeout=10)
    assert result.stdout.strip() == "retained helper"
    assert argv[:4] == ["/usr/bin/python3", "-I", "-S", "-c"]


def test_large_incompressible_request_refuses_before_execution() -> None:
    import hashlib

    content = "".join(hashlib.sha256(str(n).encode()).hexdigest() for n in range(5_000))
    with pytest.raises(Blocked, match="certification-request-too-large"):
        command({}, prompt=content, schema="{}")
