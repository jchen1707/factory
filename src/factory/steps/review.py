"""`reviewing -> pr_ready | awaiting_human` — the two-tier review (§15.2).

Tier 1 is always run: Standards and Spec, independently, in a second, read-only sandbox
whose workspace is the project mounted `:ro` and whose Codex config is overridden
per-invocation to `sandbox_mode="read-only"`. Two enforcement layers, neither of them a
prompt. The prompt is **assembled, not authored** — frame plus checklist, concatenated
exactly the way layer A's `full-review.js` `axisPrompt()` does it — so a review that runs
here and a review that runs through the workflow cannot drift apart.

Tier 2 is the full nine-axis fan-out, and it runs only when the change warrants it. The
trigger rules are all deterministic and all recorded; otherwise Tier 2 is skipped and the
PR body names the rule that skipped it. This is "do not run every skill for every ticket",
made mechanical.

**Reviewers never repair.** The reviewer sandbox cannot write. A critical-or-high finding
is a human decision (AGENTS.md reserves "is this finding real or over-engineering bait?"),
so the run goes to `awaiting_human` rather than auto-looping; the §15.2 "finding goes back
to the implementer's session" repair loop is deferred to Phase 4's resume-by-session-id.

The red-phase replay (§15.3) runs here too, as the first thing at the REVIEWING entry: the
machine makes `verifying -> awaiting_human` illegal while §15.3's `escalate` option routes
there, so the replay runs in `reviewing` where every outcome is reachable. See
`redphase.py`'s header for the reasoning.

Two-phase, like `implement` and `verify`: `start` runs the synchronous pre-checks
(`clone.fetch_back`, the red-phase replay, the weakening guard), assembles the axis
prompts, and spawns the codex fan-out as one detached script in the read-only review
sandbox; `collect` lands and validates the findings, writes the summary, and advances.
The red-phase replay stays synchronous because it runs the test gate in the **build**
sandbox, while the codex axes must stay in the read-only **review** sandbox (§4.4 —
enforcement by mount, not prompt), so the two cannot share one detached script. A
behaviour-change ticket therefore blocks the tick for one test-run's duration before the
fan-out detaches; accepted.

One trigger rule is narrowed by the split. `_decide_tier2`'s `tier1_has_human` rule
("Tier-1 found a critical/high → run the full fan-out") needs Tier-1's findings, which are
not available at `start` time because Tier-1 runs inside the detached script. `start`
computes the trigger with `tier1_has_human=False`, so the rule does not fire from the
two-phase path. A Tier-1 critical/high still routes the run to `awaiting_human` via the
transition below; only the *extra* Tier-2 findings in the narrow case (small diff, no
diff-based trigger, Tier-1 critical/high) are lost — and `--full-review` covers it on
demand. The rule stays in `_decide_tier2` for its table-driven test.
"""

from __future__ import annotations

import fnmatch
import json
import shlex
import shutil
import time
from pathlib import Path
from typing import Any

from factory import artifacts, repo
from factory.agent.base import SchemaInvalid, validate_against_schema
from factory.artifacts import AttemptDir
from factory.harness import HarnessConfig
from factory.machine import AUTOMATIC, Blocked, State
from factory.sandbox.base import RunHandle, SandboxSpec, Workspace, detached_shell_script
from factory.steps import Context, advance, redphase
from factory.steps import block as block_step
from factory.steps import clone as clone_step

__all__ = ["collect", "run", "start"]

STEP = "review"

#: §15.2's Tier-1 axes — Standards and Spec, independently. The agent names are the files
#: `axisPrompt` reads: `<agentDir>/<name>.md` (stack override) or the vendored shared frame,
#: plus `<checklistDir>/<name>.md` (the repo's checklist, optional).
_TIER1_AXES: tuple[tuple[str, str], ...] = (
    ("standards", "standards-reviewer"),
    ("spec", "spec-checker"),
)

#: What `review-summary.json` records when Tier 2 ran only because `--full-review` asked
#: for it. `deliver` reads this exact string, so it lives here rather than being spelled
#: twice.
FORCED = "ran:forced"

#: §15.2's Tier-2 trigger thresholds.
_TIER2_FILE_THRESHOLD = 10
_TIER2_LINE_THRESHOLD = 400

#: The directories whose own `AGENTS.md` exists — a Tier-2 trigger per stack.
_SENSITIVE_DIRS: dict[str, tuple[str, ...]] = {
    "python": ("src/app/ai/**",),
    "frontend": ("src/**/routes/**",),
}

