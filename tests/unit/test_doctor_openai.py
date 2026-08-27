"""`doctor`'s sbx-OpenAI-credential check — the one that makes a silent factory-wide
failure visible before a run is spent on it.

The defect this file exists for cost an hour in Phase 5 and blocked every run of both
stacks while the host looked healthy. A sandboxed agent authenticates through `sbx`'s
proxy against a *globally stored* OAuth token (`(global) service openai (oauth
configured)`); when it expires or is absent every sandboxed run 401s and `codex login`
fixes nothing — the fix is `sbx secret set openai --oauth`. Nothing in `factory doctor`
looked at it, so the only symptom was a `401 token_expired` buried in a per-axis
transcript. This check catches the absence case (a fresh machine, a reset, a removed
secret); `sbx secret ls` cannot distinguish a live token from an expired one, so expiry
still needs a live probe, which the check says in its detail line.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from factory import doctor
from factory.doctor import DoctorContext

_PRESENT = """\
SCOPE                    TYPE      NAME     SECRET
codex-python-harness     service   github   (stored)
(global)                 service   openai   (oauth configured)
"""

_ABSENT = """\
SCOPE                    TYPE      NAME     SECRET
codex-python-harness     service   github   (stored)
"""


def test_a_configured_global_openai_token_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.subprocess, "run", _capture(_PRESENT))
    result = _only()
    name, ok, detail = result.name, result.ok, result.detail
    assert ok
    assert "openai" in name
    assert "oauth configured" in detail


def test_no_global_openai_row_fails_and_names_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact shape of the Phase 5 failure: `sbx secret ls` runs fine and reports
    other services, but the `(global) openai` row is gone. Doctor must say so and point
    at the command that fixes it."""
    monkeypatch.setattr(doctor.subprocess, "run", _capture(_ABSENT))
    result = _only()
    ok, detail = result.ok, result.detail
    assert not ok
    assert "sbx secret set openai --oauth" in detail


def test_a_nonzero_sbx_secret_ls_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """`sbx` itself broken or not logged in must not read as a missing OpenAI secret —
    that would send the user to fix the wrong thing."""

    def _run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="not authenticated")

    monkeypatch.setattr(doctor.subprocess, "run", _run)
    result = _only()
    ok, detail = result.ok, result.detail
    assert not ok
    assert "not authenticated" in detail
    assert "sbx secret set openai" not in detail


def _capture(stdout: str) -> Callable[..., subprocess.CompletedProcess[str]]:
    def _run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        assert argv[:3] == ["sbx", "secret", "ls"], argv
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    return _run


def _only() -> doctor.Result:
    """The check, run the way `doctor.run` runs it.

    Named lookup through `doctor.check` rather than an imported `cli` private: three of
    these check families were tested past the interface, which is exactly what giving
    every check one shape was for.
    """
    results = doctor.check("openai credential (sbx)").run(DoctorContext(home=Path(".")))
    assert len(results) == 1
    return results[0]
