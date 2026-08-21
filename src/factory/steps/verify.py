"""`verifying -> reviewing|implementing|blocked` — the machine-readable gate report.

The third of §15.1's three independent verification signals — the Stop hook, the agent's
`gates_run` claim, and this report — and the one the state machine advances on,
cross-checked against the first two. The factory holds no gate command and no review
prompt: it invokes the vendored `gate_report.mjs` by path, reads the JSON back, and
transitions on the `verdict` field. A gate name in this file is a review failure; the
report hook is named as a path, and the gates themselves come out of the report and
`harness.config.json`, never out of this source.

The attempt directory is the **implement** attempt's, resolved the way `implement.py`
did — except that implement already recorded the attempt, so here `ctx.run.attempt` *is*
that attempt. Adding one again would point at a directory that does not exist yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factory import artifacts
from factory.agent.base import SchemaInvalid, validate_against_schema
from factory.artifacts import AttemptDir
from factory.harness import HarnessConfig
from factory.machine import Blocked, State
from factory.sandbox.base import Completed
from factory.steps import Context, advance

__all__ = ["run"]

STEP = "verify"

#: The vendored layer-A report hook, invoked by path inside the build sandbox. The
#: factory never names a gate; it names the document that lists them.
_REPORT_HOOK = ".agents/vendor/harness/hooks/gate_report.mjs"

#: Opt-in gate kinds (§12.1): the Stop hook does not run them, so without `--all` they
#: come back `not_applicable`. The factory asserts the agent's claim by passing `--all`,
#: cross-checked by name against `harness.config.json`'s own gates — never by
#: pattern-matching the diff.
_OPT_IN_KINDS = frozenset({"e2e", "integration"})

#: A claimed gate must show up as one of these in the report. Anything else —
#: `unavailable`, `not_applicable`, `skipped_unchanged`, or absent — is a disagreement
#: between what the agent said it ran and what the toolchain could prove (§15.1). An
#: honest `fail` is accepted here; the loop-back happens on the verdict, not the claim.
_RAN = frozenset({"pass", "fail"})


def run(ctx: Context) -> None:
    worktree = ctx.worktree

    if ctx.dry_run:
        ctx.would(f"run node {_REPORT_HOOK} --json in {ctx.project.build_sandbox}")
        ctx.would(f"  workdir {worktree}")
        ctx.would("write gates.json beside last-message.json; cross-check gates_run")
        ctx.would("advance on pass -> reviewing, fail -> implementing, incomplete -> blocked")
        advance(ctx, State.REVIEWING)
        return

    attempt = ctx.run.attempt
    attempt_dir = AttemptDir(worktree / ".factory" / "run" / str(attempt))

    gates_run = _gates_run(attempt_dir)
    argv = ["node", _REPORT_HOOK, "--json"]
    if _claims_opt_in(ctx.harness, gates_run):
        argv.append("--all")

    completed = ctx.sandbox.exec_sync(
        ctx.project.build_sandbox,
        argv,
        workdir=str(worktree),
        env=dict(ctx.project.env),
        timeout=ctx.timeout_for(State.VERIFYING),
    )
    report = _validated_report(ctx, completed, attempt)

    # Beside `last-message.json`, so one attempt directory holds the claim and its proof.
    artifacts.write_json(attempt_dir.path("gates.json"), report)
    ctx.store.record_check(
        ctx.run.id,
        attempt,
        "gate_report",
        "pass" if report["verdict"] == "pass" else "fail",
        detail=json.dumps(
            {
                "verdict": report["verdict"],
                "gates": [{"name": g["name"], "status": g["status"]} for g in report["gates"]],
            }
        )[:2000],
        artifact=str(attempt_dir.root),
    )
    ctx.log("verify.report", verdict=report["verdict"], gates=len(report["gates"]))

    mismatched = _evidence_mismatch(gates_run, report["gates"])
    if mismatched:
        # The mismatch is recorded before the block, so the disagreeing gate names are in
        # the evidence even when the announcement transport is down — the same shape as
        # the implement step's schema-invalid check.
        ctx.store.record_check(
            ctx.run.id,
            attempt,
            "evidence-mismatch",
            "fail",
            detail=", ".join(mismatched)[:2000],
        )
        raise Blocked(
            "evidence-mismatch",
            "the agent claimed gates the report did not show as pass or fail: "
            + ", ".join(mismatched),
        )

    verdict = report["verdict"]
    if verdict == "pass":
        advance(ctx, State.REVIEWING)
    elif verdict == "fail":
        # Loop back for a real failure. The next `implement` resolves
        # `attempt = ctx.run.attempt + 1`, so this attempt's evidence stays in its own
        # directory and the agent gets a fresh one to fix the failure in.
        advance(ctx, State.IMPLEMENTING)
    else:  # incomplete — never a pass
        # An unavailable gate or a missing app is an environment/manifest problem, not a
        # code failure, so it does not loop back to the agent. `gates-incomplete` is a
        # new slug; `Blocked` accepts any, and `machine.py:179` lists the canonical ones.
        # This is the one design decision step 2 makes — see the handoff.
        raise Blocked(
            "gates-incomplete",
            "the gate report is incomplete: a gate could not start or an app had no "
            f"config of its own. verdict={verdict}; gates="
            + ", ".join(f"{g['name']}={g['status']}" for g in report["gates"])
            + (f"; missingApps={report['missingApps']}" if report.get("missingApps") else ""),
        )


def _gates_run(attempt_dir: AttemptDir) -> list[str]:
    """The gates the implementer claimed it ran, already schema-validated by
    `implement.py`. Read back from `last-message.json` so the cross-check is against the
    same evidence the run recorded, not a second copy of the claim."""
    if not attempt_dir.last_message.exists():
        raise Blocked(
            "schema-invalid",
            "no implement last-message.json to cross-check gates_run against",
        )
    try:
        payload = json.loads(attempt_dir.last_message.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Blocked("schema-invalid", f"last-message.json is not JSON: {exc}") from exc
    claimed = payload.get("gates_run", [])
    if not isinstance(claimed, list):
        raise Blocked("schema-invalid", f"gates_run is not a list: {type(claimed).__name__}")
    return [str(gate) for gate in claimed]


def _claims_opt_in(harness: HarnessConfig | None, gates_run: list[str]) -> bool:
    """Pass `--all` iff the agent claimed a gate whose `kind` is opt-in, cross-checked by
    name against `harness.config.json`'s own gates. The claim is the agent's structured
    answer; the name→kind lookup is the deterministic check against the canonical config —
    never a pattern match over the diff (§12.1). Without `--all`, opt-in gates come back
    `not_applicable`, which is correct when the agent did not claim them."""
    if harness is None:
        return False
    kinds = {gate.name: gate.kind for gate in harness.gates}
    return any(kinds.get(name) in _OPT_IN_KINDS for name in gates_run)


def _evidence_mismatch(gates_run: list[str], report_gates: list[dict[str, Any]]) -> list[str]:
    """The §15.1 cross-check. Every gate the agent claimed must appear in the report with
    a status of `pass` or `fail`. A claimed gate that is `unavailable`, `not_applicable`,
    `skipped_unchanged`, or absent is a disagreement — the agent said it ran something the
    toolchain could not prove ran. Returns the disagreeing gate names, in claim order."""
    by_name = {gate["name"]: gate["status"] for gate in report_gates}
    return [name for name in gates_run if by_name.get(name) not in _RAN]


def _validated_report(ctx: Context, completed: Completed, attempt: int) -> dict[str, Any]:
    """The report is schema-valid or the state does not advance. The JSON `verdict` field
    is authoritative — the exit code only mirrors it — so a malformed document blocks
    rather than rounding to green. The raw stdout is kept in `gates.json` either way."""
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        ctx.store.record_check(
            ctx.run.id, attempt, "gate_report_schema", "fail", detail=f"stdout is not JSON: {exc}"
        )
        raise Blocked("schema-invalid", f"gate report stdout is not JSON: {exc}") from exc

    schema = json.loads(_schema_path(ctx).read_text(encoding="utf-8"))
    try:
        validate_against_schema(payload, schema)
    except SchemaInvalid as exc:
        ctx.store.record_check(ctx.run.id, attempt, "gate_report_schema", "fail", detail=str(exc))
        raise Blocked("schema-invalid", f"gate report failed schema validation: {exc}") from exc

    ctx.store.record_check(ctx.run.id, attempt, "gate_report_schema", "pass")
    return dict(payload)


def _schema_path(ctx: Context) -> Path:
    return ctx.home / "schemas" / "gate_report.schema.json"