#: Severities that route to a human rather than a PR. `critical`/`high` only; a
#: `medium`/`low` finding is recorded and carried in the PR body but does not stop delivery.
_HUMAN_SEVERITIES = frozenset({"critical", "high"})

#: The vendored layer-A findings schema, passed to `codex exec --output-schema` and
#: used to validate what comes back. One file, so the Codex path and the workflow path
#: cannot produce different finding shapes.
_FINDINGS_SCHEMA = ".agents/vendor/harness/schema/review-findings.schema.json"

POLL_INTERVAL_SECONDS = 10


def run(ctx: Context) -> None:
    """Start the review and stay with it until it ends — `factory run`'s path.

    `factory tick` uses `start` and `collect` separately, because a tick that blocked for
    the length of the review fan-out could not reap anything else. The two paths share
    every line that matters: this one is `start`, a poll loop, and `collect`.
    """
    started = start(ctx)
    if started is None:
        return
    attempt_dir, handle = started
    _await_exit(ctx, attempt_dir, handle)
    collect(ctx, attempt_dir, ctx.run.attempt)


def start(ctx: Context, *, actor: str = AUTOMATIC) -> tuple[AttemptDir, RunHandle] | None:
    """Run the synchronous pre-checks, then spawn the codex fan-out detached.

    Returns `None` when the run transitioned off `reviewing` without a detached spawn —
    a dry run, a red-phase escalation, or a test-weakening escalation. Returns
    `(attempt_dir, handle)` once the fan-out is running.

    `actor` threads through the `advance` into `reviewing`: automatic for the tick and the
    resumable re-run, but `BLOCKED -> reviewing` is `unblock-is-a-judgement`, so a human
    `factory resume --from reviewing` passes `"human"` or the advance refuses.
    """
    # The harness config is loaded at intake and carried on the context; the review cannot
    # run without it (the prompts are assembled from its `review.agentDir` / `checklistDir`).
    harness = ctx.harness
    if harness is None:
        raise Blocked("no-harness-config", f"no harness.config.json loaded for {ctx.run.linear_id}")

    # 0. A `--clone` project's branch lives inside the VM until now. Bring it home first:
    #    the replay's host-side diff, the reviewer sandbox (which mounts the host project
    #    `:ro` and has no clone of its own) and the host-execution guard all read an
    #    ordinary host worktree, and after this they get one. Nothing below knows.
    if ctx.project.requires_clone and not ctx.dry_run:
        clone_step.fetch_back(ctx)
    elif ctx.project.requires_clone:
        ctx.would(
            f"git -C {ctx.project.path} fetch "
            f"{clone_step.remote_name(ctx.project.build_sandbox)} {ctx.branch}"
        )
        ctx.would(
            f"git worktree add -b {ctx.branch} {clone_step.host_worktree_path(ctx)} FETCH_HEAD"
        )

    # 1. The red-phase replay (§15.3). May raise Blocked (test-proves-nothing,
    #    behaviour-change-without-test, inconclusive:block) or return "awaiting_human"
    #    (inconclusive:escalate). Either way the run leaves `reviewing` here, without a
    #    detached spawn, so `start` returns None and `run` returns.
    if not ctx.dry_run and redphase.replay(ctx) == "awaiting_human":
        _awaiting_human(ctx, "redphase-inconclusive", "the red-phase replay was inconclusive")
        return None

    # 2. The test-weakening guard — a judgement, so it escalates rather than blocks.
    offending = redphase.weakening_guard(ctx)
    if offending:
        _awaiting_human(
            ctx,
            "test-weakening",
            "the diff removes assertions in existing tests:\n"
            + "\n".join(f"- {line}" for line in offending[:20]),
        )
        return None

    if ctx.dry_run:
        ctx.would(
            f"Tier 1: standards + spec review in {ctx.project.review_sandbox} (read-only, sandbox_mode=read-only)"
        )
        ctx.would("Tier 2: evaluate trigger — run the full fan-out or skip + name the rule")
        ctx.would(
            f"advance {State.REVIEWING} -> {State.PR_READY} (clean) | {State.AWAITING_HUMAN} (finding)"
        )
        advance(ctx, State.PR_READY)
        return None

    # 3. Decide the fan-out: which axes run, and the Tier-2 rule the PR body names. The
    #    `tier1_has_human` trigger cannot fire here (Tier-1 has not run yet — see the
    #    module header), so it is passed False. `--full-review` overrides first.
    base_ref = ctx.run.base_ref or ctx.project.base_ref
    trigger = _tier2_trigger(ctx, harness, tier1_has_human=False)
    if ctx.run.full_review:
        tier2_rule = FORCED
        run_full = True
    elif trigger is None:
        tier2_rule = "ran"
        run_full = True
    else:
        tier2_rule = trigger  # the skip rule, named in the PR body
        run_full = False

    # 4. Assemble the per-axis prompts and the sandbox, then write a plan `collect` reads
    #    back. The findings land in the per-project scratch (the reviewer's one writable
    #    mount); `collect` moves them into this run's own directory.
    review_dir = ctx.state_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    scratch = _review_scratch(ctx)
    spec = _review_spec(ctx, scratch)
    ctx.sandbox.ensure(spec)
    schema_path = ctx.worktree / _FINDINGS_SCHEMA

    axes = []
    for label, agent in _TIER1_AXES:
        axes.append(
            _axis_entry(
                ctx,
                harness,
                review_dir,
                scratch,
                schema_path,
                "tier1",
                label,
                _axis_prompt(ctx.worktree, harness, agent, base_ref),
            )
        )
    if run_full:
        axes.append(
            _axis_entry(
                ctx,
                harness,
                review_dir,
                scratch,
                schema_path,
                "tier2",
                "full",
                _tier2_prompt(ctx, base_ref),
            )
        )
    artifacts.write_json(
        review_dir / "review-plan.json",
        {"tier2": tier2_rule, "axes": [a["plan"] for a in axes]},
    )

    # 5. The detached script: one codex block per axis, each reading its prompt from a
    #    file and writing findings (via `-o`) to the scratch. The envelope adds the
    #    heartbeat + atomic `exit` that `poll` and `reap` read.
    body = "\n".join(_axis_script_block(a) for a in axes)
    attempt = ctx.run.attempt
    # The review's liveness lives in the scratch, not at `ctx.factory_dir`, and this is
    # the one place the review's attempt directory differs from implement's and verify's.
    #
    # Those two run in the *build* sandbox, which mounts `ctx.factory_dir` rw for exactly
    # this. The reviewer runs in a different sandbox with a deliberately narrower spec —
    # the project `:ro` plus the scratch — and `ctx.factory_dir` is not in it: for a clone
    # project it is under `state/clone/<project>/`, which the review sandbox does not
    # mount at all, and for a bind-mounted project it is inside the worktree, which is
    # mounted read-only on purpose (§15.2 — a reviewer that can write to the code it is
    # reviewing is not a reviewer). Either way the wrapper cannot write its heartbeat, so
    # the detached run dies before its first beat and every tick reaps it as
    # `attempt-orphaned`, forever.
    #
    # Measured on FRO-7 run `b1aa9785bbe44663`: `cannot create .../\.factory/run/1/
    # heartbeat: Directory nonexistent`, then `reviewing -> resumable` on a loop until the
    # re-run ceiling would have failed the run. Nothing caught it earlier because review
    # only became a *detached* step in `147dc88`; before that it ran synchronously and
    # needed no heartbeat at all, which is why BAC-4 reviewed cleanly under the old shape.
    attempt_dir = AttemptDir(_sandbox_run_dir(scratch, ctx.run.id) / "run" / str(attempt))
    # Clear the stale `exit`/`heartbeat` a previous re-run left. The implement and verify
    # evidence is untouched by this — it is in a different tree now.
    attempt_dir.clear_liveness()
    script = detached_shell_script(
        heartbeat_path=attempt_dir.heartbeat,
        exit_path=attempt_dir.exit_file,
        body=body,
    )

    ctx.store.start_attempt(
        ctx.run.id,
        attempt,
        State.REVIEWING,
        sandbox=ctx.project.review_sandbox,
        artifact_dir=str(attempt_dir.root),
    )
    ctx.refresh()
    if ctx.state is not State.REVIEWING:
        advance(ctx, State.REVIEWING, actor=actor)

    handle = RunHandle(
        run_id=ctx.run.id,
        attempt=attempt,
        sandbox=ctx.project.review_sandbox,
        workdir=str(ctx.worktree),
        attempt_dir=attempt_dir.root,
    )
    ctx.sandbox.exec_detached(handle, script, dict(ctx.project.env))
    ctx.log("review.started", axes=len(axes), tier2=tier2_rule)
    return attempt_dir, handle


