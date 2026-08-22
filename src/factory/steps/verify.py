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

Two-phase, like `implement`: `start` spawns the gate report as a detached node process
inside the build sandbox (heartbeat + atomic `exit`), and `collect` reads it back off
the filesystem and advances. A tick that blocked for the length of a gate suite would be
a tick that could not reap anything else, which the daemon cannot have. `factory run`
uses `run` (start → await → collect); the tick uses `start` and `collect` separately.
"""

from __future__ import annotations

import json
import shlex
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from factory import artifacts
from factory.agent.base import SchemaInvalid, validate_against_schema
from factory.artifacts import AttemptDir
from factory.harness import HarnessConfig
from factory.machine import AUTOMATIC, Blocked, State
from factory.sandbox.base import RunHandle, detached_shell_script
from factory.steps import Context, advance

__all__ = ["collect", "run", "start"]

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

POLL_INTERVAL_SECONDS = 10


def run(ctx: Context) -> None:
    """Start the gate report and stay with it until it ends — `factory run`'s path.

    `factory tick` uses `start` and `collect` separately, because a tick that blocked for
    the length of a gate suite could not reap anything else. The two paths share every
    line that matters: this one is `start`, a poll loop, and `collect`.
    """
    started = start(ctx)
    if started is None:
        return
    attempt_dir, handle = started
    _await_exit(ctx, attempt_dir, handle)
    collect(ctx, attempt_dir, ctx.run.attempt)


def start(ctx: Context, *, actor: str = AUTOMATIC) -> tuple[AttemptDir, RunHandle] | None:
    """Spawn the gate report as a detached node process. Returns once it is running.

    `None` means a dry run, which walks the states and executes nothing.

    The gate report runs in the build sandbox against the worktree, with `--base` set to
    the run's base ref so `gate_report.mjs`'s "did this app change?" check sees the
    committed change rather than a clean working tree (every gate would otherwise be
    `skipped_unchanged`). `--all` is passed iff the agent claimed an opt-in gate,
    cross-checked by name against `harness.config.json`.

    `actor` threads through the `advance` into `verifying`. The hop is automatic for
    the tick's forward dispatch and the resumable re-run, but `BLOCKED -> verifying` is
    `unblock-is-a-judgement`, so a human `factory resume --from verifying` passes
    `"human"` or the advance refuses.
    """
    worktree = ctx.worktree
    base_ref = ctx.run.base_ref or ctx.project.base_ref

    if ctx.dry_run:
        ctx.would(
            f"run node {_REPORT_HOOK} --json --base {base_ref} in {ctx.project.build_sandbox}"
        )
        ctx.would(f"  workdir {worktree}")
        ctx.would("write gates.json beside last-message.json; cross-check gates_run")
        ctx.would("advance on pass -> reviewing, fail -> implementing, incomplete -> blocked")
        advance(ctx, State.REVIEWING)
        return None

    attempt = ctx.run.attempt
    attempt_dir = AttemptDir(ctx.factory_dir / "run" / str(attempt))
    # Reuse the implement attempt's directory — verify reads its `last-message.json` and
    # the deliver step archives the whole dir — but clear the implementer's stale
    # `exit`/`heartbeat` first, or `poll` would read a finished implement attempt as the
    # gate report's terminal record. Evidence files (`last-message.json`, `events.jsonl`)
    # are left in place.
    attempt_dir.clear_liveness()

    gates_run = _gates_run(attempt_dir)
    argv = _gate_argv(ctx.harness, gates_run, base_ref)
    body = (
        " ".join(shlex.quote(part) for part in argv)
        + f" > {shlex.quote(str(attempt_dir.path('gates.stdout.txt')))}"
        + f" 2> {shlex.quote(str(attempt_dir.path('gates.stderr.txt')))}"
    )
    script = detached_shell_script(
        heartbeat_path=attempt_dir.heartbeat,
        exit_path=attempt_dir.exit_file,
        body=body,
    )

    ctx.store.start_attempt(
        ctx.run.id,
        attempt,
        State.VERIFYING,
        sandbox=ctx.project.build_sandbox,
        artifact_dir=str(attempt_dir.root),
    )
    ctx.refresh()
    # The hop into `verifying` is recorded once, by whoever put the run here. From
    # `implementing` (via implement.collect) that was the previous step; from `blocked`
    # it is this `start` under a human actor. Re-recording `verifying -> verifying` is an
    # illegal transition, so `start` advances only when it is not already there.
    if ctx.state is not State.VERIFYING:
        advance(ctx, State.VERIFYING, actor=actor)

    handle = RunHandle(
        run_id=ctx.run.id,
        attempt=attempt,
        sandbox=ctx.project.build_sandbox,
        workdir=str(worktree),
        attempt_dir=attempt_dir.root,
    )
    ctx.sandbox.exec_detached(handle, script, dict(ctx.project.env))
    ctx.log("verify.started", sandbox=handle.sandbox, base_ref=base_ref)
    return attempt_dir, handle


def _gate_argv(harness: HarnessConfig | None, gates_run: list[str], base_ref: str) -> list[str]:
    argv = ["node", _REPORT_HOOK, "--json"]
    if _claims_opt_in(harness, gates_run):
        argv.append("--all")
    if base_ref:
        argv += ["--base", base_ref]
    return argv


def _await_exit(ctx: Context, attempt_dir: AttemptDir, handle: RunHandle) -> None:
    """Watch the `exit` file. A timeout is never silently a success.

    On timeout the node process is killed and the wrapper's `exit` file is waited for,
    so the attempt ends with a real terminal record rather than a truncated one — the
    same shape as `implement._await_exit`.
    """
    timeout = ctx.timeout_for(State.VERIFYING)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if attempt_dir.exit_file.exists():
            return
        ctx.store.renew_lease(ctx.run.id, ttl_seconds=900)
        time.sleep(POLL_INTERVAL_SECONDS)

    ctx.log("verify.timeout", level="warning", seconds=timeout)
    kill = getattr(ctx.sandbox, "kill_agent", None)
    if kill is not None:
        kill(handle.sandbox)
    for _ in range(12):
        if attempt_dir.exit_file.exists():
            break
        time.sleep(5)
    from factory.machine import Resumable

    raise Resumable(
        "verify-timeout", f"no exit file after {timeout}s; the gate report was signalled"
    )


def collect(ctx: Context, attempt_dir: AttemptDir, attempt: int) -> None:
    """Read the gate report off the filesystem and decide what it proved.

    Called by `run` moments after the gate report exits, or by `reap` on a later tick —
    possibly in a different process, after a reboot. Nothing here reads anything `start`
    held in memory: the report is on disk at `gates.stdout.txt`, the claim at
    `last-message.json`, both under the same absolute path on both sides.
    """
    recorded = _already_recorded(ctx, attempt)
    report = _validated_report(ctx, attempt_dir, attempt, recorded=recorded)
    artifacts.write_json(attempt_dir.path("gates.json"), report)
    if not recorded:
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

    gates_run = _gates_run(attempt_dir)
    mismatched = _evidence_mismatch(gates_run, report["gates"])
    if mismatched:
        # The mismatch is recorded before the block, so the disagreeing gate names are in
        # the evidence even when the announcement transport is down — the same shape as
        # the implement step's schema-invalid check.
        if not recorded:
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
    # The attempt row is finished before the transition, so a crash between the two
    # re-collects idempotently rather than advancing twice.
    ctx.store.finish_attempt(
        ctx.run.id, attempt, State.VERIFYING, exit_code=attempt_dir.exit_code(), outcome=verdict
    )
    if verdict == "pass":
        advance(ctx, State.REVIEWING)
    elif verdict == "fail":
        # Loop back for a real failure. The next `implement` resolves
        # `attempt = ctx.run.attempt + 1`, so this attempt's evidence stays in its own
        # directory and the agent gets a fresh one to fix the failure in. `collect` does
        # NOT increment the attempt — the increment lives in `implement.start`, and
        # adding one here would point the next implement at a directory that does not
        # exist (handoff note 2).
        advance(ctx, State.IMPLEMENTING)
    else:  # incomplete — never a pass
        # An unavailable gate or a missing app is an environment/manifest problem, not a
        # code failure, so it does not loop back to the agent. `gates-incomplete` is a
        # new slug; `Blocked` accepts any, and `machine.py:179` lists the canonical ones.
        raise Blocked(
            "gates-incomplete",
            "the gate report is incomplete: a gate could not start or an app had no "
            f"config of its own. verdict={verdict}; gates="
            + ", ".join(f"{g['name']}={g['status']}" for g in report["gates"])
            + (f"; missingApps={report['missingApps']}" if report.get("missingApps") else ""),
        )


def _already_recorded(ctx: Context, attempt: int) -> bool:
    row = ctx.store.attempt_row(ctx.run.id, attempt, State.VERIFYING)
    return row is not None and row["ended_at"] is not None


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
    return any(
        kinds.get(_gate_named_in(claim, kinds) or "") in _OPT_IN_KINDS for claim in gates_run
    )


def _gate_named_in(claim: str, names: Iterable[str]) -> str | None:
    """The gate name that sits inside a free-form claim, longest candidate first.

    One matcher, two callers, and that is the point. `gates_run` entries are free-form —
    the schema is `array of string` and the prompt fixes no format — so a real claim reads
    `"playwright — pnpm test:e2e passed (4 tests)"`, never the bare `"playwright"` the
    config and the report use. `_evidence_mismatch` knew that and matched by containment;
    `_claims_opt_in` looked the claim up in a dict by equality, ten lines above it.

    Measured on FRO-6, 2026-08-22: every opt-in lookup missed, so `--all` was never
    passed, so `playwright` and `lighthouse` came back `not_applicable` — and the very
    same claims then failed the mismatch check for not being `pass` or `fail`. The agent
    had run both, honestly, and was blocked for it. Two copies of "how do I match a claim
    to a gate" is how that happened, so now there is one.

    Longest-first is load-bearing: it stops `"uv run pytest -m integration — 3 passed"`
    matching the bare `pytest` gate before the `pytest -m integration` gate.
    """
    for name in sorted(names, key=len, reverse=True):
        if name and name in claim:
            return name
    return None


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
    by_name = {gate["name"]: gate["status"] for gate in report_gates}
    mismatched: list[str] = []
    for claim in gates_run:
        matched = _gate_named_in(claim, by_name)
        status = by_name.get(matched) if matched is not None else None
        if status not in _RAN:
            # The report gate name when one matched (clearer in the block message than the
            # agent's command string); the raw claim only when nothing matched.
            mismatched.append(matched if matched is not None else claim)
    return mismatched


def _validated_report(
    ctx: Context, attempt_dir: AttemptDir, attempt: int, *, recorded: bool = False
) -> dict[str, Any]:
    """The report is schema-valid or the state does not advance. The JSON `verdict` field
    is authoritative — the exit code only mirrors it — so a malformed document blocks
    rather than rounding to green. Deliberately strict: taking the first document out of a
    stream with trailing bytes on it (`raw_decode`) would round an unaccounted-for exec
    path to green, which is the failure this check exists to prevent. The raw streams are
    already on disk (the detached script wrote them), so a rejected document is readable."""
    stdout_path = attempt_dir.path("gates.stdout.txt")
    try:
        raw = stdout_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Blocked("schema-invalid", f"the gate report wrote no stdout: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        if not recorded:
            ctx.store.record_check(
                ctx.run.id,
                attempt,
                "gate_report_schema",
                "fail",
                detail=f"stdout is not JSON: {exc}",
            )
        stderr_tail = artifacts.tail_lines(attempt_dir.path("gates.stderr.txt"), 20)
        raise Blocked(
            "schema-invalid", f"gate report stdout is not JSON: {exc}\n{stderr_tail}"
        ) from exc

    schema = json.loads(_schema_path(ctx).read_text(encoding="utf-8"))
    try:
        validate_against_schema(payload, schema)
    except SchemaInvalid as exc:
        if not recorded:
            ctx.store.record_check(
                ctx.run.id, attempt, "gate_report_schema", "fail", detail=str(exc)
            )
        raise Blocked("schema-invalid", f"gate report failed schema validation: {exc}") from exc

    if not recorded:
        ctx.store.record_check(ctx.run.id, attempt, "gate_report_schema", "pass")
    return dict(payload)


def _schema_path(ctx: Context) -> Path:
    return ctx.home / "schemas" / "gate_report.schema.json"
