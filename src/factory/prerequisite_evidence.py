"""Host-verified prerequisite resolutions carried into a resumed run.

The ticket and its frozen specification remain the statement of requested work.  This
module records a later, narrowly-scoped fact about the host that the ticket could not
have known: a named prerequisite was rechecked and now passes.  The copied document and
its digest make that handoff durable; the run-scoped operator event makes it auditable.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from factory import artifacts
from factory.machine import Blocked

MAX_BYTES = 64 * 1024
MAX_AGE_SECONDS = 24 * 60 * 60
_ACTION = "prerequisite-resolution-recorded"


def record(ctx: Any, source: Path, *, now: float | None = None) -> dict[str, Any]:
    """Validate, copy and audit one fresh resolution document for this exact run."""
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise Blocked("prerequisite-evidence-unavailable", str(exc)) from exc
    if len(raw) > MAX_BYTES:
        raise Blocked("prerequisite-evidence-invalid", "Evidence exceeds 64 KiB")
    try:
        evidence = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Blocked("prerequisite-evidence-invalid", "Evidence must be UTF-8 JSON") from exc
    _validate(evidence, ctx.run.linear_id, time.time() if now is None else now)

    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    destination = ctx.state_dir / "handoffs" / f"prerequisite-{digest}.json"
    if destination.exists():
        try:
            existing = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise Blocked(
                "prerequisite-evidence-conflict", "Retained evidence is unreadable"
            ) from exc
        if existing != evidence:
            raise Blocked("prerequisite-evidence-conflict", "Digest collision in retained evidence")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        artifacts.write_json(destination, evidence)
    with ctx.store.runtime.transaction():
        prior = [
            event
            for event in ctx.store.runtime.events(ctx.run.id)
            if event["action"] == _ACTION and json.loads(event["payload"]).get("sha256") == digest
        ]
        if not prior:
            ctx.store.runtime.audit(
                "run",
                ctx.run.id,
                _ACTION,
                {
                    "ticket": ctx.run.linear_id,
                    "prerequisite": evidence["prerequisite"],
                    "verified_at": evidence["verified_at"],
                    "sha256": digest,
                    "artifact": str(destination.relative_to(ctx.state_dir)),
                },
            )
    return evidence


def retained(ctx: Any) -> list[dict[str, Any]]:
    """Read only digest-matching evidence that was audited for this run."""
    result: list[dict[str, Any]] = []
    for event in ctx.store.runtime.events(ctx.run.id):
        if event["action"] != _ACTION:
            continue
        payload = json.loads(event["payload"])
        relative = Path(str(payload.get("artifact", "")))
        path = (ctx.state_dir / relative).resolve()
        if relative.is_absolute() or ctx.state_dir.resolve() not in path.parents:
            continue
        try:
            evidence = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(canonical).hexdigest() != payload.get("sha256"):
            continue
        if isinstance(evidence, dict) and evidence.get("ticket") == ctx.run.linear_id:
            result.append(evidence)
    return result


def _validate(evidence: Any, ticket: str, now: float) -> None:
    if not isinstance(evidence, dict) or set(evidence) != {
        "schema_version",
        "ticket",
        "prerequisite",
        "status",
        "verified_at",
        "verification",
    }:
        raise Blocked("prerequisite-evidence-invalid", "Unexpected evidence shape")
    if type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1:
        raise Blocked("prerequisite-evidence-invalid", "schema_version must be 1")
    if not isinstance(evidence["ticket"], str) or evidence["ticket"].upper() != ticket:
        raise Blocked("prerequisite-evidence-wrong-ticket", f"Evidence must name {ticket}")
    if evidence["status"] != "resolved":
        raise Blocked("prerequisite-evidence-not-resolved", "Prerequisite status must be resolved")
    if not isinstance(evidence["prerequisite"], str) or not evidence["prerequisite"].strip():
        raise Blocked("prerequisite-evidence-invalid", "Prerequisite must be named")
    try:
        observed = datetime.fromisoformat(str(evidence["verified_at"]).replace("Z", "+00:00"))
        timestamp = observed.timestamp() if observed.tzinfo is not None else float("nan")
    except (ValueError, OverflowError):
        timestamp = float("nan")
    if not (now - MAX_AGE_SECONDS <= timestamp <= now + 300):
        raise Blocked(
            "prerequisite-evidence-stale", "Verification must be UTC and under 24 hours old"
        )
    verification = evidence["verification"]
    if not isinstance(verification, dict) or set(verification) != {
        "command",
        "exit_code",
        "summary",
    }:
        raise Blocked("prerequisite-evidence-invalid", "Unexpected verification shape")
    command = verification["command"]
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part for part in command)
        or type(verification["exit_code"]) is not int
        or verification["exit_code"] != 0
        or not isinstance(verification["summary"], str)
        or not verification["summary"].strip()
    ):
        raise Blocked(
            "prerequisite-evidence-unverified", "Verification must be a passing argv command"
        )
