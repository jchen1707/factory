"""Read executing-runtime model metadata without changing the invocation adapter."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from factory import artifacts
from factory.machine import Blocked
from factory.routing import Role
from factory.sandbox.base import SandboxAdapter
from factory.sandbox.sbx import SbxError


def validate(
    sandbox: SandboxAdapter,
    name: str,
    directory: Path,
    role: Role,
    *,
    env: Mapping[str, str],
) -> Path:
    """Retain one metadata observation in a mounted path and validate the exact role."""
    directory.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="runtime-model-", dir=directory))
    worker = evidence / "app_server_worker.py"
    shutil.copyfile(Path(__file__).with_name(worker.name), worker)
    request = evidence / "request.json"
    artifacts.write_json(request, {"probe_models": True})
    argv = ["python3", str(worker), str(request)]
    try:
        result = sandbox.exec_sync(name, argv, workdir=str(evidence), env=env, timeout=60)
    except (OSError, SbxError) as exc:
        raise Blocked(
            "runtime-model-probe-failed", f"Cannot read model metadata in {name}: {exc}"
        ) from exc
    (evidence / "events.jsonl").write_text(result.stdout)
    (evidence / "stderr.log").write_text(result.stderr)
    try:
        if not result.ok:
            raise ValueError(f"probe exited {result.returncode}")
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        catalogues = [event["models"] for event in events if event.get("type") == "factory.models"]
        if len(catalogues) != 1 or not isinstance(catalogues[0], list):
            raise ValueError("probe did not return one model catalogue")
        matches = [model for model in catalogues[0] if model["model"] == role.model]
        supported = len(matches) == 1 and role.effort in [
            entry["reasoningEffort"] for entry in matches[0]["supportedReasoningEfforts"]
        ]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise Blocked("runtime-model-probe-failed", f"{name}: {exc}; evidence: {evidence}") from exc
    if not supported:
        raise Blocked(
            "runtime-model-unavailable",
            f"{role.model} / {role.effort} is not supported in {name}; evidence: {evidence}",
        )
    path = evidence / "validated.json"
    artifacts.write_json(
        path,
        {"sandbox": name, "model": role.model, "effort": role.effort, "catalogue": catalogues[0]},
    )
    return path
