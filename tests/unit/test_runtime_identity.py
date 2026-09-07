"""Native runtime identity, independent of a wrapper's advertised version."""

from pathlib import Path

import pytest

from factory.agent.runtime_identity import identify


def test_wrapper_is_refused_without_executing_it(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    wrapper = tmp_path / "codex"
    wrapper.write_text(f"#!/bin/sh\ntouch {marker}\necho codex-cli 0.153.4\n")
    wrapper.chmod(0o755)
    with pytest.raises(ValueError, match="native ELF"):
        identify(str(wrapper))
    assert not marker.exists()


def test_same_version_does_not_hide_binary_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    binary = tmp_path / "codex"
    binary.write_bytes(b"\x7fELFfirst native fixture")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "codex-cli 0.153.4\n"),
    )
    first = identify(str(binary))
    binary.write_bytes(b"\x7fELFsecond native fixture")
    second = identify(str(binary))
    assert first["runtime_version"] == second["runtime_version"] == "codex-cli 0.153.4"
    assert first["runtime_sha256"] != second["runtime_sha256"]


def test_replacement_during_version_query_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    binary = tmp_path / "codex"
    binary.write_bytes(b"\x7fELFfirst native fixture")

    def replace(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        replacement = tmp_path / "replacement"
        replacement.write_bytes(b"\x7fELFchanged native fixture")
        replacement.replace(binary)
        return subprocess.CompletedProcess([], 0, "codex-cli 0.153.4")

    monkeypatch.setattr(subprocess, "run", replace)
    with pytest.raises(ValueError, match="changed during observation"):
        identify(str(binary))


def test_fifo_is_refused_without_waiting_for_a_writer(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    fifo = tmp_path / "codex"
    os.mkfifo(fifo)
    result = subprocess.run(
        [sys.executable, "-m", "factory.agent.runtime_identity", str(fifo)],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    assert result.returncode == 1
    assert '"error": "runtime-identity-unavailable"' in result.stdout