def _axis_entry(
    ctx: Context,
    harness: HarnessConfig,
    review_dir: Path,
    scratch: Path,
    schema_path: Path,
    tier: str,
    label: str,
    prompt: str,
) -> dict[str, Any]:
    """One axis's start-time bookkeeping: write its prompt, choose its paths, build argv."""
    # Every path the *sandbox* touches is in the scratch; every path the *host* reads the
    # evidence from is in the run directory. The prompt is read by codex, the event stream
    # and stderr are written by it, so all three sit in the scratch and `collect` lands
    # them — the same round trip the findings have always made.
    sandbox_dir = _sandbox_run_dir(scratch, ctx.run.id)
    prompt_path = sandbox_dir / f"review-{label}.prompt"
    prompt_path.write_text(prompt, encoding="utf-8")
    scratch_out = _sandbox_out(sandbox_dir, f"review-{label}.json")
    scratch_events = sandbox_dir / f"review-{label}.events.jsonl"
    scratch_stderr = sandbox_dir / f"review-{label}.stderr.log"
    out_path = review_dir / f"review-{label}.json"
    events_path = review_dir / f"review-{label}.events.jsonl"
    stderr_path = review_dir / f"review-{label}.stderr.log"
    argv = _review_argv(ctx, schema_path, scratch_out, workdir=ctx.worktree)
    return {
        "plan": {
            "tier": tier,
            "label": label,
            "prompt": str(prompt_path),
            "scratch_out": str(scratch_out),
            "scratch_events": str(scratch_events),
            "scratch_stderr": str(scratch_stderr),
            "out": str(out_path),
            "events": str(events_path),
            "stderr": str(stderr_path),
            "argv": list(argv),
        },
        "argv": argv,
        "prompt_path": prompt_path,
        "events_path": scratch_events,
        "stderr_path": scratch_stderr,
    }


