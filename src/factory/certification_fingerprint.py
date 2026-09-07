"""Compose fresh runtime observations with controller-owned certification inputs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from factory.agent import app_server_worker
from factory.agent.app_server import read_evidence
from factory.certification import CertificationIdentity
from factory.machine import Blocked
from factory.sandbox.base import SandboxSpec


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class FingerprintInputs:
    spec: SandboxSpec
    binary: str
    workdir: str
    env: Mapping[str, str]
    authority_root: Path
    probe_root: Path
    hook_files: Mapping[str, str]
    usage_scope: str
    probe_parameters: Mapping[str, Any] = field(default_factory=dict)
    probe_implementation: tuple[Path, ...] = ()
    credential_names: tuple[str, ...] = ()
    acknowledged_credentials: tuple[str, ...] = ()


class CertificationObserver(Protocol):
    def observe_certification(
        self,
        spec: SandboxSpec,
        *,
        binary: str,
        workdir: str,
        env: Mapping[str, str],
        hook_files: Mapping[str, str],
        credential_names: tuple[str, ...] = (),
    ) -> dict[str, Any]: ...


def inventory(root: Path) -> dict[str, str]:
    """Hash the complete trusted tree, refusing links and special files."""
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("trusted root must be an absolute regular directory")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("trusted inputs cannot contain symlinks")
        if not path.is_dir():
            relative = str(path.relative_to(root))
            result[relative] = hashlib.sha256(read_evidence(root, relative)).hexdigest()
    if not result:
        raise ValueError("trusted input tree is empty")
    return result


def observe(adapter: CertificationObserver, inputs: FingerprintInputs) -> CertificationIdentity:
    """Never use a saved job identity as an observation. No model or mutation occurs."""
    try:
        writable = [w.path.resolve() for w in inputs.spec.workspaces if not w.readonly]
        for root in (inputs.authority_root, inputs.probe_root):
            if any(root.resolve().is_relative_to(path) for path in writable):
                raise ValueError("authority and probe source must be outside writable mounts")
        if not inputs.hook_files:
            raise ValueError("trusted hook input inventory is required")
        authority = inventory(inputs.authority_root)
        probes = inventory(inputs.probe_root)
        implementation = {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in inputs.probe_implementation
        }
        worker = Path(app_server_worker.__file__).read_bytes()
        actual = adapter.observe_certification(
            inputs.spec,
            binary=inputs.binary,
            workdir=inputs.workdir,
            env=inputs.env,
            hook_files=inputs.hook_files,
            credential_names=inputs.credential_names,
        )
        from factory.policy import capability_env_names

        present = actual["actual"].get("credential_names", [])
        if not isinstance(present, list) or any(not isinstance(name, str) for name in present):
            raise ValueError("invalid credential observation")
        blocking, _ = capability_env_names(present, inputs.acknowledged_credentials)
        if blocking:
            raise ValueError(
                "sandbox environment grants an undeclared capability: " + ", ".join(blocking)
            )
        if implementation != {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in inputs.probe_implementation
        }:
            raise ValueError("probe implementation changed during observation")
        if authority != inventory(inputs.authority_root) or probes != inventory(inputs.probe_root):
            raise ValueError("trusted inputs changed during observation")
        if worker != Path(app_server_worker.__file__).read_bytes():
            raise ValueError("worker changed during observation")
        requested = asdict(inputs.spec)
        requested["workspaces"] = [
            {"path": str(w.path.resolve()), "readonly": w.readonly} for w in inputs.spec.workspaces
        ]
        # Environment values never enter the attestation or its database identity.
        requested["env"] = digest(dict(inputs.spec.env))
        return CertificationIdentity(
            sandbox=inputs.spec.name,
            generation=actual["generation"],
            image_digest=actual["image_digest"],
            runtime_path=actual["runtime_path"],
            runtime_version=actual["runtime_version"],
            runtime_sha256=actual["runtime_sha256"],
            spec_sha256=digest(
                {
                    "requested": requested,
                    "actual": actual["actual"],
                    "workdir": inputs.workdir,
                    "environment": digest(dict(inputs.env)),
                    "credential_names": inputs.credential_names,
                    "acknowledged_credentials": inputs.acknowledged_credentials,
                }
            ),
            worker_sha256=hashlib.sha256(worker).hexdigest(),
            authority_sha256=digest({"files": authority, "hooks": dict(inputs.hook_files)}),
            probe_sha256=digest(
                {
                    "source": probes,
                    "parameters": dict(inputs.probe_parameters),
                    "implementation": implementation,
                }
            ),
            usage_scope=inputs.usage_scope,
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise Blocked("certification-inputs-untrusted", str(exc)) from exc
