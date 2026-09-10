"""Audited human dispositions for one run's canonical review findings.

Review findings are evidence, while deciding whether each finding is repaired, invalid, or
deferred belongs to James. This module retains that decision as ticket- and run-bound
evidence and only returns it while it still matches the current canonical review summary.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from factory import artifacts
from factory.machine import Blocked

MAX_BYTES = 64 * 1024
_ACTION = "review-disposition-recorded"
_DISPOSITIONS = {"must-fix", "invalid", "deferred"}


def check(ctx: Any, source: Path, *, now: float | None = None) -> dict[str, Any]:
    """Validate a disposition against current evidence without retaining or auditing it."""
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise Blocked("review-disposition-unavailable", str(exc)) from exc
    if len(raw) > MAX_BYTES:
        raise Blocked("review-disposition-invalid", "Disposition exceeds 64 KiB")
    try:
        evidence = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Blocked("review-disposition-invalid", "Disposition must be UTF-8 JSON") from exc

    review_summary = _latest_review_summary(ctx)
    _validate(evidence, ctx, review_summary, time.time() if now is None else now)
    return evidence


def record(ctx: Any, source: Path, *, now: float | None = None) -> dict[str, Any]:
    """Validate, retain, and audit James's decision for the current review summary."""
    evidence = check(ctx, source, now=now)
    review_summary = _latest_review_summary(ctx)
    canonical = _canonical(evidence)
    digest = hashlib.sha256(canonical).hexdigest()
    source_digest = _review_digest(review_summary)

    prior_for_review: list[dict[str, Any]] = []
    for event in ctx.store.runtime.events(ctx.run.id):
        if event["action"] != _ACTION:
            continue
        try:
            payload = json.loads(event["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        if payload.get("source_review_sha256") == source_digest:
            prior_for_review.append(payload)
    if any(payload.get("sha256") != digest for payload in prior_for_review):
        raise Blocked(
            "review-disposition-conflict",
            "This review already has a different audited disposition",
        )

    destination = ctx.state_dir / "handoffs" / f"review-disposition-{digest}.json"
    if destination.exists():
        try:
            existing = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise Blocked(
                "review-disposition-conflict", "Retained disposition is unreadable"
            ) from exc
        if existing != evidence:
            raise Blocked("review-disposition-conflict", "Digest collision in retained disposition")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        artifacts.write_json(destination, evidence)

    if not prior_for_review:
        with ctx.store.runtime.transaction():
            ctx.store.runtime.audit(
                "run",
                ctx.run.id,
                _ACTION,
                {
                    "ticket": ctx.run.linear_id,
                    "run_id": ctx.run.id,
                    "decided_by": evidence["decided_by"],
                    "decided_at": evidence["decided_at"],
                    "source_review_sha256": source_digest,
                    "finding_count": len(evidence["findings"]),
                    "sha256": digest,
                    "artifact": str(destination.relative_to(ctx.state_dir)),
                },
            )
    return evidence


def retained(ctx: Any) -> list[dict[str, Any]]:
    """Return only audited, intact dispositions for the current review summary."""
    try:
        review_summary = _latest_review_summary(ctx)
    except Blocked:
        return []
    source_digest = _review_digest(review_summary)
    result: list[dict[str, Any]] = []
    for event in ctx.store.runtime.events(ctx.run.id):
        if event["action"] != _ACTION:
            continue
        try:
            payload = json.loads(event["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        if payload.get("source_review_sha256") != source_digest:
            continue
        relative = Path(str(payload.get("artifact", "")))
        path = (ctx.state_dir / relative).resolve()
        if relative.is_absolute() or ctx.state_dir.resolve() not in path.parents:
            continue
        try:
            evidence = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if hashlib.sha256(_canonical(evidence)).hexdigest() != payload.get("sha256"):
            continue
        try:
            _validate(evidence, ctx, review_summary, time.time(), allow_historical=True)
        except Blocked:
            continue
        result.append(evidence)
    return result


def source_review(ctx: Any) -> tuple[str, tuple[str, ...]]:
    """Expose the exact current review identity for constructing a bounded handoff."""
    summary = _latest_review_summary(ctx)
    return _review_digest(summary), tuple(_finding_lines(summary))


def template(ctx: Any, *, decided_at: str) -> dict[str, Any]:
    """Build a non-valid draft containing the exact run/review identity and findings."""
    source_digest, findings = source_review(ctx)
    return {
        "schema_version": 1,
        "ticket": ctx.run.linear_id,
        "run_id": ctx.run.id,
        "source_review_sha256": source_digest,
        "decided_by": "James",
        "decided_at": decided_at,
        "findings": [
            {
                "finding": finding,
                "disposition": "REPLACE",
                "direction": "",
                "acceptance": [],
            }
            for finding in findings
        ],
    }


def _latest_review_summary(ctx: Any) -> dict[str, Any]:
    path = ctx.state_dir / "review" / "review-summary.json"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Blocked("review-disposition-unavailable", "Review summary is unavailable") from exc
    if len(raw) > MAX_BYTES:
        raise Blocked("review-disposition-unavailable", "Review summary exceeds 64 KiB")
    try:
        summary = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Blocked("review-disposition-unavailable", "Review summary is not UTF-8 JSON") from exc

    from factory.steps import review as review_step

    if not isinstance(summary, dict) or not review_step.has_blocking_findings(summary):
        raise Blocked(
            "review-disposition-unavailable", "Review summary has no canonical blocking finding"
        )
    return summary


def _finding_lines(summary: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    for finding in summary["findings"]:
        location = str(finding["file"])
        if finding["line"] is not None:
            location += f":{finding['line']}"
        findings.append(f"- [{finding['severity']}] {location} {finding['summary']}")
    return findings


def _review_digest(summary: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(summary)).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _validate(
    evidence: Any,
    ctx: Any,
    review_summary: dict[str, Any],
    now: float,
    *,
    allow_historical: bool = False,
) -> None:
    expected_top = {
        "schema_version",
        "ticket",
        "run_id",
        "source_review_sha256",
        "decided_by",
        "decided_at",
        "findings",
    }
    if not isinstance(evidence, dict) or set(evidence) != expected_top:
        raise Blocked("review-disposition-invalid", "Unexpected disposition shape")
    if type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1:
        raise Blocked("review-disposition-invalid", "schema_version must be 1")
    if not isinstance(evidence["ticket"], str) or evidence["ticket"].upper() != ctx.run.linear_id:
        raise Blocked(
            "review-disposition-wrong-ticket", f"Disposition must name {ctx.run.linear_id}"
        )
    if evidence["run_id"] != ctx.run.id:
        raise Blocked("review-disposition-wrong-run", f"Disposition must name run {ctx.run.id}")
    expected_digest = _review_digest(review_summary)
    if evidence["source_review_sha256"] != expected_digest:
        raise Blocked(
            "review-disposition-stale-review",
            "Disposition must bind the run's current canonical review summary",
        )
    if evidence["decided_by"] != "James":
        raise Blocked("review-disposition-invalid", "decided_by must be James")
    try:
        decided = datetime.fromisoformat(str(evidence["decided_at"]).replace("Z", "+00:00"))
        offset = decided.utcoffset() if decided.tzinfo is not None else None
        timestamp = (
            decided.timestamp()
            if offset is not None and offset.total_seconds() == 0
            else float("nan")
        )
    except (ValueError, OverflowError):
        timestamp = float("nan")
    if not allow_historical and not (math.isfinite(timestamp) and 0 < timestamp <= now + 300):
        raise Blocked("review-disposition-invalid", "decided_at must be a non-future UTC time")
    if allow_historical and not (math.isfinite(timestamp) and timestamp > 0):
        raise Blocked("review-disposition-invalid", "decided_at must be a UTC time")

    findings = evidence["findings"]
    expected_findings = _finding_lines(review_summary)
    if not isinstance(findings, list) or not findings or len(findings) > 32:
        raise Blocked("review-disposition-invalid", "findings must be a bounded non-empty list")
    provided_sources: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {
            "finding",
            "disposition",
            "direction",
            "acceptance",
        }:
            raise Blocked("review-disposition-invalid", "Unexpected finding disposition shape")
        source = finding["finding"]
        disposition = finding["disposition"]
        direction = finding["direction"]
        acceptance = finding["acceptance"]
        if not isinstance(source, str) or not source or len(source) > 8000:
            raise Blocked("review-disposition-invalid", "Each source finding must be bounded text")
        if disposition not in _DISPOSITIONS:
            raise Blocked("review-disposition-invalid", "Unknown finding disposition")
        if not isinstance(direction, str) or not direction.strip() or len(direction) > 4000:
            raise Blocked("review-disposition-invalid", "Each finding requires bounded direction")
        if (
            not isinstance(acceptance, list)
            or not 1 <= len(acceptance) <= 8
            or any(
                not isinstance(item, str) or not item.strip() or len(item) > 1000
                for item in acceptance
            )
        ):
            raise Blocked(
                "review-disposition-invalid", "Each finding requires bounded acceptance criteria"
            )
        provided_sources.append(source)
    if provided_sources != expected_findings:
        raise Blocked(
            "review-disposition-incomplete",
            "Disposition must cover every current finding exactly once and in review order",
        )