def _axis_script_block(axis: dict[str, Any]) -> str:
    """The shell line for one axis: `codex exec … - < prompt > events 2> stderr`."""
    argv = " ".join(shlex.quote(str(part)) for part in axis["argv"])
    return (
        f"{argv} < {shlex.quote(str(axis['prompt_path']))} "
        f"> {shlex.quote(str(axis['events_path']))} "
        f"2> {shlex.quote(str(axis['stderr_path']))}"
    )


def _await_exit(ctx: Context, attempt_dir: AttemptDir, handle: RunHandle) -> None:
    """Watch the `exit` file. A timeout is never silently a success."""
    timeout = ctx.timeout_for(State.REVIEWING)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if attempt_dir.exit_file.exists():
            return
        ctx.store.renew_lease(ctx.run.id, ttl_seconds=900)
        time.sleep(POLL_INTERVAL_SECONDS)

    ctx.log("review.timeout", level="warning", seconds=timeout)
    kill = getattr(ctx.sandbox, "kill_agent", None)
    if kill is not None:
        kill(handle.sandbox)
    for _ in range(12):
        if attempt_dir.exit_file.exists():
            break
        time.sleep(5)
    from factory.machine import Resumable

    raise Resumable("review-timeout", f"no exit file after {timeout}s; the fan-out was signalled")


