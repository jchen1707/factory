"""Failure episodes and concise handoffs. Product and review judgements stay human-owned."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factory import artifacts, authority, repo
from factory.intake.linear import open_blockers
from factory.machine import Blocked

if TYPE_CHECKING:
    from factory.steps import Context


def readiness(ctx: Context) -> bool:
    """Return whether a technical brief is needed. Missing product authority blocks."""
    if ctx.issue is None:
        raise Blocked("readiness-missing-ticket", ctx.run.linear_id)
    root = authority.current(ctx) or ctx.project.path
    contract_path = root / ".agents/vendor/harness/docs/agents/ticket-readiness.json"
    if not contract_path.exists():
        raise Blocked("workflow-contract-missing", str(contract_path))
    rules = json.loads(contract_path.read_text())
    facts = {
        "ticket": asdict(ctx.issue),
        "dependencies": open_blockers(ctx.issue),
        "tests": list(ctx.harness.tests) if ctx.harness else [],
    }
    brief = False
    for rule in rules["checks"]:
        value: Any = facts
        for part in rule["field"].split("."):
            value = value.get(part) if isinstance(value, dict) else None
        present = bool(value.strip()) if isinstance(value, str) else bool(value)
        if present == rule["present"]:
            continue
        if rule["action"] == "brief":
            brief = True
        else:
            raise Blocked(rule["reason"], rule["message"])
    return brief


def write(ctx: Context, target: Path) -> None:
    snapshot = ctx.store.runtime.policy(ctx.run.id)
    if ctx.project.requires_clone:

        def git(*argv: str) -> str:
            result = ctx.sandbox.exec_sync(
                ctx.project.build_sandbox, ["git", *argv], workdir=str(ctx.worktree), env=ctx.env
            )
            if result.returncode:
                raise Blocked("handoff-inventory-failed", result.stderr)
            return result.stdout.strip()
    else:

        def git(*argv: str) -> str:
            return repo._git(ctx.worktree, *argv)

    payload = {
        "contract_revision": snapshot["source_revision"] if snapshot else ctx.run.base_ref,
        "policy_revision": snapshot["revision"] if snapshot else None,
        "branch": ctx.branch,
        "commit": git("rev-parse", "HEAD"),
        "dirty_work": git("status", "--porcelain").splitlines(),
        "verified": [
            {"name": row["check_name"], "status": row["status"], "evidence": row["artifact"]}
            for row in ctx.store.checks(ctx.run.id)[-20:]
        ],
        "remaining_failure": ctx.run.blocked_reason,
        "attempted_fixes": [
            {"attempt": row["attempt"], "state": row["state"], "outcome": row["outcome"]}
            for row in ctx.store.runtime.db.execute(
                "SELECT * FROM attempts WHERE run_id=? ORDER BY attempt", (ctx.run.id,)
            ).fetchall()
        ],
        "evidence": str(ctx.factory_dir / "run"),
    }
    artifacts.write_json(target, payload)


def authorize_repair(ctx: Context, diagnosis: dict[str, Any], attempt: int) -> None:
    classification = diagnosis["classification"]
    if classification != "code" or diagnosis["status"] != "repair":
        next_action = {
            "environment": (
                "Inspect the retained verifier output and factory doctor results; correct the "
                "failing sandbox adapter, project setup, or declared installation environment "
                "before running verification again."
            ),
            "stale-authority": (
                "Operator must reconcile the approved contract and current authority, explicitly "
                "replace the paused run's policy if needed, then request a fresh handoff and "
                "renew verification and review."
            ),
            "requirements": (
                "Operator must resolve the missing product decision or scope change in the "
                "approved contract before refreshing execution authority and resuming."
            ),
            "review-dispute": (
                "Operator must decide the disputed review finding and record its disposition "
                "before resuming; diagnosis cannot dismiss the finding."
            ),
        }.get(
            classification, "Operator must inspect the diagnosis before authorizing further work."
        )
        evidence_dir = ctx.factory_dir if ctx.run.worktree else ctx.state_dir
        evidence_dir.mkdir(parents=True, exist_ok=True)
        target = evidence_dir / f"diagnosis-{attempt}.json"
        payload = {
            "diagnosis": diagnosis,
            "next_action": next_action,
            "handoff": str(evidence_dir / "handoff.json"),
            "failure_evidence": str(evidence_dir / "run"),
        }
        artifacts.write_json(target, payload)
        ctx.store.record_check(
            ctx.run.id,
            attempt,
            "diagnosis-routing",
            "fail",
            artifact=str(target),
            detail=json.dumps(payload),
        )
        raise Blocked(
            f"diagnosis-{classification}",
            f"{diagnosis['summary']} {next_action} Retained diagnosis: {target}",
        )
    # Only a host-recorded verifier artifact can authorize repair. Its digest binds
    # the file to the evidence observed before the diagnosing agent was launched.
    records = [
        row
        for row in ctx.store.checks(ctx.run.id)
        if row["check_name"] == "failure-reproduction" and row["status"] == "fail"
    ]
    if not records:
        raise Blocked("diagnosis-not-reproduced", "No host-recorded failure")
    recorded = records[-1]
    path = Path(recorded["artifact"])
    evidence_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    provenance = json.loads(recorded["detail"])
    if evidence_hash != provenance["sha256"]:
        raise Blocked("diagnosis-not-reproduced", "Verifier evidence changed after collection")
    relative = Path(diagnosis["reproduction_evidence"])
    if relative.is_absolute() or (ctx.factory_dir / relative).resolve() != path.resolve():
        raise Blocked(
            "diagnosis-not-reproduced", "Diagnosis must cite the recorded verifier artifact"
        )
    # The episode is allocated by the host on the first failure and lasts until a
    # passing verification closes it. Model wording cannot reset its repair budget.
    fingerprint = provenance["episode"]
    db = ctx.store.runtime.db
    with ctx.store.runtime.transaction():
        old = db.execute(
            "SELECT repairs,payload FROM failure_episodes WHERE run_id=? AND fingerprint=?",
            (ctx.run.id, fingerprint),
        ).fetchone()
        previous = json.loads(old["payload"]) if old else {}
        if previous.get("attempt") == attempt:
            return
        repairs = old["repairs"] if old else 0
        if repairs >= 2:
            raise Blocked(
                "repair-limit", "Two repairs have been attempted for this failure episode"
            )
        if previous.get("behavior_sha256") == provenance["behavior_sha256"]:
            raise Blocked(
                "failure-without-new-evidence", "The same acceptance failure remains unchanged"
            )
        if attempt > ctx.registry.defaults.max_total_attempts:
            raise Blocked("repair-lifetime-limit", "The lifetime attempt limit is exhausted")
        payload = diagnosis | {
            "attempt": attempt,
            "evidence_sha256": evidence_hash,
            "behavior_sha256": provenance["behavior_sha256"],
        }
        db.execute(
            "INSERT INTO failure_episodes VALUES (?,?,?,?) ON CONFLICT(run_id,fingerprint) "
            "DO UPDATE SET repairs=excluded.repairs,payload=excluded.payload",
            (ctx.run.id, fingerprint, repairs + 1, json.dumps(payload)),
        )


def prompt(ctx: Context, plans: Path) -> str:
    name = "diagnose-and-hand-off" if ctx.run.attempt else "ticket-readiness"
    return (
        authority.contract(ctx, name)
        + "\n"
        + authority.contract(ctx, "refresh-execution-authority")
        + f"\nTicket: {ctx.run.linear_id}\nContext: {ctx.factory_dir / 'context'}\n"
        + f"Handoff: {ctx.factory_dir / 'handoff.json'}\nOutput directory: {plans}\n"
        + f"Failure evidence: {ctx.factory_dir / 'run' / str(ctx.run.attempt)}\n"
    )


def record_failure(ctx: Context, path: Path, report: dict[str, Any]) -> None:
    """Retain verifier provenance on the host, outside the diagnosing agent's writes."""
    settings = ctx.store.runtime.settings("run", ctx.run.id)
    episode = settings.get("failure_episode") or f"{ctx.run.id}:{ctx.run.attempt}"
    ctx.store.runtime.configure("run", ctx.run.id, {"failure_episode": episode})
    # Ignore duration and attempt paths when comparing failure behavior. A rerun of
    # the same failed checks supplies no new acceptance evidence by itself.
    failing = [
        {
            "name": g["name"],
            "app": g.get("app"),
            "exit": g.get("exit"),
            "output": _failure_output(g.get("outputTail") or "", ctx.factory_dir),
        }
        for g in report["gates"]
        if g["status"] == "fail"
    ]
    failing.sort(key=lambda gate: json.dumps(gate, sort_keys=True))
    ctx.store.record_check(
        ctx.run.id,
        ctx.run.attempt,
        "failure-reproduction",
        "fail",
        artifact=str(path),
        detail=json.dumps(
            {
                "episode": episode,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "behavior_sha256": hashlib.sha256(
                    json.dumps(failing, sort_keys=True).encode()
                ).hexdigest(),
            }
        ),
    )


