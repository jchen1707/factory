"""`worktree_ready|planning -> implementing -> verifying` — the detached agent run.

This is where the plan's crash-safety claim is either true or not. The host writes
three files, spawns a detached process inside the sandbox, and then only *watches* the
filesystem. If the control plane dies at any moment, the next tick reads SQLite, finds
the attempt, and learns what happened by looking at `heartbeat`, `exit` and
`events.jsonl`.

The prompt inlines `implement`'s SKILL.md rather than naming it: the only plugin a launch
loads is the declared doctrine (`doctrine.py`), which does not carry it, so the only text
the builder can follow is the text the prompt carries, and the record hashes exactly that.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

from factory import (
    accounting,
    agent_run,
    artifacts,
    authority,
    candidate_handoff,
    doctrine,
    execution,
    policy,
    workflow_launches,
)
from factory.agent import claude, stream
from factory.agent.base import SchemaInvalid, validate_against_schema
from factory.agent.claude import StructuredOutputMissing
from factory.agent.stream import SessionId
from factory.artifacts import AttemptDir
from factory.machine import AUTOMATIC, Blocked, State
from factory.registry import IMPLEMENT_SKILL, PLUGIN_CACHE
from factory.sandbox.base import RunHandle
from factory.steps import Context, advance, attempt_files

__all__ = ["IMPLEMENT_SKILL", "PLUGIN_CACHE", "build_prompt", "collect", "start"]

STEP = "implement"


def start(
    ctx: Context,
    *,
    resume_session: str | None = None,
    continuation: str | None = None,
    actor: str = AUTOMATIC,
) -> tuple[AttemptDir, RunHandle] | None:
    """Write the attempt's files and spawn the detached agent. Returns once it is running.

    `None` means a dry run, which walks the states and executes nothing.

    `resume_session` is §16.3's resume branch: `--resume <id>` into the session the
    previous attempt pinned, in a new attempt directory. The caller found the id through
    `agent_run.resumable_session`, so its stream holds an `init` event.
    `continuation` is the ladder's rung-2 addition: what the previous attempt already
    changed and how it failed, which is the only thing that makes a second attempt
    different from the first.

    `actor` threads through the `advance` into `implementing`. The hop is automatic for the
    two callers that start an agent mid-pipeline — the tick's forward dispatch and the
    `resumable` recovery — but `SUSPENDED → implementing` is `resume-is-james` and `BLOCKED →
    implementing` is `unblock-is-a-judgement`, so a human `factory resume` passes `"human"`
    here or the advance refuses with `requires-human`.
    """
    # A rewind is one attempt with two phases, so the implement half of an attempt that
    # already planned reuses that attempt's number and its directory. Anywhere else this
    # is a new attempt.
    attempt = ctx.run.attempt if ctx.state is State.PLANNING else ctx.run.attempt + 1
    execution.guard(ctx, attempt, STEP, invocation_role=STEP)
    worktree = ctx.worktree
    attempt_dir = AttemptDir.create(ctx.factory_dir, attempt)
    role = execution.role_for(ctx, "builder")

    declared = _declared(ctx)
    prompt, skill_sha = build_prompt(ctx, continuation=continuation, declared=declared)
    schema_source = _schema_path(ctx)
    session = SessionId(resume_session) if resume_session else claude.new_session()

    attempt_dir.prompt.write_text(prompt, encoding="utf-8")
    shutil.copyfile(schema_source, attempt_dir.schema)
    invocation = agent_run.invocation(
        ctx,
        role=claude.Role.BUILDER,
        routed=role,
        files=attempt_files(State.IMPLEMENTING, attempt_dir.root),
        schema=json.loads(attempt_dir.schema.read_text(encoding="utf-8")),
        session=session,
        resume=resume_session is not None,
        plugins=_doctrine_plugin(declared, ctx.home),
    )
    artifacts.write_json(
        attempt_dir.request,
        {
            "schemaVersion": 2,
            "run_id": ctx.run.id,
            "ticket": ctx.run.linear_id,
            "attempt": attempt,
            "role": role.name,
            "model": role.model,
            "effort": role.effort,
            "argv": claude.argv(invocation),
            "implement_skill_sha256": skill_sha,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "schema_sha256": artifacts.sha256_of(attempt_dir.schema),
            "started_at": int(time.time()),
        },
    )

    _write_vault_snapshot(ctx, attempt_dir)

    with workflow_launches.preparation(ctx):
        ctx.store.start_attempt(
            ctx.run.id,
            attempt,
            State.IMPLEMENTING,
            sandbox=ctx.project.build_sandbox,
            artifact_dir=str(attempt_dir.root),
        )
        # Pinned before the launch, in the transaction that records it: a crash at any
        # later point leaves a row that names the session the frozen script will open.
        ctx.store.set_session_id(ctx.run.id, attempt, State.IMPLEMENTING, session)
        ctx.refresh()
        # The hop into `implementing` is recorded once, by whoever put the run here. From
        # `worktree_ready`/`planning`/`suspended`/`blocked`/`resumable` that is this `start`; from
        # a verify-fail loop-back it was `verify` (`verifying -> implementing`), and re-recording
        # `implementing -> implementing` is an illegal transition that blocked FRO-6's resume.
        # `start` begins a fresh attempt against the existing worktree either way — the attempt
        # counter (above) is what marks the new attempt, not the state hop.
        if ctx.state is not State.IMPLEMENTING:
            advance(ctx, State.IMPLEMENTING, actor=actor)

        handle = RunHandle(
            run_id=ctx.run.id,
            attempt=attempt,
            sandbox=ctx.project.build_sandbox,
            workdir=str(worktree),
            attempt_dir=attempt_dir.root,
        )
        identifier = accounting.begin(
            ctx,
            attempt,
            role,
            STEP,
            attempt_dir.events,
            extra_metadata={
                "expected": agent_run.expected_json(invocation),
                "resume": resume_session is not None,
            },
        )
        inputs: tuple[Path, ...] = (attempt_dir.prompt, attempt_dir.schema, attempt_dir.request)
        workflow_launches.prepare(ctx, identifier, handle, claude.script(invocation), inputs=inputs)
    workflow_launches.resume(ctx)
    ctx.log(
        "implement.started",
        sandbox=handle.sandbox,
        model=role.model,
        effort=role.effort,
        session=session,
        resumed=bool(resume_session),
    )
    return attempt_dir, handle


def _schema_path(ctx: Context) -> Path:
    return ctx.home / "schemas" / "implement_result.schema.json"


#: The before-half of the §8.5 vault check, written to disk rather than held in a local.
#: `start` and `collect` can be two different processes on two different days, so a
#: snapshot that lived only in memory would make the check silently unavailable on
#: exactly the runs recovery exists for.
VAULT_SNAPSHOT = "vault-before.json"


def _write_vault_snapshot(ctx: Context, attempt_dir: AttemptDir) -> None:
    snapshot = policy.snapshot_vault(
        ctx.registry.vault.path, exclude=ctx.registry.vault.snapshot_exclude
    )
    artifacts.write_json(
        attempt_dir.path(VAULT_SNAPSHOT),
        {"schemaVersion": 1, "root": str(ctx.registry.vault.path), "files": snapshot},
    )


def _read_vault_snapshot(ctx: Context, attempt_dir: AttemptDir) -> dict[str, tuple[int, int, str]]:
    """The snapshot `start` wrote. Its absence blocks rather than defaulting to empty.

    An empty `before` would make every file in the vault look newly added, so the
    allowlist check would fire on a run that touched nothing — and a check that fails
    for the wrong reason teaches people to ignore it. Missing means the attempt
    directory is not the one `start` wrote, which is a fact worth stopping on.
    """
    path = attempt_dir.path(VAULT_SNAPSHOT)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Blocked("vault-snapshot-missing", f"{path}: {exc}") from exc
    return {
        name: (int(meta[0]), int(meta[1]), str(meta[2]))
        for name, meta in dict(payload.get("files", {})).items()
    }


def collect(ctx: Context, attempt_dir: AttemptDir, attempt: int) -> None:
    """Read the attempt back off the filesystem and decide what it proved.

    Called either by `run` moments after the agent exits, or by `steps/reap.py` on a
    later tick — possibly in a different process, after a reboot. Nothing here reads
    anything the starting call held in memory, which is the property that makes the
    second case work: the vault snapshot, the session id and the exit code are all on
    disk, in the attempt directory, under the same absolute path on both sides.
    """
    from factory import learning

    learning.collect(ctx, attempt_dir.events)
    exit_code = attempt_dir.exit_code()
    accounting.collect(ctx, attempt, STEP, attempt_dir.events)
    vault_before = _read_vault_snapshot(ctx, attempt_dir)
    # A crash between `finish_attempt` and `advance` leaves the run at `implementing`
    # with the attempt already recorded, and the next tick re-enters here. The verdict
    # is re-derived — it is a pure function of files that have not changed — but the
    # append-only tables are not written twice, because two cost rows for one model
    # call is a spend report that is quietly wrong.
    recorded = _already_recorded(ctx, attempt)
    _check_vault(ctx, attempt, vault_before)
    run = agent_run.conclude(
        ctx,
        attempt=attempt,
        state=State.IMPLEMENTING,
        invocation_id=accounting.key(ctx, attempt, STEP),
        files=attempt_files(State.IMPLEMENTING, attempt_dir.root),
        record=not recorded,
    )

    result = _validated_result(ctx, run, attempt_dir, attempt, recorded=recorded)
    artifacts.write_manifest(attempt_dir.root, produced_by=str(State.IMPLEMENTING))
    ctx.store.finish_attempt(
        ctx.run.id,
        attempt,
        State.IMPLEMENTING,
        exit_code=exit_code,
        outcome=str(result.get("status")),
    )

    if result.get("status") == "blocked":
        raise Blocked("agent-blocked", str(result.get("blocked_reason") or "no reason given"))

    ctx.log(
        "implement.finished",
        status=result.get("status"),
        files=len(result.get("files_changed", [])),
        tests=len(result.get("tests_added", [])),
        behaviour_changed=result.get("behaviour_changed"),
        notional_usd=run.notional_usd,
        context_tokens=run.context_tokens,
    )
    advance(ctx, State.VERIFYING)


def _already_recorded(ctx: Context, attempt: int) -> bool:
    row = ctx.store.attempt_row(ctx.run.id, attempt, State.IMPLEMENTING)
    return row is not None and row["ended_at"] is not None


def _validated_result(
    ctx: Context, run: stream.Run, attempt_dir: AttemptDir, attempt: int, *, recorded: bool = False
) -> dict[str, Any]:
    """Schema-valid or the state does not advance. F6 — the raw file is kept either way.

    A schema the model cannot satisfy ends `success` with `structured_output: null`; the
    first check is that case.
    """
    try:
        payload = claude.materialize_final(run, attempt_dir.last_message)
    except StructuredOutputMissing as exc:
        raise Blocked("schema-invalid", "the agent returned no structured output") from exc

    schema = json.loads(_schema_path(ctx).read_text(encoding="utf-8"))
    try:
        validate_against_schema(payload, schema)
    except SchemaInvalid as exc:
        if not recorded:
            ctx.store.record_check(
                ctx.run.id, attempt, "implement_result_schema", "fail", detail=str(exc)
            )
        raise Blocked("schema-invalid", str(exc)) from exc

    if not recorded:
        ctx.store.record_check(ctx.run.id, attempt, "implement_result_schema", "pass")
    return dict(payload)


def _check_vault(ctx: Context, attempt: int, before: dict[str, tuple[int, int, str]]) -> None:
    """The §8.5 workaround: bound the blast radius by observation, not by permission.

    `protect_paths.mjs` cannot help here — its globs are repo-relative and the vault is
    not the repo — so the factory takes a snapshot either side of the attempt.

    What it does with the difference is now split, because a whole-vault diff attributes
    **by time** and time is not cause. The vault is a live Obsidian vault that James edits
    while runs are in flight, and with `concurrency_per_project` above 1 two attempts share
    a window and see each other's legitimate writes. Blocking on everything the diff showed
    could only ever have been right while exactly one run existed and nobody was typing.

    - Inside the allowlist the writer is known — layer A's distiller hook, which only adds
      or rewrites its own dated note — so a **deletion** there is the run's, and blocks.
      The hook's own capture-lock cleanup is the exception, recorded like the next case.
    - Outside it, nothing identifies the writer. Those changes are recorded as a `warn` and
      the attempt continues.

    BAC-10 attempt 1 is the case this was rewritten for: failed for
    `Getting Promoted/Daily takeaways/Raw notes.md`, a file the agent had itself listed
    under `out_of_scope` and never touched. A control that spends an attempt on that is not
    protecting anything; it is adding a random failure to every run long enough to overlap
    a human. The evidence is still written either way, which is the half worth keeping.
    """
    after = policy.snapshot_vault(
        ctx.registry.vault.path, exclude=ctx.registry.vault.snapshot_exclude
    )
    changes = policy.diff_vault(before, after)
    allowlist = ctx.registry.vault.write_allowlist
    blocking = policy.disallowed_vault_writes(changes, allowlist)
    unattributable = policy.unattributable_vault_changes(changes, allowlist)

    snapshot_dir = ctx.state_dir / "vault"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    artifacts.write_json(snapshot_dir / f"before-{attempt}.json", before)
    artifacts.write_json(snapshot_dir / f"after-{attempt}.json", after)

    if blocking:
        status = "fail"
    elif unattributable:
        status = "warn"
    else:
        status = "pass"
    ctx.store.record_check(
        ctx.run.id,
        attempt,
        "vault_snapshot",
        status,
        # The reason names which half fired, so a `warn` row is not read as a near-miss
        # of the blocking rule. They are different findings about different evidence.
        reason=(
            f"{len(unattributable)} change(s) outside the allowlist or transient-lock "
            "cleanup, attributed to nobody"
            if status == "warn"
            else None
        ),
        detail=json.dumps([{"path": c.path, "kind": c.kind} for c in changes])[:2000],
        artifact=str(snapshot_dir),
    )
    if unattributable:
        ctx.log(
            "vault.changed_outside_allowlist",
            level="warning",
            count=len(unattributable),
            paths=[c.path for c in unattributable][:20],
        )
    if blocking:
        raise Blocked(
            "vault-write-outside-allowlist",
            "the run deleted vault files it does not own: "
            + ", ".join(f"{c.kind} {c.path}" for c in blocking),
        )


def build_prompt(
    ctx: Context,
    *,
    continuation: str | None = None,
    declared: doctrine.Doctrine | None = None,
) -> tuple[str, str]:
    """`(prompt, sha256 of the inlined skill)`.

    The prompt is assembled from files the control plane already wrote, plus one skill
    body it inlines. Nothing here restates a gate command or a review rule: those live
    in `harness.config.json` and the vendored tree, and the agent reads them where they
    are.

    `continuation` is §16.3a rung 2: the failure evidence from the attempt that just
    died. It is appended rather than substituted, because the ticket, the spec and the
    boundaries are as true on the second attempt as on the first — what changes is that
    the model now knows what already failed.
    """
    if ctx.issue is None:
        raise Blocked("no-issue-loaded", ctx.run.linear_id)

    skill_body, skill_sha = _skill_text()
    worktree = ctx.worktree
    # Named absolutely, not as `.factory/context/…`. For a bind-mounted project the two
    # are the same string; for a `--clone` project the context sits on a separate `rw`
    # mount, because the clone carries no untracked files (`steps/clone.py`). A relative
    # path would silently name nothing there, and the agent would start with no ticket.
    context_dir = ctx.factory_dir / "context"

    skills = doctrine.prompt_sentence(declared or _declared(ctx))
    sections = [
        f"# {ctx.issue.identifier} — {ctx.issue.title}",
        "",
        "You are implementing one approved ticket in a worktree that already exists, on a",
        f"branch that already exists (`{ctx.branch}`), based on `{ctx.project.base_ref}`.",
        "",
        "## The workflow you are running",
        "",
        "The text below is the `implement` skill, inlined verbatim. It is inlined rather",
        "than invoked because no plugin this run loads carries it. Follow it as though it",
        "had been invoked.",
        "",
        '<skill name="implement">',
        skill_body.strip(),
        "</skill>",
        "",
        *([skills, ""] if skills else []),
        "## Context, already written for you",
        "",
        f"- `{context_dir}/ticket.md` — this ticket, in full",
        f"- `{context_dir}/spec.md` — the approved parent spec, verbatim",
        f"- `{context_dir}/breakdown.md` — the sibling tickets, so you can see the slice boundary",
        f"- `{context_dir}/comments.md` — the ticket's comment thread",
        "",
        "Read all four before you write anything. Work only inside",
        f"`{worktree}`.",
        "",
        "## The Definition of Done is declared, not described here",
        "",
        "`harness.config.json` at the repository root declares every gate. Run them from",
        "there; do not invent a command, and do not assume a gate that is not declared.",
        "The Stop hook enforces the same list, and this run is under it.",
        "",
        "## Boundaries",
        "",
        "- Commit to the current branch. **Do not push, do not open a pull request, and do",
        "  not merge.** The control plane pushes from the host; merging is a human's.",
        "- **Do not write to Linear.** The control plane owns every tracker write. If you",
        "  find a bug next to this ticket, put it in `out_of_scope` and keep going.",
        "- Do not edit anything under `.agents/vendor/` or `.factory/`, or any path the",
        "  repository marks protected. A refusal from the write guard is the guard working;",
        "  report it rather than working around it.",
        "- Stay inside this ticket. Work that belongs to a sibling is `out_of_scope`.",
        "",
        "## How to finish",
        "",
        "Your final message must be a JSON document matching the schema this run was given.",
        "Two fields decide what the control plane checks next, so answer them honestly:",
        "",
        "- `behaviour_changed` — true if the change alters what the software does. It is",
        "  required. A true value triggers a replay that applies only the test half of your",
        "  diff at the base ref and requires it to **fail**; a test that passes there proves",
        "  nothing about the new behaviour and blocks the run.",
        "- `seam_confirmed` — true if the seams you needed were already in place.",
        "",
        "`gates_run` is cross-checked against an independent gate report that runs each gate",
        "only on the files your change touched. List a gate there only when you ran it",
        "against files your change actually exercised — a gate you ran against unchanged code",
        "comes back `skipped_unchanged` in the report and claiming it blocks as",
        "`evidence-mismatch`, so for a change no gate covers the honest `gates_run` is an",
        "empty list. A gate name is the declared `name` from `harness.config.json` (e.g.",
        "`ruff check`, `pytest -m integration`); the claim may include the command and its",
        "outcome, because the report is matched by name and the verdict — not your text —",
        "decides pass or fail. An honest `gates_run` with a failure in it is a better",
        "outcome than an optimistic one.",
    ]
    snapshot_root = authority.current(ctx)
    if snapshot_root and (snapshot := ctx.store.runtime.policy(ctx.run.id)):
        sections.extend(
            [
                "",
                "## Selected delivery authority",
                "",
                f"Authority root: `{snapshot_root}`; profile: `{snapshot['profile']}`.",
                "Use this captured policy for the required scope and explicit deferrals.",
                "Candidate policy edits do not change this run's authority.",
                f"Effective policy: {json.dumps(snapshot['effective'])}",
            ]
        )
    handoff = ctx.factory_dir / "handoff.json"
    if handoff.exists():
        planning = ctx.store.attempt_row(ctx.run.id, ctx.run.attempt, State.PLANNING)
        planning_root = (
            Path(planning["artifact_dir"])
            if planning is not None
            else ctx.factory_dir / "run" / str(ctx.run.attempt)
        )
        collected = planning_root / "planning-output"
        sections.extend(
            [
                "",
                authority.contract(ctx, "consume-execution-handoff"),
                f"Execution handoff: {handoff}",
            ]
        )
        sections.extend(
            f"Collected handoff artifact: {path}"
            for path in sorted(collected.glob("*"))
            if path.is_file() and path.stat().st_size
        )
    sections.extend(candidate_handoff.prompt_section(ctx))
    if continuation:
        sections += [
            "",
            "## This is not the first attempt",
            "",
            "The worktree has **not** been reset, so whatever the previous attempt wrote is",
            "in front of you. Decide whether to keep, amend or revert it — do not start",
            "again from nothing, and do not assume it was wrong.",
            "",
            continuation.strip(),
        ]
    return "\n".join(sections) + "\n", skill_sha


def _declared(ctx: Context) -> doctrine.Doctrine:
    try:
        return doctrine.load(doctrine.config_path(ctx.home))
    except doctrine.DoctrineError as exc:
        raise Blocked("doctrine-invalid", str(exc)) from exc


def _doctrine_plugin(declared: doctrine.Doctrine, home: Path) -> tuple[claude.PluginRef, ...]:
    try:
        plugin = doctrine.build(declared, PLUGIN_CACHE, doctrine.root(home))
    except (doctrine.DoctrineError, OSError) as exc:
        raise Blocked("doctrine-invalid", str(exc)) from exc
    return (plugin,) if plugin else ()


def _skill_text() -> tuple[str, str]:
    """The `implement` skill body with its frontmatter stripped, and the file's sha256."""
    if not IMPLEMENT_SKILL.exists():
        raise Blocked(
            "implement-skill-missing",
            f"{IMPLEMENT_SKILL} does not exist. The execution set is installed by "
            "symlinking the pinned mattpocock cache into ~/.agents/skills (§24.11).",
        )
    raw = IMPLEMENT_SKILL.read_text(encoding="utf-8")
    sha = hashlib.sha256(raw.encode()).hexdigest()
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            raw = raw[end + 4 :]
    return raw, sha
