"""Opt-in app-server adapter. Compatibility evidence is required before execution."""

from __future__ import annotations

import hashlib
import json
import shutil
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
        report = json.loads(path.read_text())
        if report["runtime_version"] != runtime_version or report["sandbox"] != sandbox:
            raise ValueError("runtime or sandbox changed")
        if set(report["checks"]) != COMPATIBILITY_CHECKS:
            raise ValueError("compatibility checks incomplete")
        for name, check in report["checks"].items():
            evidence = path.parent / check["evidence"]
            if (
                check["status"] != "pass"
                or hashlib.sha256(evidence.read_bytes()).hexdigest() != check["sha256"]
            ):
                raise ValueError(f"unverified evidence for {name}")
        return report
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Blocked("app-server-compatibility-incomplete", str(exc)) from exc


class AppServerAdapter(CodexAdapter):
    def __init__(
        self,
        compatibility: Path,
        *,
        runtime_version: str,
        sandbox: str,
        baselines: dict[str, dict[str, int]] | None = None,
    ) -> None:
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
        worker = Path(__file__).with_name("app_server_worker.py")
        shutil.copyfile(worker, invocation.prompt_path.parent / worker.name)
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
            "vault": invocation.vault_directory,
            "context_semantics_verified": True,
        }
        invocation.prompt_path.with_suffix(".app-server.json").write_text(json.dumps(request))

    def wrapper_script(self, invocation: AgentInvocation) -> str:
        self.prepare(invocation)
        return super().wrapper_script(invocation)