def collect(ctx: Context, attempt_dir: AttemptDir, attempt: int) -> None:
    """Land and validate the fan-out's findings, write the summary, and advance.

    Called by `run` after the fan-out exits, or by `reap` on a later tick — possibly in a
    different process. Nothing here reads anything `start` held in memory: the plan at
    `review-plan.json`, the findings in the per-project scratch, and the exit code are all
    on disk.
    """
    review_dir = ctx.state_dir / "review"
    plan = _read_plan(review_dir)
    recorded = _already_recorded(ctx, attempt)

    findings: list[dict[str, Any]] = []
    tier1_has_human = False
    for axis in plan.get("axes", []):
        label = str(axis["label"])
        scratch_out = Path(axis["scratch_out"])
        out_path = Path(axis["out"])
        events_path = Path(axis["events"])
        stderr_path = Path(axis["stderr"])
        _land(scratch_out, out_path)
        # The event stream and stderr come home the same way the findings do. `.get` with
        # a fallback so a plan written by an older build — one whose axes name only the
        # host paths — still collects rather than crashing on a missing key mid-recovery.
        _land(Path(axis.get("scratch_events", events_path)), events_path)
        _land(Path(axis.get("scratch_stderr", stderr_path)), stderr_path)
        axis_findings = _validated_findings(ctx, out_path, stderr_path, label)
        # A secret in the review's own stream is compromised the way one in the PR body is.
        artifacts.scan_for_secrets(
            _read_text(events_path) + _read_text(stderr_path), f"review {label}"
        )
        findings += axis_findings
        if axis.get("tier") == "tier1":
            tier1_has_human = tier1_has_human or any(
                f["severity"] in _HUMAN_SEVERITIES for f in axis_findings
            )
        if not recorded:
            ctx.store.record_check(
                ctx.run.id,
                attempt,
                f"review:{label}",
                "pass",
                detail=json.dumps(
                    [{"sev": f["severity"], "file": f.get("file")} for f in axis_findings]
                )[:2000],
                artifact=str(review_dir),
            )

    tier2_rule = str(plan.get("tier2", "no-trigger"))
    _write_summary(ctx, review_dir, findings, tier2_rule)
    ctx.store.finish_attempt(
        ctx.run.id, attempt, State.REVIEWING, exit_code=attempt_dir.exit_code(), outcome=tier2_rule
    )

    # 5. Transition. A critical/high finding (Tier-1 or Tier-2) is James's reserved
    #    judgement: route to the human queue rather than auto-looping.
    if tier1_has_human or any(f["severity"] in _HUMAN_SEVERITIES for f in findings):
        human = [f for f in findings if f["severity"] in _HUMAN_SEVERITIES]
        raise Blocked(
            "review-finding",
            "the review returned a finding that needs a human to triage:\n"
            + "\n".join(
                f"- [{f['severity']}] {f.get('file', '?')}: {f['summary']}" for f in human[:10]
            ),
        )
    advance(ctx, State.PR_READY)


def _already_recorded(ctx: Context, attempt: int) -> bool:
    row = ctx.store.attempt_row(ctx.run.id, attempt, State.REVIEWING)
    return row is not None and row["ended_at"] is not None


def _read_plan(review_dir: Path) -> dict[str, Any]:
    path = review_dir / "review-plan.json"
    if not path.exists():
        raise Blocked("review-plan-missing", f"no review-plan.json at {path}; was start run?")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Blocked("review-plan-missing", f"review-plan.json is not JSON: {exc}") from exc
    return loaded if isinstance(loaded, dict) else {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --------------------------------------------------------------------------------
# Tier 1 — Standards + Spec, read-only, assembled prompts
# --------------------------------------------------------------------------------


def _review_argv(
    ctx: Context, schema_path: Path, out_path: Path, workdir: Path | None = None
) -> list[str]:
    """`codex exec` for one axis — the same invocation the implement step uses.

    Not `codex exec review --base <ref>`: codex refuses that flag together with a prompt
    (`the argument '--base <BRANCH>' cannot be used with '[PROMPT]'`), and the prompt is
    the axis. An axis-less review would run the same generic pass twice and call it
    Standards and Spec, which is the failure §15.2 exists to prevent. Layer A's own
    `full-review.js` reaches the same conclusion from the other side: it names the diff
    inside the prompt and never uses the `review` subcommand.

    `--dangerously-bypass-hook-trust` is included for the same reason the implement step
    includes it: the worktree path is untrusted in `~/.codex/config.toml`, and without it
    Codex appends a project stanza for it. The `:ro` mount and `sandbox_mode=read-only`
    are what make the review safe; this flag only stops a config-file side effect.
    """
    role = ctx.routing.role("reviewer")
    return [
        "codex",
        "exec",
        "-m",
        role.model,
        "-c",
        f"model_reasoning_effort={role.effort}",
        "-c",
        "sandbox_mode=read-only",
        "--dangerously-bypass-hook-trust",
        "--json",
        "--output-schema",
        str(schema_path),
        "-o",
        str(out_path),
        # `-C` goes before the positional, never after: `-` is the prompt argument and a
        # flag trailing it is a flag the parser has already stopped reading.
        *(["-C", str(workdir)] if workdir else []),
        "-",  # prompt from stdin
    ]


def _sandbox_out(scratch: Path, name: str) -> Path:
    """Where a reviewer is told to write, for every tier.

    The scratch is the reviewer's one writable mount, so it is the only path a `-o`
    argument may name. Told to write anywhere else, codex exits 0 and produces nothing —
    `Failed to write last message file …: No such file or directory` on stderr — which
    reads downstream as a review that returned no findings file. Tier 1 learned this on
    BAC-4's run 1effc543d83a459a and Tier 2 learned it again on 2efa19065ce6476e, so both
    now come through here.
    """
    path = scratch / name
    path.unlink(missing_ok=True)
    return path


def _sandbox_run_dir(scratch: Path, run_id: str) -> Path:
    """The reviewer's writable ground for **one run**, inside the per-project mount.

    Everything the sandbox must read or write lives here, and the reason is the same one
    `_sandbox_out` gives for findings: the scratch is the reviewer's only writable mount,
    and it is the only part of the host filesystem it can see at all. `state/runs/<run>/`
    — where the review's evidence belongs and where `collect` reads its plan — is **not a
    workspace of the review sandbox**, so a prompt written there cannot be read and an
    events file pointed there cannot be written.

    The mount is fixed per project (§9.1, and `_review_scratch` explains why it cannot be
    per run); a subdirectory under it is free, exactly as the clone mount takes a run-id
    subdirectory for the same reason. Keyed by run id so two runs of one ticket cannot
    collide — the defect `factory_dir_for` records.
    """
    path = scratch / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _land(scratch_out: Path, out_path: Path) -> None:
    """Move one of the reviewer's outputs from the shared scratch into this run's own
    directory, where the evidence belongs. Findings, the event stream and stderr all come
    home this way; a missing file is silent, because an axis that never started has
    nothing to land and `_validated_findings` is what judges that."""
    if scratch_out.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(scratch_out), out_path)


