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

    def ensure(
        self,
        run_id: str,
        *,
        spec: SandboxSpec,
        workdir: str,
        env: Mapping[str, str],
        model: str,
        observe: Callable[[], CertificationIdentity],
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
                    continue
                if isinstance(receipt, dict) and receipt.get("owner") == owner:
                    if completed.status != "confirmed":
                        raise Blocked(
                            "runtime-preparation-uncertain",
                            "Reconcile the retained zero-model preparation before retrying",
                        )
                    if receipt.get("after") == asdict(current):
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
        code += "\ntry:\n print(json.dumps(prepare_runtime(json.loads(sys.argv[1]), start_server)))\nexcept Exception:\n print('runtime preparation refused', file=sys.stderr)\n raise SystemExit(1)\n"
        try:
            result = self.sandbox.exec_sync(
                spec.name,
                ["/usr/bin/python3", "-I", "-S", "-c", code, json.dumps(request)],
                workdir=workdir,
                env=env,
                timeout=70,
            )
            if not result.ok or len(result.stdout) > 16384:
                raise ValueError("runtime preparation did not complete")
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
                json.dumps({"reason": type(exc).__name__, "model_turns_admitted": 0})
            )
            raise Blocked(
                "runtime-preparation-failed", "Retained evidence: " + str(directory)
            ) from exc
