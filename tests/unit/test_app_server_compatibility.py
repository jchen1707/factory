"""Compatibility evidence must describe the worker that will actually execute."""

import hashlib
import json
from pathlib import Path

import pytest

from factory.agent import app_server
from factory.agent.base import AgentInvocation
from factory.machine import Blocked


def manifest(tmp_path: Path) -> Path:
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("synthetic fixture, not runtime acceptance")
    report = {
        "sandbox": "factory-build-fixture",
        "runtime_version": "fixture",
        "worker_sha256": hashlib.sha256(
            Path(app_server.__file__).with_name("app_server_worker.py").read_bytes()
        ).hexdigest(),
        "checks": {
            name: {
                "status": "pass",
                "evidence": evidence.name,
                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            }
            for name in app_server.COMPATIBILITY_CHECKS
        },
    }
    path = tmp_path / "compatibility.json"
    path.write_text(json.dumps(report))
    return path


@pytest.mark.parametrize("worker_hash", [None, "0" * 64])
def test_missing_or_stale_worker_evidence_is_refused(
    tmp_path: Path, worker_hash: str | None
) -> None:
    path = manifest(tmp_path)
    report = json.loads(path.read_text())
    if worker_hash is None:
        del report["worker_sha256"]
    else:
        report["worker_sha256"] = worker_hash
    path.write_text(json.dumps(report))
    with pytest.raises(Blocked, match="app-server-compatibility-incomplete"):
        app_server.validate_compatibility(
            path, runtime_version="fixture", sandbox="factory-build-fixture"
        )


def test_exact_worker_evidence_is_accepted(tmp_path: Path) -> None:
    path = manifest(tmp_path)
    assert (
        app_server.validate_compatibility(
            path, runtime_version="fixture", sandbox="factory-build-fixture"
        )["worker_sha256"]
        == json.loads(path.read_text())["worker_sha256"]
    )


def test_worker_change_after_selection_is_refused_before_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = app_server.AppServerAdapter(
        manifest(tmp_path), runtime_version="fixture", sandbox="factory-build-fixture"
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "app_server_worker.py").write_text("changed worker")
    monkeypatch.setattr(app_server, "__file__", str(source / "app_server.py"))
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    invocation = AgentInvocation(
        model="fixture",
        effort="low",
        workdir=str(attempt),
        prompt_path=attempt / "prompt.md",
        schema_path=attempt / "schema.json",
        output_path=attempt / "output.json",
        events_path=attempt / "events.jsonl",
        stderr_path=attempt / "stderr",
        exit_path=attempt / "exit",
        heartbeat_path=attempt / "heartbeat",
        pgid_path=attempt / "pgid",
        vault_directory=str(attempt),
    )
    with pytest.raises(Blocked, match="app-server-compatibility-incomplete"):
        adapter.prepare(invocation)
    assert not (attempt / "app_server_worker.py").exists()
    assert not invocation.prompt_path.with_suffix(".app-server.json").exists()


@pytest.mark.parametrize("reference", ["../outside.txt", "/outside.txt", "linked.txt"])
def test_evidence_cannot_escape_manifest_directory(tmp_path: Path, reference: str) -> None:
    root = tmp_path / "reports"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("unrelated evidence")
    path = manifest(root)
    (root / "linked.txt").symlink_to(outside)
    report = json.loads(path.read_text())
    check = report["checks"]["hook_enforcement"]
    check["evidence"] = str(outside) if reference == "/outside.txt" else reference
    check["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    path.write_text(json.dumps(report))
    with pytest.raises(Blocked, match="app-server-compatibility-incomplete"):
        app_server.validate_compatibility(
            path, runtime_version="fixture", sandbox="factory-build-fixture"
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("checks", list(app_server.COMPATIBILITY_CHECKS)), ("usage_scope", []), ("checks", None)],
)
def test_malformed_report_refuses_without_crashing(
    tmp_path: Path, field: str, value: object
) -> None:
    path = manifest(tmp_path)
    report = json.loads(path.read_text())
    report[field] = value
    path.write_text(json.dumps(report))
    with pytest.raises(Blocked, match="app-server-compatibility-incomplete"):
        app_server.validate_compatibility(
            path, runtime_version="fixture", sandbox="factory-build-fixture"
        )


def test_evidence_change_after_selection_is_refused_before_preparation(tmp_path: Path) -> None:
    path = manifest(tmp_path)
    adapter = app_server.AppServerAdapter(
        path, runtime_version="fixture", sandbox="factory-build-fixture"
    )
    (tmp_path / "evidence.txt").write_text("changed after adapter selection")
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    invocation = AgentInvocation(
        model="fixture",
        effort="low",
        workdir=str(attempt),
        prompt_path=attempt / "prompt.md",
        schema_path=attempt / "schema.json",
        output_path=attempt / "output.json",
        events_path=attempt / "events.jsonl",
        stderr_path=attempt / "stderr",
        exit_path=attempt / "exit",
        heartbeat_path=attempt / "heartbeat",
        pgid_path=attempt / "pgid",
        vault_directory=str(attempt),
    )
    with pytest.raises(Blocked, match="app-server-compatibility-incomplete"):
        adapter.prepare(invocation)
    assert not (attempt / "app_server_worker.py").exists()


def test_certified_native_binding_survives_preparation_for_resume(tmp_path: Path) -> None:
    path = manifest(tmp_path)
    report = json.loads(path.read_text())
    report["certification"] = {
        "identity": {
            "runtime_path": "/opt/codex/native",
            "runtime_sha256": "a" * 64,
            "launcher_sha256": "b" * 64,
            "code_host_sha256": "e" * 64,
            "runtime_version": "fixture",
            "sandbox": "factory-build-fixture",
            "worker_sha256": report["worker_sha256"],
        }
    }
    path.write_text(json.dumps(report))
    adapter = app_server.AppServerAdapter(
        path, runtime_version="fixture", sandbox="factory-build-fixture"
    )
    invocation = AgentInvocation(
        model="fixture",
        effort="high",
        workdir=str(tmp_path),
        prompt_path=tmp_path / "prompt.md",
        schema_path=tmp_path / "schema.json",
        output_path=tmp_path / "output.json",
        events_path=tmp_path / "events.jsonl",
        stderr_path=tmp_path / "stderr",
        exit_path=tmp_path / "exit",
        heartbeat_path=tmp_path / "heartbeat",
        pgid_path=tmp_path / "pgid",
        vault_directory=str(tmp_path),
        resume_session="retained-thread",
    )
    adapter.prepare(invocation)
    request = json.loads(invocation.prompt_path.with_suffix(".app-server.json").read_text())
    assert request["runtime_identity"] == {
        "runtime_path": "/opt/codex/native",
        "runtime_sha256": "a" * 64,
        "launcher_sha256": "b" * 64,
        "code_host_sha256": "e" * 64,
    }
    assert request["resume_session"] == "retained-thread"


@pytest.mark.parametrize(
    "binding",
    [
        None,
        {},
        {"identity": {}},
        {"identity": {"runtime_path": "codex", "runtime_sha256": "a" * 64}},
    ],
)
def test_malformed_certification_binding_cannot_become_manual(
    tmp_path: Path, binding: object
) -> None:
    path = manifest(tmp_path)
    report = json.loads(path.read_text())
    report["certification"] = binding
    path.write_text(json.dumps(report))
    with pytest.raises(Blocked, match="app-server-compatibility-incomplete"):
        app_server.AppServerAdapter(
            path, runtime_version="fixture", sandbox="factory-build-fixture"
        )