def _review_scratch(ctx: Context) -> Path:
    """The reviewer's writable ground: one directory per **project**, not per run.

    §9.1 fixes a sandbox's workspace set at creation, and the reviewer sandbox is named
    once per project, so every path in its spec has to outlive the run that first created
    it. A per-run directory does not: the second run finds the sandbox mounted on the
    first run's path and `_assert_spec_matches` refuses it, correctly. BAC-4 measured
    exactly that. The axis outputs are moved into the run's own directory as soon as they
    land, so the evidence is still per-run — only the mount is shared.
    """
    scratch = ctx.home / "state" / "review" / ctx.project.name
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


def _review_spec(ctx: Context, scratch: Path) -> SandboxSpec:
    """The read-only review sandbox: the project `:ro` + a `rw` scratch for findings.

    The read-only mount is the **project root**, not the worktree, for the same reason the
    scratch is per-project: a worktree path contains the ticket, and a spec that changes
    per ticket cannot be satisfied by a sandbox named per project. The build sandbox has
    always mounted the root for this reason — worktrees live inside it at
    `.factory/worktrees/<TICKET>`, so mounting the root reaches every one of them, and
    `exec_sync(workdir=...)` still puts the reviewer in the worktree it is reviewing.

    `--no-share-skills` (§19 Phase 3 checklist): the reviewer has no skills store, so the
    portable `full-review` skill is reached by inlining it (Tier 2), not by loading a shared
    store a build sandbox could have polluted (R5).
    """
    return SandboxSpec(
        project=ctx.project.name,
        role="review",
        name=ctx.project.review_sandbox,
        # The rw scratch is *first*, and that is not a style choice: `sbx create` refuses
        # a read-only primary workspace outright — "primary workspace must be read/write
        # (remove ':ro' or ':readonly')" — and its own help says `:ro` applies to the
        # additional workspaces. So the reviewer's writable ground is the scratch it writes
        # findings to, and the code under review comes in beside it, read-only, at the
        # identical path. The boundary §15.2 asks for is unchanged; only the order is.
        workspaces=(
            Workspace(scratch),
            Workspace(ctx.project.path, readonly=True),
        ),
        template=ctx.project.template or None,
        kits=(),
        static_mcp=(),
        deny_network=ctx.registry.defaults.deny_network,
        env={},
        share_skills=False,
    )


