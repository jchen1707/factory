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

__all__ = ["collect", "start"]

STEP = "verify"

#: The vendored layer-A report hook, invoked by path inside the build sandbox. The
#: factory never names a gate; it names the document that lists them.
_REPORT_HOOK = ".agents/vendor/harness/hooks/gate_report.mjs"

#: Opt-in gate kinds (§12.1): the Stop hook does not run them, so unasserted they come
#: back `not_applicable`. The factory asserts the agent's claim per gate, by name,
#: cross-checked against `harness.config.json`'s own gates — never by pattern-matching
#: the diff.
_OPT_IN_KINDS = frozenset({"e2e", "integration"})

#: A claimed gate must show up as one of these in the report. Anything else —
#: `unavailable`, `not_applicable`, `skipped_unchanged`, or absent — is a disagreement
#: between what the agent said it ran and what the toolchain could prove (§15.1). An
#: honest `fail` is accepted here; the loop-back happens on the verdict, not the claim.
_RAN = frozenset({"pass", "fail"})

#: `disabled` is a gate the config switched off with `enabled: false`. It is not a gate
#: that ran, so it is not in `_RAN` — but it is not a disagreement either, and this
#: distinction is load-bearing. Which opt-in gates an agent claims is **not
#: deterministic**: FRO-7 was run twice from one prompt, and the second run claimed
#: `lighthouse` where the first had not. An agent free to run a disabled gate on its own
#: and report it honestly must not be blocked for it, because the alternative is a run
#: that dies on `evidence-mismatch` instead of `env-gate-failed` — the same wall wearing
#: a different name, and the whole reason `enabled: false` exists.
#:
#: The gate still did not run *as a gate*: nothing here promotes it to evidence. The
#: report says `disabled`, the verdict ignores it, and this set only stops the claim from
#: being read as a lie.
_CLAIM_SATISFIED = _RAN | {"disabled"}


def start(ctx: Context, *, actor: str = AUTOMATIC) -> tuple[AttemptDir, RunHandle] | None:
    """Spawn the gate report as a detached node process. Returns once it is running.

    `None` means a dry run, which walks the states and executes nothing.

    The gate report runs in the build sandbox against the worktree, with `--base` set to
    the run's base ref so `gate_report.mjs`'s "did this app change?" check sees the
    committed change rather than a clean working tree (every gate would otherwise be
    `skipped_unchanged`). One `--gate <name>` is passed per opt-in gate the agent
    claimed, cross-checked by name against `harness.config.json`.

    `actor` threads through the `advance` into `verifying`. The hop is automatic for
    the tick's forward dispatch and the resumable re-run, but `BLOCKED -> verifying` is
    `unblock-is-a-judgement`, so a human `factory resume --from verifying` passes
    `"human"` or the advance refuses.
    """
    worktree = ctx.worktree
    base_ref = ctx.run.base_ref or ctx.project.base_ref

    attempt = ctx.run.attempt
    attempt_dir = AttemptDir(ctx.factory_dir / "run" / str(attempt))
    # Reuse the implement attempt's directory — verify reads its `last-message.json` and
    # the deliver step archives the whole dir — but clear the implementer's stale
    # `exit`/`heartbeat` first, or `poll` would read a finished implement attempt as the
    # gate report's terminal record. Evidence files (`last-message.json`, `events.jsonl`)
    # are left in place.
    attempt_dir.clear_liveness()

    if ctx.project.requires_clone:
        # The build sandbox is shared across the project's runs, so a second run may have
        # checked out its own branch in the clone between the implement attempt finishing
        # and this tick. The gate report runs against the clone's working tree, so put it
        # back on the run's branch first — or the gates would run against the wrong ticket's
        # code while the evidence points at this one. No-op on the forward path; the
        # `exec_sync` it issues starts a stopped sandbox, exactly like `fetch_back` does.
        from factory.steps import clone as clone_step

        clone_step.ensure_on_branch(ctx)

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
    for name in _asserted_opt_in_gates(harness, gates_run):
        argv += ["--gate", name]
    if base_ref:
        argv += ["--base", base_ref]
    return argv


