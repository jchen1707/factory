"""Durable VM-only zero-model preparation before the first paid certification."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from factory.agent import app_server_worker, runtime_preparation_worker
from factory.certification import CertificationIdentity
from factory.certification_fingerprint import digest
from factory.machine import Blocked
from factory.policy import assert_factory_sandbox
from factory.sandbox.base import Completed, SandboxSpec
from factory.store import Store


class PreparationSandbox(Protocol):
    def exec_sync(
        self,
        name: str,
        argv: Sequence[str],
        *,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int | None = None,
        stdin: str | None = None,
    ) -> Completed: ...


class RuntimePreparation:
    def __init__(self, store: Store, sandbox: PreparationSandbox, home: Path) -> None:
        self.store, self.sandbox, self.home = store, sandbox, home

    def authorize_replacement(
        self,
        run_id: str,
        key: str,
        *,
        receipt_sha256: str,
        evidence_sha256: str,
        observe: Callable[[], CertificationIdentity],
    ) -> None:
        """Explicit host operator action, never called by automatic recovery.

        Preserve the unknown receipt; authorize a new preparation after diagnosing
        it after a preparation-source fix. Unchanged-source retries are refused before
        authorization. This is not confirmation that the old thread succeeded.
        """
        for value in (receipt_sha256, evidence_sha256):
            if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise Blocked("runtime-preparation-resolution-invalid", "Invalid evidence digest")
        current = asdict(observe())
        with self.store.runtime.transaction():
            effect = self.store.find_effect(run_id, 0, key, "runtime-preparation", "thread-start")
            if effect is None or effect.status != "intended" or effect.external_id is None:
                raise Blocked("runtime-preparation-resolution-invalid", "No uncertain preparation")
            if hashlib.sha256(effect.external_id.encode()).hexdigest() != receipt_sha256:
                raise Blocked("runtime-preparation-resolution-invalid", "Receipt changed")
            receipt = json.loads(effect.external_id)
            previous_source = receipt.get("owner", {}).get("preparation")
            corrected_source = hashlib.sha256(
                Path(runtime_preparation_worker.__file__).read_bytes()
            ).hexdigest()
            if previous_source == corrected_source:
                raise Blocked(
                    "runtime-preparation-resolution-invalid",
                    "Replacement requires corrected preparation source",
                )
            before = receipt.get("before", {})
            # Configuration may have changed during thread start; probe identity includes
            # the corrected preparation source. Neither authorizes reuse of a paid
            # certificate. VM, native binaries and authority must still match.
            if {k: v for k, v in before.items() if k not in {"spec_sha256", "probe_sha256"}} != {
                k: v for k, v in current.items() if k not in {"spec_sha256", "probe_sha256"}
            }:
                raise Blocked("runtime-preparation-resolution-invalid", "Identity changed")
            resolution = json.dumps(
                {
                    "actor": "operator",
                    "disposition": "replace-unacknowledged",
                    "receipt_sha256": receipt_sha256,
                    "evidence_sha256": evidence_sha256,
                    "observed": current,
                },
                sort_keys=True,
            )
            old = self.store.find_effect(
                run_id, 0, key, "runtime-preparation-resolution", "replace"
            )
            if old is not None:
                if old.status != "confirmed" or old.external_id != resolution:
                    raise Blocked("runtime-preparation-resolution-invalid", "Resolution changed")
                return
            self.store.intend_effect(run_id, 0, key, "runtime-preparation-resolution", "replace")
            self.store.confirm_effect(
                run_id, 0, key, "runtime-preparation-resolution", "replace", resolution
            )

    def ensure(
        self,
        run_id: str,
        *,
        spec: SandboxSpec,
        workdir: str,
        env: Mapping[str, str],
        model: str,
        observe: Callable[[], CertificationIdentity],
        trust_root: str | None = None,
    ) -> None:
        assert_factory_sandbox(spec.name)
        current = observe()
        if current.sandbox != spec.name:
            raise Blocked("runtime-preparation-identity", "Observed another sandbox")
        worker = Path(app_server_worker.__file__).read_bytes().decode()
        if hashlib.sha256(worker.encode()).hexdigest() != current.worker_sha256:
            raise Blocked("runtime-preparation-worker-changed", "Worker changed after observation")
        preparation = Path(runtime_preparation_worker.__file__).read_text()
        owner = {
            "run_id": run_id,
            "sandbox": spec.name,
            "generation": current.generation,
            "workdir": workdir,
            "trust_root": trust_root or workdir,
            "environment": digest(dict(env)),
            "runtime": current.runtime_sha256,
            "worker": current.worker_sha256,
            "preparation": hashlib.sha256(preparation.encode()).hexdigest(),
        }
        key = digest({"owner": owner, "identity": asdict(current)})
        directory = self.home / "state/runtime-preparation" / key
        if any(
            directory.resolve().is_relative_to(w.path.resolve())
            for w in spec.workspaces
            if not w.readonly
        ):
            raise Blocked(
                "runtime-preparation-evidence", "Evidence overlaps candidate writable mounts"
            )
        with self.store.runtime.transaction():
            matched = False
            for completed in self.store.effects(run_id):
                if completed.system != "runtime-preparation":
                    continue
                try:
                    receipt = json.loads(completed.external_id or "{}")
                except ValueError:
                    if completed.status != "confirmed":
                        raise Blocked(
                            "runtime-preparation-uncertain", "Preparation receipt unreadable"
                        ) from None
                    continue
                previous_owner = receipt.get("owner", {}) if isinstance(receipt, dict) else {}
                if completed.status != "confirmed" and (
                    not isinstance(previous_owner, dict)
                    or not all(previous_owner.get(k) for k in ("sandbox", "generation"))
                ):
                    raise Blocked("runtime-preparation-uncertain", "Preparation owner unavailable")
                same_vm = all(previous_owner.get(k) == owner[k] for k in ("sandbox", "generation"))
                if completed.status != "confirmed" and same_vm:
                    resolution = self.store.find_effect(
                        run_id, 0, completed.step, "runtime-preparation-resolution", "replace"
                    )
                    replacement = json.loads(resolution.external_id or "{}") if resolution else {}
                    resolved = (
                        resolution is not None
                        and resolution.status == "confirmed"
                        and replacement.get("disposition") == "replace-unacknowledged"
                        and replacement.get("receipt_sha256")
                        == hashlib.sha256((completed.external_id or "").encode()).hexdigest()
                    )
                    if not resolved:
                        raise Blocked(
                            "runtime-preparation-uncertain",
                            "Reconcile the retained zero-model preparation before retrying",
                        )
                if (
                    previous_owner == owner
                    and completed.status == "confirmed"
                    and receipt.get("after") == asdict(current)
                ):
                    matched = True
            if matched:
                return
            previous = self.store.find_effect(run_id, 0, key, "runtime-preparation", "thread-start")
            if previous is not None:
                if previous.status == "confirmed":
                    raise Blocked(
                        "runtime-preparation-context-changed",
                        "Restore or explicitly replace the retained runtime preparation",
                    )
                raise Blocked(
                    "runtime-preparation-uncertain",
                    "Reconcile the retained zero-model preparation before retrying",
                )
            self.store.intend_effect(run_id, 0, key, "runtime-preparation", "thread-start")
            self.store.runtime.db.execute(
                "UPDATE effects SET external_id=? WHERE run_id=? AND attempt=0 AND step=? AND system='runtime-preparation' AND key='thread-start'",
                (
                    json.dumps(
                        {"owner": owner, "before": asdict(current), "directory": str(directory)},
                        sort_keys=True,
                    ),
                    run_id,
                    key,
                ),
            )
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "before.json").write_text(json.dumps(asdict(current), sort_keys=True))
        request = {
            "workdir": workdir,
            "trust_root": trust_root or workdir,
            "model": model,
            "runtime_identity": {
                field: getattr(current, field)
                for field in (
                    "runtime_path",
                    "runtime_sha256",
                    "launcher_sha256",
                    "code_host_sha256",
                )
            },
        }
        # Compose trusted source in memory; no import or helper comes from the candidate.
        code = worker.rsplit('if __name__ == "__main__":', 1)[0]
        code += "\n" + preparation.replace("from __future__ import annotations\n", "")
        code += "\ntry:\n print(json.dumps(prepare_runtime(json.loads(sys.argv[1]), start_server)))\nexcept Exception as exc:\n print(json.dumps({'failure': exc.code if isinstance(exc, PreparationFailure) else 'worker-failed'}))\n raise SystemExit(1)\n"
        failure_code = "transport-failed"
        try:
            result = self.sandbox.exec_sync(
                spec.name,
                ["/usr/bin/python3", "-I", "-S", "-c", code, json.dumps(request)],
                workdir=workdir,
                env=env,
                timeout=70,
            )
            failure_code = "worker-failed"
            if not result.ok or len(result.stdout) > 16384:
                if len(result.stdout) <= 16384:
                    try:
                        refusal = json.loads(result.stdout)
                        category = refusal.get("failure") if isinstance(refusal, dict) else None
                        if (
                            isinstance(category, str)
                            and category in runtime_preparation_worker.PREPARATION_FAILURE_CODES
                        ):
                            failure_code = category
                    except ValueError:
                        pass
                raise ValueError("runtime preparation did not complete")
            failure_code = "evidence-invalid"
            evidence = json.loads(result.stdout)
            if not isinstance(evidence, dict) or set(evidence) != {
                "thread_id",
                "configuration_before",
                "configuration_after",
                "model_turns",
            }:
                raise ValueError("runtime preparation evidence unavailable")
            if (
                type(evidence["model_turns"]) is not int
                or evidence["model_turns"] != 0
                or not isinstance(evidence["thread_id"], str)
                or not evidence["thread_id"]
                or len(evidence["thread_id"]) > 128
            ):
                raise ValueError("runtime preparation evidence invalid")
            for field in ("configuration_before", "configuration_after"):
                value = evidence[field]
                if (
                    not isinstance(value, str)
                    or len(value) != 64
                    or any(c not in "0123456789abcdef" for c in value)
                ):
                    raise ValueError("runtime configuration digest unavailable")
            failure_code = "identity-changed"
            after = observe()
            before_identity, after_identity = asdict(current), asdict(after)
            before_identity.pop("spec_sha256")
            after_identity.pop("spec_sha256")
            if before_identity != after_identity:
                raise ValueError("runtime identity changed during preparation")
            (directory / "after.json").write_text(json.dumps(asdict(after), sort_keys=True))
            (directory / "result.json").write_text(json.dumps(evidence, sort_keys=True))
            with self.store.runtime.transaction():
                self.store.confirm_effect(
                    run_id,
                    0,
                    key,
                    "runtime-preparation",
                    "thread-start",
                    json.dumps(
                        {
                            "owner": owner,
                            "after": asdict(after),
                            "directory": str(directory),
                            "result_sha256": digest(evidence),
                        },
                        sort_keys=True,
                    ),
                )
        except (OSError, ValueError, RuntimeError) as exc:
            (directory / "failure.json").write_text(
                json.dumps(
                    {
                        "reason": type(exc).__name__,
                        "failure_code": failure_code,
                        "model_turns_admitted": 0,
                    }
                )
            )
            raise Blocked(
                "runtime-preparation-failed",
                failure_code + "; retained evidence: " + str(directory),
            ) from exc