def _axis_prompt(worktree: Path, harness: HarnessConfig, agent: str, base_ref: str) -> str:
    """Assemble one axis's prompt the way `full-review.js`'s `axisPrompt()` does.

    Frame = a stack override at `<review.agentDir>/<agent>.md`, else the shared vendored
    frame at `.agents/vendor/harness/agents/<agent>.md`. Checklist = `<review.checklistDir>/
    <agent>.md`, optional. If both resolve to nothing, the factory throws — matching layer A's
    own refusal to review on a one-line brief, because a silent "no findings" from an axis
    that never ran is the failure this system exists to prevent.
    """
    own = _body(worktree / harness.review_agent_dir / f"{agent}.md")
    frame = own or _body(worktree / ".agents/vendor/harness/agents" / f"{agent}.md")
    checklist = _body(worktree / harness.review_checklist_dir / f"{agent}.md")
    if not frame and not checklist:
        raise Blocked(
            "review-frame-missing",
            f"the {agent} axis resolved to nothing. Looked in "
            f"{harness.review_agent_dir}/{agent}.md, .agents/vendor/harness/agents/{agent}.md "
            f"and {harness.review_checklist_dir}/{agent}.md. Layer A is delivered as a plugin "
            "or as a vendored tree; one is missing.",
        )
    if not checklist:
        # spec-checker carries its whole review in the frame; no checklist
        return frame + _target(base_ref)
    return (
        frame + f"\n\n---\n\n## {harness.name}: what to look for, in this repo's terms\n\n"
        "This is the checklist the frame above told you to read. It is reproduced here so "
        "you have it without a file read; it is authoritative for this repository, and where "
        "it names a source file, that source outranks it.\n\n" + checklist + _target(base_ref)
    )


def _target(base_ref: str) -> str:
    """Which diff this axis reviews.

    The factory holds no review prompt — the frame and the checklist above are read from
    the vendored layer-A tree and the repository, and this adds no criterion to either.
    It names the target, which is the factory's own business: it decides what runs and
    against what. `full-review.js` appends the same two facts for the same reason
    (`Review the diff: git diff ${BASE}...HEAD`), because a reviewer given a frame and no
    target reviews whatever it happens to open, and one not told that silence is a valid
    answer invents findings to look thorough.
    """
    return (
        f"\n\n---\n\nReview the diff: `git diff {base_ref}...HEAD`\n\n"
        "Report ONLY real defects in this diff. An empty findings list is a valid and "
        "common result — do not manufacture findings to look thorough."
    )


def _body(path: Path) -> str:
    """Body of a Markdown definition with YAML frontmatter stripped. Empty when absent."""
    if not path.exists():
        return ""
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            raw = raw[end + 4 :]
    return raw.strip()


def _validated_findings(
    ctx: Context, out_path: Path, stderr_path: Path, label: str
) -> list[dict[str, Any]]:
    """Read the `-o` findings file, validate against the layer-A schema, return the list.

    A missing or invalid file blocks rather than rounding to "no findings": an axis that
    produced nothing the schema recognises is not the same as an axis that found nothing.
    """
    if not out_path.exists():
        stderr_tail = _read_text(stderr_path)[:500]
        raise Blocked(
            "review-schema-invalid",
            f"the {label} review wrote no findings file at {out_path}. stderr={stderr_tail}",
        )
    try:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Blocked("review-schema-invalid", f"the {label} findings are not JSON: {exc}") from exc
    schema = json.loads((ctx.worktree / _FINDINGS_SCHEMA).read_text(encoding="utf-8"))
    try:
        validate_against_schema(payload, schema)
    except SchemaInvalid as exc:
        raise Blocked(
            "review-schema-invalid", f"the {label} findings failed schema validation: {exc}"
        ) from exc
    findings = payload.get("findings", [])
    if not isinstance(findings, list):
        raise Blocked("review-schema-invalid", f"the {label} findings `findings` is not a list")
    return [dict(f) for f in findings]


# --------------------------------------------------------------------------------
# Tier 2 — the full nine-axis fan-out, when warranted
# --------------------------------------------------------------------------------


def _tier2_trigger(ctx: Context, harness: HarnessConfig, tier1_has_human: bool) -> str | None:
    """The rule that fires Tier 2, or the skip rule name if it does not. All deterministic,
    all recorded. Returns None to mean "run Tier 2"; a string to mean "skip, name this rule."""
    worktree = ctx.worktree
    base_ref = ctx.run.base_ref or ctx.project.base_ref
    paths = repo.changed_paths(worktree, base_ref)
    lines = repo.changed_lines(worktree, base_ref)
    sensitive = _SENSITIVE_DIRS.get(ctx.project.stack, ())
    return _decide_tier2(
        paths=paths,
        lines=lines,
        protected_hits=harness.protected_hits(paths),
        sensitive=sensitive,
        tier1_has_human=tier1_has_human,
        bug_without_test=_bug_without_test(ctx),
        forced=ctx.run.full_review,
    )


