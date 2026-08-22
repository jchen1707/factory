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
    # The factory commits the agent's work before it verifies, so the working tree is
    # clean and `gate_report.mjs`'s default `git status --porcelain` "did this app change?"
    # check sees nothing — every gate would be `skipped_unchanged` regardless of what the
    # change touched. Against the run's base ref, `git diff --name-only <base>..HEAD` sees
    # the committed change instead, so the gates actually run. `--base` switches the report
    # to that check; the Stop hook never passes a base, because its working tree is the
    # agent's uncommitted edits. See `gatedChangeSince` in the vendored `verify.mjs`.
    # `run.base_ref` is set at worktree creation; the `project.base_ref` fallback mirrors
    # `cli._restore`'s spelling so a run row that predates the column still verifies.
    base_ref = ctx.run.base_ref or ctx.project.base_ref

    if ctx.dry_run:
        ctx.would(
            f"run node {_REPORT_HOOK} --json --base {base_ref} in {ctx.project.build_sandbox}"
        )
        ctx.would(f"  workdir {worktree}")
        ctx.would("write gates.json beside last-message.json; cross-check gates_run")
        ctx.would("advance on pass -> reviewing, fail -> implementing, incomplete -> blocked")
        advance(ctx, State.REVIEWING)
        return

    attempt = ctx.run.attempt
    attempt_dir = AttemptDir(ctx.factory_dir / "run" / str(attempt))

    gates_run = _gates_run(attempt_dir)
    argv = ["node", _REPORT_HOOK, "--json"]
    if _claims_opt_in(ctx.harness, gates_run):
        argv.append("--all")
    if base_ref:
        argv += ["--base", base_ref]

    completed = ctx.sandbox.exec_sync(
        ctx.project.build_sandbox,
        argv,
        workdir=str(worktree),
        env=dict(ctx.project.env),
        timeout=ctx.timeout_for(State.VERIFYING),
    )
    # Written before the parse, because `schema-invalid` was the one failure mode that
    # destroyed its own evidence: `_validated_report` raises, `gates.json` below never
    # gets written, and the only record left is the exception text in the `checks` row.
    # A report the parser rejects is exactly the one someone has to read by hand.
    _write_raw(attempt_dir, completed)

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
    toolchain could not prove ran. Returns the disagreeing gate names, in claim order.

    The agent's `gates_run` entries are free-form strings — the schema is `array of string`
    and the prompt fixes no format — so the real agent writes the commands it ran with the
    outcome appended (`"uv run ruff check . — passed"`), not the bare gate name the report
    uses (`"ruff check"`). A claim is matched to a report gate by **longest-name
    containment**: the report gate whose `name` sits inside the claim. Longest-first is
    what stops `"uv run pytest -m integration — 3 passed"` matching the bare `pytest` gate
    before the `pytest -m integration` gate. A claim no report gate name sits inside is
    absent from the report — the same disagreement. The gate's *status* comes from the
    report, never the claim's text: this check proves the gate ran, and the verdict (not
    the claim) decides pass-vs-fail. An honest `fail` in a claim is accepted here."""
    # Longest gate name first, so `pytest -m integration` is tried before `pytest`.
    ordered = sorted(report_gates, key=lambda gate: len(gate["name"]), reverse=True)
    mismatched: list[str] = []
    for claim in gates_run:
        status: str | None = None
        matched: str | None = None
        for gate in ordered:
            if gate["name"] and gate["name"] in claim:
                status = gate["status"]
                matched = gate["name"]
                break
        if status not in _RAN:
            # The report gate name when one matched (clearer in the block message than the
            # agent's command string); the raw claim only when nothing matched.
            mismatched.append(matched if matched is not None else claim)
    return mismatched


def _write_raw(attempt_dir: AttemptDir, completed: Completed) -> None:
    """The two streams of the report hook, exactly as the sandbox produced them.

    Split the way `implement.py` splits them (`AttemptDir.stderr`) and for the same
    reason: one is the data the state machine advances on, the other is evidence about
    the run that produced it. `gates.json` is the parsed document and only exists when
    the parse succeeded; these two always exist, so a `schema-invalid` block leaves
    something to read. Best-effort — a report that cannot be filed is not a reason to
    lose the verdict."""
    try:
        attempt_dir.path("gates.stdout.txt").write_text(completed.stdout, encoding="utf-8")
        attempt_dir.path("gates.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    except OSError:
        pass


def _validated_report(ctx: Context, completed: Completed, attempt: int) -> dict[str, Any]:
    """The report is schema-valid or the state does not advance. The JSON `verdict` field
    is authoritative — the exit code only mirrors it — so a malformed document blocks
    rather than rounding to green. Deliberately strict: taking the first document out of a
    stream with trailing bytes on it (`raw_decode`) would round an unaccounted-for exec
    path to green, which is the failure this check exists to prevent. The raw streams are
    already on disk by here (`_write_raw`), so a rejected document is still readable."""
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
