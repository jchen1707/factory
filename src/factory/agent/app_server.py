"""Opt-in app-server adapter. Compatibility evidence is required before execution."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Sequence
from pathlib import Path

from factory.agent.base import AgentInvocation
from factory.agent.codex import CodexAdapter
from factory.machine import Blocked

COMPATIBILITY_CHECKS = frozenset(
    {
        "hook_enforcement",
        "schema_output",
        "sandbox_isolation",
        "detached_durability",
        "recovery",
        "usage_semantics",
    }
)


def validate_compatibility(path: Path, *, runtime_version: str, sandbox: str) -> dict:
    try:
        report = json.loads(read_evidence(path.parent, path.name))
        _validate_worker(report, Path(__file__).with_name("app_server_worker.py").read_bytes())
        if report.get("usage_scope", "thread") not in {"thread", "connection"}:
            raise ValueError("unknown usage counter scope")
        if report["runtime_version"] != runtime_version or report["sandbox"] != sandbox:
            raise ValueError("runtime or sandbox changed")
        if not isinstance(report["checks"], dict) or set(report["checks"]) != COMPATIBILITY_CHECKS:
            raise ValueError("compatibility checks incomplete")
        for name, check in report["checks"].items():
            evidence = read_evidence(path.parent, check["evidence"])
            if check["status"] != "pass" or hashlib.sha256(evidence).hexdigest() != check["sha256"]:
                raise ValueError(f"unverified evidence for {name}")
        return report
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Blocked("app-server-compatibility-incomplete", str(exc)) from exc


def read_evidence(root: Path, reference: str) -> bytes:
    """Read a bounded regular file beneath a controller-selected report directory.

    Open each component relative to its directory descriptor without following links,
    so a path replacement cannot race the containment check. The root is host-owned.
    """
    if not isinstance(reference, str) or not reference or "\x00" in reference:
        raise ValueError("invalid evidence reference")
    relative = Path(reference)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("evidence must stay beneath its report directory")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(
            relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("evidence must be a regular file")
            data = stream.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024:
                raise ValueError("evidence exceeds size limit")
            return data
    finally:
        os.close(directory)


def _validate_worker(report: dict, worker: bytes) -> None:
    if (
        not isinstance(report, dict)
        or report.get("worker_sha256") != hashlib.sha256(worker).hexdigest()
    ):
        raise Blocked("app-server-compatibility-incomplete", "worker changed or hash missing")


class AppServerAdapter(CodexAdapter):
    def __init__(
        self,
        compatibility: Path,
        *,
        runtime_version: str,
        sandbox: str,
        baselines: dict[str, dict[str, int]] | None = None,
    ) -> None:
        self.compatibility = compatibility
        self.runtime_version = runtime_version
        self.sandbox = sandbox
        self.baselines = baselines or {}
        self.report = validate_compatibility(
            compatibility, runtime_version=runtime_version, sandbox=sandbox
        )

    def command(self, invocation: AgentInvocation) -> Sequence[str]:
        return [
            "python3",
            str(invocation.prompt_path.parent / "app_server_worker.py"),
            str(invocation.prompt_path.with_suffix(".app-server.json")),
        ]

    def prepare(self, invocation: AgentInvocation, *, readonly: bool = False) -> None:
        report = validate_compatibility(
            self.compatibility, runtime_version=self.runtime_version, sandbox=self.sandbox
        )
        if report != self.report:
            raise Blocked("app-server-compatibility-incomplete", "report changed after selection")
        worker = Path(__file__).with_name("app_server_worker.py")
        # Recheck at launch: an adapter can outlive a source update on the host.
        worker_bytes = worker.read_bytes()
        _validate_worker(self.report, worker_bytes)
        (invocation.prompt_path.parent / worker.name).write_bytes(worker_bytes)
        request = {
            "readonly": readonly,
            "model": invocation.model,
            "effort": invocation.effort,
            "workdir": invocation.workdir,
            "prompt": str(invocation.prompt_path),
            "schema": str(invocation.schema_path),
            "output": str(invocation.output_path),
            "resume_session": invocation.resume_session,
            "usage_baseline": self.baselines.get(invocation.resume_session or ""),
            "usage_scope": self.report.get("usage_scope", "thread"),
            "vault": invocation.vault_directory,
            "context_semantics_verified": True,
        }
        invocation.prompt_path.with_suffix(".app-server.json").write_text(json.dumps(request))

    def wrapper_script(self, invocation: AgentInvocation) -> str:
        self.prepare(invocation)
        return super().wrapper_script(invocation)