def _decide_tier2(
    *,
    paths: list[str],
    lines: int,
    protected_hits: list[str],
    sensitive: tuple[str, ...],
    tier1_has_human: bool,
    bug_without_test: bool,
    forced: bool = False,
) -> str | None:
    """The pure Tier-2 decision (§15.2). None = run; a string = the skip rule to name.

    Separated from `_tier2_trigger` so the whole trigger table is asserted without a context,
    a worktree or git — the rules are the part that must not drift from the spec.

    `forced` is `factory run --full-review`, and it is checked first because it is an
    override rather than a seventh rule: it does not describe the diff, it says a human
    wants the fan-out on this run whatever the diff looks like. Four of the six real rules
    cannot be produced on demand — a protected path is refused by `protect_paths.mjs`
    before it can reach a diff, the sensitive-directory globs match nothing in either
    repository today, a Tier-1 critical is not orderable, and the Bug-without-test row is
    blocked earlier by the red-phase replay — so without an override the fan-out is a code
    path reachable only by getting lucky with a diff size, and a path like that stays
    unproven.

    Note: the two-phase `start` calls this with `tier1_has_human=False` because Tier-1 has
    not run yet at spawn time; see the module header. The rule stays here for the
    table-driven test.
    """
    if forced:
        return None
    if len(paths) >= _TIER2_FILE_THRESHOLD:
        return None
    if lines >= _TIER2_LINE_THRESHOLD:
        return None
    if protected_hits:
        return None
    if sensitive and any(_matches_any(path, sensitive) for path in paths):
        return None
    if tier1_has_human:
        return None
    if bug_without_test:
        return None
    return "no-trigger"


def _bug_without_test(ctx: Context) -> bool:
    """The ticket carries a `Bug` label and the fix added no test."""
    if ctx.issue is None:
        return False
    labels = {label.lower() for label in ctx.issue.labels}
    if "bug" not in labels:
        return False
    attempt_dir = ctx.factory_dir / "run" / str(ctx.run.attempt)
    if not attempt_dir.exists():
        return True
    try:
        payload = json.loads((attempt_dir / "last-message.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    return not payload.get("tests_added")


def _tier2_prompt(ctx: Context, base_ref: str) -> str:
    """The portable `full-review` skill prompt, inlined rather than invoked.

    Matching the implement step's pattern: a Codex skill's availability in the catalog is
    not guaranteed in an unattended sandbox, and inlining the body keeps the review
    independent of skill-store plumbing.
    """
    skill = ctx.worktree / ".agents/vendor/harness/skills/full-review/SKILL.md"
    if not skill.exists():
        raise Blocked(
            "review-skill-missing", f"the portable full-review skill is absent at {skill}"
        )
    body = _body(skill)
    return (
        f"{body}\n\n---\n\nRun the full review now, against `{base_ref}...HEAD` in this "
        f"worktree. Report defects only, ranked most severe first, each with file:line and a "
        "one-sentence failure scenario. Emit the findings as the JSON schema this run was "
        f"given (`{_FINDINGS_SCHEMA}`). Do not push fixes; the sandbox is read-only.\n"
    )


# --------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    normalised = path.replace("\\", "/").removeprefix("./")
    for pattern in patterns:
        if fnmatch.fnmatchcase(normalised, pattern):
            return True
        if pattern.endswith("/**") and normalised.startswith(pattern[:-2]):
            return True
    return False


def _write_summary(
    ctx: Context, review_dir: Path, findings: list[dict[str, Any]], tier2_rule: str
) -> None:
    """Stash the review summary for the PR body (§13.2)."""
    review_dir.mkdir(parents=True, exist_ok=True)
    artifacts.write_json(
        review_dir / "review-summary.json",
        {
            "tier2": tier2_rule,
            "findings": [
                {
                    "severity": f.get("severity"),
                    "file": f.get("file"),
                    "line": f.get("line"),
                    "summary": f.get("summary"),
                }
                for f in findings
            ],
        },
    )


def _awaiting_human(ctx: Context, reason: str, detail: str) -> None:
    """Route to `awaiting_human` and announce it (§13.1). No PR is opened."""
    advance(ctx, State.AWAITING_HUMAN, rule=reason, detail=detail[:2000])
    tokens_in, tokens_out, usd = ctx.store.spend(ctx.run.id)
    block_step.announce_awaiting_human(
        ctx,
        pr_url=None,
        sections=[
            f"**Reason:** {reason}\n{detail[:1500]}",
            f"**Review summary:** see `{ctx.state_dir / 'review' / 'review-summary.json'}`",
            f"**Cost:** in {tokens_in}, out {tokens_out} "
            f"({f'${usd:.2f}' if usd is not None else 'token counts only'})",
        ],
    )
