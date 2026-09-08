"""Concrete compatibility probes; policy text comes from the trusted layer-A source.

Protocol files live in an explicitly shared disposable directory. Attestations and
host observations live outside every candidate-writable mount. A model's success
claim cannot make a check pass: the evaluator requires runtime and host evidence.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from factory.agent.app_server import COMPATIBILITY_CHECKS, read_evidence
from factory.agent.certified_command import command as certified_command
from factory.agent_launches import AgentLaunches
from factory.certification import CertificationIdentity, Certifications
from factory.certification_runner import PreparedProbe
from factory.execution import ProjectQueued
from factory.machine import Blocked
from factory.sandbox.base import RunHandle, RunStatus, detached_shell_script
from factory.sandbox.sbx import SbxAdapter


def _json(path: Path) -> Any:
    return json.loads(read_evidence(path.parent, path.name))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, sort_keys=True, indent=2)
    if path.exists():
        if path.read_text() != data:
            raise Blocked("certification-preparation-changed", str(path))
    else:
        with path.open("x") as stream:
            stream.write(data)


def _events(path: Path) -> list[dict[str, Any]]:
    data = read_evidence(path.parent, path.name)
    lines = data.splitlines()
    events = []
    for number, line in enumerate(lines):
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            if number != len(lines) - 1 or data.endswith(b"\n"):
                raise
            # A concurrently writing host capture may end inside one notification.
            # Terminal evaluation still requires complete success notifications.
    return events


class CertificationSandboxExecution:
    """Capture the detached VM stream directly into a host-only evidence directory."""

    def __init__(self, sandbox: SbxAdapter, root: Path) -> None:
        self.sandbox, self.root = sandbox, root

    def exec_detached(self, handle: RunHandle, script: str, env: Mapping[str, str]) -> None:
        job_id, step = handle.attempt_dir.parent.name, handle.attempt_dir.name
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise ValueError("invalid certification job")
        directory = self.root / job_id
        directory.mkdir(parents=True, exist_ok=True)
        self.sandbox.exec_detached(
            handle, script, env, stdout_path=directory / (step + "-events.jsonl")
        )

    def poll(self, handle: RunHandle) -> RunStatus:
        return self.sandbox.poll(handle)


class CertificationProbeDriver:
    def __init__(
        self,
        certifications: Certifications,
        sandbox: SbxAdapter,
        *,
        protocol_root: Path,
        workdir: str,
        env: Mapping[str, str],
        source: Path,
        model: str,
        effort: str,
        readonly: bool,
        canary_path: str,
        readonly_root: str | None = None,
        alternate_model: str | None = None,
    ) -> None:
        self.certifications = certifications
        self.sandbox = sandbox
        self.protocol_root = protocol_root.resolve()
        self.workdir = workdir
        self.env = dict(env)
        self.source = source.resolve()
        self.model, self.effort = model, effort
        self.alternate_model = alternate_model
        self.readonly, self.readonly_root = readonly, readonly_root
        self.canary = Path(canary_path)
        if self.canary.is_absolute() or ".." in self.canary.parts:
            raise ValueError("canary must be an existing target-relative protected file")
        if self.protocol_root.is_relative_to(
            certifications.root
        ) or certifications.root.is_relative_to(self.protocol_root):
            raise ValueError("host certification evidence and VM protocol must be separate")
        if not Path(workdir).is_absolute():
            raise ValueError("workdir must be absolute")

    def _contract(self) -> dict[str, Any]:
        contract: dict[str, Any] = _json(self.source)
        if contract["version"] != 1 or not isinstance(contract["prompts"], dict):
            raise Blocked("certification-probes-invalid", str(self.source))
        return contract

    def preflight(self, identity: CertificationIdentity) -> None:
        self._contract()
        if not self.alternate_model or self.alternate_model == self.model:
            raise Blocked(
                "certification-model-change-required", "configure a second available model"
            )
        if self.readonly and not self.readonly_root:
            raise Blocked("certification-isolation-incomplete", "read-only root required")
        worker = Path(__file__).with_name("app_server_worker.py")
        if hashlib.sha256(worker.read_bytes()).hexdigest() != identity.worker_sha256:
            raise Blocked("certification-stale", "worker changed")
        # Source/native/mount freshness is independently rebuilt by the runner's
        # observation callback before every paid admission and publication.

    def steps(self, identity: CertificationIdentity) -> tuple[str, ...]:
        return tuple(self._contract()["steps"])

    def environment(self, job: dict[str, Any], step: str) -> Mapping[str, str]:
        return self.env

    def _host(self, job: dict[str, Any]) -> Path:
        return self.certifications.root / job["id"]

    def _events_path(self, job: dict[str, Any], step: str) -> Path:
        return self._host(job) / (step + "-events.jsonl")

    def _attempt(self, job: dict[str, Any], step: str) -> Path:
        if step not in self._contract()["steps"]:
            raise Blocked("certification-probes-invalid", step)
        return self.protocol_root / job["id"] / step

    def _git(self, sandbox: str) -> dict[str, str]:
        result = {}
        for name, argv in (
            ("head", ["git", "rev-parse", "HEAD"]),
            ("status", ["git", "status", "--porcelain=v1", "--untracked-files=all"]),
            ("diff", ["git", "diff", "HEAD", "--binary"]),
        ):
            command = self.sandbox.exec_sync(sandbox, argv, workdir=self.workdir, env=self.env)
            if not command.ok:
                raise Blocked("certification-isolation-incomplete", command.stderr)
            result[name] = command.stdout
        # Hash every existing untracked file as well as tracked modifications. Git
        # status alone would miss an edit to an already-dirty untracked file.
        script = (
            "import hashlib,json,pathlib,subprocess;"
            "names=subprocess.check_output(['git','ls-files','-z','--cached','--others',"
            "'--exclude-standard']).split(bytes([0]));"
            "print(json.dumps({n.decode():hashlib.sha256(pathlib.Path(n.decode()).read_bytes())."
            "hexdigest() for n in names if n and pathlib.Path(n.decode()).is_file()},sort_keys=True))"
        )
        command = self.sandbox.exec_sync(
            sandbox,
            ["/usr/bin/python3", "-I", "-S", "-c", script],
            workdir=self.workdir,
            env=self.env,
        )
        if not command.ok:
            raise Blocked("certification-isolation-incomplete", command.stderr)
        result["files"] = command.stdout
        return result

    def initialize(self, job: dict[str, Any]) -> None:
        """Deterministic host measurements, outside the paid preparation transaction."""
        launches = AgentLaunches(self.certifications.store, self.sandbox)
        for row in self.certifications.store.runtime.db.execute(
            "SELECT invocation_id FROM agent_leases WHERE status='active'"
        ):
            invocation = row["invocation_id"]
            if invocation.startswith("certification:" + job["id"] + ":"):
                continue
            try:
                existing = launches.handle(invocation)
            except ValueError as exc:
                raise ProjectQueued("unresolved agent reservation during certification") from exc
            if existing.sandbox == job["identity"]["sandbox"]:
                raise ProjectQueued("sandbox has another active agent")
        baseline = self._host(job) / "before.json"
        if not baseline.exists():
            _write(baseline, self._git(job["identity"]["sandbox"]))
        if self.readonly:
            evidence = self._host(job) / "readonly.json"
            if not evidence.exists():
                # Exclusive create cannot damage an existing file. The UUID target
                # is wholly owned by this disposable certification request.
                marker = str(Path(self.readonly_root or "") / (".certification-" + job["id"]))
                script = (
                    "import errno,json,os,sys;\n"
                    "try:\n f=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)\n"
                    "except OSError as e:\n print(json.dumps({'errno':e.errno}));"
                    "sys.exit(0 if e.errno==errno.EROFS else 2)\n"
                    "else:\n os.close(f);os.unlink(sys.argv[1]);sys.exit(3)\n"
                )
                result = self.sandbox.exec_sync(
                    job["identity"]["sandbox"],
                    ["/usr/bin/python3", "-I", "-S", "-c", script, marker],
                    env=self.env,
                )
                if not result.ok:
                    raise Blocked("certification-isolation-failed", result.stderr or result.stdout)
                _write(evidence, json.loads(result.stdout))

    def prepare(self, job: dict[str, Any], step: str, invocation_id: str) -> PreparedProbe:
        directory = self._attempt(job, step)
        directory.mkdir(parents=True, exist_ok=True)
        identity = job["identity"]
        contract = self._contract()
        request: dict[str, Any] = {
            "runtime_identity": {
                key: identity[key]
                for key in ("runtime_path", "runtime_sha256", "launcher_sha256", "code_host_sha256")
            },
            "model": self.model,
            "effort": self.effort,
            "workdir": self.workdir,
            "usage_scope": identity["usage_scope"],
        }
        worker_name = "app_server_worker.py"
        if step.startswith("usage-"):
            worker_name = "certification_usage_worker.py"
            stage = step.removeprefix("usage-")
            request |= {"stage": stage, "prompt": contract["prompts"]["usage"]}
            if stage != "initial":
                phases = [
                    e
                    for e in _events(self._events_path(job, "usage-initial"))
                    if e.get("type") == "factory.certification.phase"
                ]
                request["thread_id"] = phases[-1]["thread_id"]
                previous_step = contract["steps"][contract["steps"].index(step) - 1]
                previous = [
                    e
                    for e in _events(self._events_path(job, previous_step))
                    if e.get("type") == "factory.certification.phase"
                ][-1]
                request["usage_baseline"] = previous["total"]
            if stage in {"modelchange", "resume"}:
                request["model"] = self.alternate_model
        else:
            prompt = contract["prompts"][step].replace("{canary_path}", str(self.canary))
            prompt_path = directory / "prompt.txt"
            if prompt_path.exists() and prompt_path.read_text() != prompt:
                raise Blocked("certification-preparation-changed", str(prompt_path))
            prompt_path.write_text(prompt)
            _write(directory / "schema.json", contract["schema"])
            request |= {
                "prompt": str(prompt_path),
                "schema": str(directory / "schema.json"),
                "output": str(directory / "output.json"),
                "readonly": self.readonly,
                "vault": str(directory / "unused-vault"),
                "context_semantics_verified": False,
            }
            if step == "recovery":
                interrupted = _json(self._host(job) / "interruption.json")
                request |= {
                    "resume_session": interrupted["thread_id"],
                    "usage_baseline": interrupted["baseline"],
                }
        _write(directory / "request.json", request)
        for name in {worker_name, "app_server_worker.py"}:
            data = Path(__file__).with_name(name).read_bytes()
            target = directory / name
            if target.exists() and target.read_bytes() != data:
                raise Blocked("certification-preparation-changed", str(target))
            target.write_bytes(data)
        run = self.certifications.store.run_by_id(job["run_id"])
        if run is None:
            raise ValueError("certification run missing")
        handle = RunHandle(
            job["run_id"], max(run.attempt, 1), identity["sandbox"], self.workdir, directory
        )
        events = self._events_path(job, step)
        command = shlex.join(
            certified_command(
                request,
                prompt=None if step.startswith("usage-") else prompt_path.read_text(),
                schema=None if step.startswith("usage-") else json.dumps(contract["schema"]),
                usage=step.startswith("usage-"),
            )
        )
        body = command + " 2> " + shlex.quote(str(directory / "stderr"))
        script = detached_shell_script(
            heartbeat_path=directory / "heartbeat",
            exit_path=directory / "exit",
            pgid_path=directory / "pgid",
            body=body,
            immutable=True,
        )
        return PreparedProbe(
            handle,
            script,
            events,
            {
                "model": request["model"],
                "effort": self.effort,
                "preset": "certification",
                "adapter": "app-server",
                "service_tier": "standard",
            },
            inputs=tuple(
                directory / name for name in ("request.json", worker_name, "app_server_worker.py")
            )
            + (
                ()
                if step.startswith("usage-")
                else (directory / "prompt.txt", directory / "schema.json")
            ),
        )

    def _owned_group(self, handle: RunHandle) -> int:
        pgid = int((handle.attempt_dir / "pgid").read_text())
        result = self.sandbox.exec_sync(handle.sandbox, ["ps", "-eo", "pid,pgid,args"])
        expected = str(handle.attempt_dir / "pgid-body.sh")
        matches = [line.split(None, 2) for line in result.stdout.splitlines()[1:]]
        if (
            pgid <= 1
            or not result.ok
            or not any(
                len(row) == 3 and row[0] == row[1] == str(pgid) and expected in row[2]
                for row in matches
            )
        ):
            raise Blocked("certification-process-identity-changed", str(pgid))
        return pgid

    def advance(self, job: dict[str, Any], step: str, handle: RunHandle, status: RunStatus) -> None:
        if status != RunStatus.RUNNING:
            return
        directory = handle.attempt_dir
        if not (directory / "pgid").exists() or not self._events_path(job, step).exists():
            return
        elapsed = time.time() - (directory / "sbx-exec.pid").stat().st_mtime
        contract = self._contract()
        if elapsed > contract["maximum_phase_seconds"]:
            pgid = self._owned_group(handle)
            store = self.certifications.store
            invocation = "certification:" + job["id"] + ":" + step
            with store.runtime.transaction():
                from factory.certification_signals import probe_may_signal

                if not probe_may_signal(
                    store, job["id"], handle.run_id, handle.attempt, invocation
                ):
                    return
                store.intend_effect(
                    handle.run_id, handle.attempt, invocation, "certification", "timeout"
                )
            self.sandbox.kill_group(handle.sandbox, pgid)
            store.confirm_effect(
                handle.run_id, handle.attempt, invocation, "certification", "timeout", "terminated"
            )
            return
        if step != "interrupt":
            return
        if elapsed < self._contract()["minimum_detached_seconds"]:
            return
        events = _events(self._events_path(job, step))
        threads = [e["thread_id"] for e in events if e.get("type") == "thread.started"]
        usage = [e["thread_total"] for e in events if e.get("type") == "factory.usage"]
        if not threads or not usage:
            return
        pgid = self._owned_group(handle)
        # Observe the requested sleep in the exact process-group descendant tree.
        result = self.sandbox.exec_sync(handle.sandbox, ["ps", "-eo", "pid,ppid,pgid,comm"])
        if not result.ok:
            raise Blocked("certification-durability-incomplete", result.stderr)
        rows = [line.split() for line in result.stdout.splitlines()[1:] if len(line.split()) >= 4]
        descendants = {str(pgid)}
        for _ in rows:
            descendants.update(row[0] for row in rows if row[1] in descendants)
        if not any(row[0] in descendants and row[3] == "sleep" for row in rows):
            return
        store = self.certifications.store
        invocation = "certification:" + job["id"] + ":" + step
        with store.runtime.transaction():
            from factory.certification_signals import probe_may_signal

            if not probe_may_signal(store, job["id"], handle.run_id, handle.attempt, invocation):
                return
            _write(
                self._host(job) / "interruption.json",
                {
                    "thread_id": threads[-1],
                    "baseline": usage[-1],
                    "pgid": pgid,
                    "elapsed_seconds": elapsed,
                    "processes": rows,
                    "status": str(status),
                },
            )
            store.intend_effect(
                handle.run_id, handle.attempt, invocation, "certification", "interrupt"
            )
        # An ambiguous stop is never retried automatically, like an ambiguous paid
        # spawn. Recovery observes the existing exact process group and exit record.
        self.sandbox.kill_group(handle.sandbox, pgid)
        store.confirm_effect(
            handle.run_id, handle.attempt, invocation, "certification", "interrupt", str(pgid)
        )

    def accept_exit(self, job: dict[str, Any], step: str, code: int) -> bool:
        if step != "interrupt":
            return code == 0
        return code == 143 and (self._host(job) / "interruption.json").exists()

    def evaluate(self, job: dict[str, Any]) -> None:
        host = self._host(job)
        for step in self._contract()["steps"]:
            code = int(read_evidence(self._attempt(job, step), "exit"))
            if not self.accept_exit(job, step, code):
                raise Blocked("certification-probe-failed", step)
        if self.readonly and _json(host / "readonly.json").get("errno") != 30:
            raise Blocked("certification-isolation-failed", "read-only mount refusal missing")
        before = _json(host / "before.json")
        if self._git(job["identity"]["sandbox"]) != before:
            raise Blocked("certification-isolation-failed", "target contents changed")
        canary = _events(self._events_path(job, "canary"))
        if not any(
            e.get("type") == "factory.runtime"
            and e.get("event", {}).get("method") == "hook/completed"
            and e.get("event", {}).get("params", {}).get("run", {}).get("eventName") == "preToolUse"
            and e.get("event", {}).get("params", {}).get("run", {}).get("status") == "blocked"
            for e in canary
        ):
            raise Blocked("certification-hook-failed", "no observed hook refusal")
        for step in ("canary", "recovery"):
            captured_events = _events(self._events_path(job, step))
            answers = [
                e["item"]["text"]
                for e in captured_events
                if e.get("type") == "item.completed"
                and e.get("item", {}).get("type") == "agent_message"
            ]
            if (
                not answers
                or json.loads(answers[-1]) != {"ok": True}
                or not any(e.get("type") == "turn.completed" for e in captured_events)
            ):
                raise Blocked("certification-schema-failed", step)
        interrupted = _json(host / "interruption.json")
        recovery = _events(self._events_path(job, "recovery"))
        resumed = [e for e in recovery if e.get("type") == "factory.usage"]
        if not resumed or resumed[-1]["thread_id"] != interrupted["thread_id"]:
            raise Blocked("certification-recovery-failed", "thread changed")
        phases = {}
        raw: list[dict[str, Any]] = []
        for step in self._contract()["steps"]:
            if not step.startswith("usage-"):
                continue
            events = _events(self._events_path(job, step))
            phase = [e for e in events if e.get("type") == "factory.certification.phase"]
            if not phase or phase[-1].get("status") != "completed":
                raise Blocked("certification-usage-incomplete", step)
            phases[step] = phase[-1]
            raw.extend(
                e["event"]
                for e in events
                if e.get("type") == "factory.rpc" and e.get("direction") == "received"
            )
        if not phases["usage-compact"].get("compaction_observed"):
            raise Blocked("certification-usage-incomplete", "compaction not observed")
        if len({p["thread_id"] for p in phases.values()}) != 1:
            raise Blocked("certification-usage-incomplete", "usage phases changed thread")
        counters = [
            e["params"]["tokenUsage"] for e in raw if e.get("method") == "thread/tokenUsage/updated"
        ]
        if not any(
            c["last"]["inputTokens"] == 0 and c["total"]["inputTokens"] > 0 for c in counters
        ):
            raise Blocked("certification-usage-incomplete", "no compaction counter reset")
        # Require direct evidence of the declared counter scope on a fresh
        # connection; schema field presence is never semantic proof.
        first = phases["usage-initial"]
        second = phases["usage-second"]
        total, last = second.get("total", {}), second.get("last", {})
        if job["identity"]["usage_scope"] == "connection":
            scope_valid = total == last and bool(total.get("inputTokens"))
        else:
            scope_valid = (
                total.get("inputTokens", 0) > first.get("total", {}).get("inputTokens", 0)
                and total != last
            )
        if not scope_valid:
            raise Blocked("certification-usage-incomplete", "declared counter scope unproven")
        summary = {
            "identity": job["identity"],
            "interruption": interrupted,
            "unchanged_target": before,
            "usage_phases": phases,
        }
        _write(host / "summary.json", summary)
        # Retain the raw protocol, terminal records and staged source by content.
        # All report references stay beneath the host-owned certification directory.
        index = {}
        captured = {
            step: read_evidence(self._host(job), step + "-events.jsonl").decode()
            for step in self._contract()["steps"]
        }
        for step in self._contract()["steps"]:
            for path in self._attempt(job, step).iterdir():
                if path.is_file() and not path.is_symlink():
                    data = read_evidence(path.parent, path.name)
                    target = host / "raw" / step / path.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    index[str(target.relative_to(host))] = hashlib.sha256(data).hexdigest()
                    if path.name in {"events.jsonl", "output.json", "exit", "request.json"}:
                        captured[str(target.relative_to(host))] = data.decode()
        _write(host / "raw-index.json", index)
        _write(
            host / "summary-index.json", {"summary": summary, "raw": index, "captured": captured}
        )
        digest = hashlib.sha256((host / "summary-index.json").read_bytes()).hexdigest()
        report = {
            "runtime_version": job["identity"]["runtime_version"],
            "sandbox": job["identity"]["sandbox"],
            "worker_sha256": job["identity"]["worker_sha256"],
            "usage_scope": job["identity"]["usage_scope"],
            "checks": {
                name: {"status": "pass", "evidence": "summary-index.json", "sha256": digest}
                for name in COMPATIBILITY_CHECKS
            },
            "certification": {
                "job_id": job["id"],
                "fingerprint": job["fingerprint"],
                "identity": job["identity"],
            },
        }
        _write(host / "compatibility.json", report)