def _failure_output(output: str, factory_dir: Path) -> str:
    """Remove known runner noise without discarding assertions or failed test IDs.

    Keep raw output in the digest-bound artifact. This comparison deliberately
    avoids replacing arbitrary numbers, timestamps, paths, or durations: those can
    be the actual acceptance failure (including performance assertions).
    """
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    output = re.sub(
        re.escape(str(factory_dir / "run")) + r"/\d+(?=/)",
        str(factory_dir / "run") + "/<attempt>",
        output,
    )
    # Standard temporary roots retain their child names, including the failing
    # test's directory and the filename that caused the error.
    output = re.sub(
        r"/(?:private/)?tmp/pytest-of-[^/\s]+/pytest-\d+(?=/)",
        "/<test-temp>",
        output,
    )
    output = re.sub(r"/(?:private/)?tmp/tmp[a-z0-9_]{8}(?=/|['\"\s)])", "/<temp>", output)
    lines = []
    for line in output.splitlines():
        if re.fullmatch(
            r"[= ]*\d+ (?:failed|passed|error|errors|skipped|deselected|xfailed|xpassed)"
            r"(?:, \d+ [a-z]+)* in \d+(?:\.\d+)?s(?: \([\d:]+\))?[= ]*",
            line,
        ):
            line = re.sub(r" in \d+(?:\.\d+)?s(?: \([\d:]+\))?", "", line).strip("= ")
        lines.append(line.rstrip())
    return "\n".join(lines).strip()