def collect(ctx: Context, attempt_dir: AttemptDir, attempt: int) -> None:
    """Read the gate report off the filesystem and decide what it proved.

    Called by `run` moments after the gate report exits, or by `reap` on a later tick —
    possibly in a different process, after a reboot. Nothing here reads anything `start`
    held in memory: the report is on disk at `gates.stdout.txt`, the claim at
    `last-message.json`, both under the same absolute path on both sides.

    A `--from verifying` resume re-runs the gate report rather than trusting a stale
    `gates.json`: `recovery.resume` only advances into `verifying`, and the tick's
    `START_NEEDED` branch calls `start` again, so `collect` reads a freshly written
    `gates.stdout.txt`. That is deliberate — re-running is what surfaced a hidden
    environment-gate failure on FRO-6, and `env-gate-failed` (below) makes a re-run safe by
    blocking instead of looping back. Reading the prior `gates.json` would round a stale
    answer to green and is never done.
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
        failing = [g for g in report["gates"] if g.get("status") == "fail"]
        if failing and all(g.get("caveat") for g in failing):
            # An environment gate the agent cannot fix by writing code — lighthouse
            # without Chrome, a browser gate without the browser — fails, and its
            # `caveat` is the config author's own flag that the gate is conditional on
            # something outside the diff. Under `--all` (§12.1) every opt-in gate runs,
            # so a ticket that claims one in-scope opt-in gate (playwright) also forces
            # every other (lighthouse), and looping back to `implementing` here would
            # spend another ~10 M-token attempt "fixing" a missing tool — repeatedly,
            # until the attempt budget exhausted to `failed`. That is the daemon hazard,
            # and it bites a manual resume today. A real code gate (ruff, pytest, mypy)
            # carries `caveat: null`, so an honest code failure still loops back; only a
            # failure where *every* failing gate is caveated is treated as environmental.
            # `blocked` is a human-judgement state: install the tool the caveat names, or
            # `factory resume <TICKET> --from reviewing` to bypass and let the PR open
            # with the failure reported honestly in its body.
            raise Blocked(
                "env-gate-failed",
                "the gate report failed only on gates the agent cannot fix by writing "
                "code; their caveats name an environment condition, not a code defect. "
                "Install the tool the caveat names, or resume with `--from reviewing` "
                "to bypass and report the failure honestly in the PR: "
                + ", ".join(f"{g['name']} ({g.get('caveat')})" for g in failing),
            )
        if _next_is_rewind(ctx):
            from factory.steps import plan as plan_step

            plan_step.start(ctx)
            return
        # Loop back for a real failure, unless this is the third consecutive one. §16.3a
        # rung 3 rewinds to `planning` rather than running the same prompt a third time, and
        # the edge lives here: `verifying -> planning` is in the table and
        # `implementing -> planning` is not, so the rewind is decided *before* the loop-back
        # advances the state. `plan.start` advances and spawns in one call, exactly the way
        # `resume_run`'s REWIND branch does; a rewind is one attempt with two phases (plan,
        # then implement-from-plan), so `plan.start`'s `attempt = ctx.run.attempt + 1` is
        # correct here too.
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
        #
        # The caveat rides along on the gates that could not run, because this is a
        # human-judgement state and the human's next act is to install something. Naming
        # the gate says *that* it could not run; the caveat is the config author's own
        # sentence saying *what to do about it*, which is the difference between a message
        # that ends the search and one that starts it. `env-gate-failed` has always carried
        # them for the same reason. Only on the gates that did not run: a caveat beside a
        # green gate is a note about a vacuous pass, and repeating it here would pad a
        # message about missing tools with gates that are fine.
        unavailable = [g for g in report["gates"] if g.get("status") == "unavailable"]
        needs = ", ".join(f"{g['name']} ({g['caveat']})" for g in unavailable if g.get("caveat"))
        raise Blocked(
            "gates-incomplete",
            "the gate report is incomplete: a gate could not start or an app had no "
            f"config of its own. verdict={verdict}; gates="
            + ", ".join(f"{g['name']}={g['status']}" for g in report["gates"])
            + (f"; missingApps={report['missingApps']}" if report.get("missingApps") else "")
            + (f"; install what these name: {needs}" if needs else ""),
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


def _next_is_rewind(ctx: Context) -> bool:
    """Is the next attempt rung 3 of §16.3a's ladder — the rewind to `planning`?

    Lazy import so `verify` does not pull `recovery` (which talks to the store) at module
    load. `decide` is the single ladder authority, shared with `resume_run`, so the
    loop-back and the recovery path cannot disagree about when a rung rewinds.
    """
    from factory import recovery

    return recovery.next_attempt_disposition(ctx).disposition is recovery.Disposition.REWIND


def _asserted_opt_in_gates(harness: HarnessConfig | None, gates_run: list[str]) -> list[str]:
    """The opt-in gates the agent claimed, by their canonical `harness.config.json` names.

    The claim is the agent's structured answer; the name→kind lookup is the deterministic
    check against the canonical config — never a pattern match over the diff (§12.1). A
    gate the agent did not claim is not asserted, so it comes back `not_applicable`.

    A gate with `enabled: false` is never asserted, whoever claimed it. Missing this is
    harmless — layer A reports `disabled` regardless of the assertion, deliberately, so
    that an operator who switched a gate off gets the same answer whatever the caller
    said — but the argv would then state an intent the factory does not have, and the
    argv is the record of what layer D asked for.

    This used to return a bool and the caller passed `--all`, which is where FRO-7 died.
    `--all` asserts *every* opt-in gate's `when` clause at once, and a `when` is prose no
    machine can evaluate — so an agent that honestly ran `playwright` also asserted
    lighthouse's "performance or accessibility budgets are in scope", which was false for
    a status-filter feature and which cannot pass on this machine at all (its own caveat:
    the performance category scores null against the installed Chrome). The run blocked
    with `env-gate-failed` on a gate that should never have executed, and no amount of
    implementing could have cleared it. Asserting per gate is the repair: the factory now
    asserts exactly what the agent claimed and nothing adjacent to it.

    Order follows the config, not the claim, so the argv is stable across runs whose
    agent happened to list its gates differently. Deduplicated: two claims naming one
    gate assert it once.
    """
    if harness is None:
        return []
    kinds = {gate.name: gate.kind for gate in harness.gates}
    claimed = {_gate_named_in(claim, kinds) for claim in gates_run}
    return [
        gate.name
        for gate in harness.gates
        if gate.kind in _OPT_IN_KINDS and gate.name in claimed and gate.enabled
    ]


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
    a status of `pass` or `fail` — or `disabled`, see `_CLAIM_SATISFIED`. A claimed gate
    that is `unavailable`, `not_applicable`, `skipped_unchanged`, or absent is a
    disagreement — the agent said it ran something the toolchain could not prove ran.
    Returns the disagreeing gate names, in claim order.

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
        if status not in _CLAIM_SATISFIED:
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
