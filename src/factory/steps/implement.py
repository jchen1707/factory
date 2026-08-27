"""`worktree_ready|planning -> implementing -> verifying` — the detached agent run.

This is where the plan's crash-safety claim is either true or not. The host writes
three files, spawns a detached process inside the sandbox, and then only *watches* the
filesystem. If the control plane dies at any moment, the next tick reads SQLite, finds
the attempt, and learns what happened by looking at `heartbeat`, `exit` and
`events.jsonl`.

The prompt inlines `implement`'s SKILL.md rather than naming it. P0-15 measured why:
Codex treats `policy: allow_implicit_invocation: false` as *remove from the catalog*,
not *the model may not invoke it*, so no prompt reaches `/implement` — not by name, not
as a slash command, which `codex exec` does not expand anyway. The other six skills are
reachable and are named normally.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

from factory import artifacts, policy
from factory.agent.base import (
    AgentInvocation,
    SchemaInvalid,
    Transcript,
    validate_against_schema,
)
from factory.agent.codex import TranscriptError
from factory.artifacts import AttemptDir
from factory.machine import AUTOMATIC, Blocked, Resumable, State
from factory.sandbox.base import RunHandle
from factory.sandbox.sbx import exec_argv
from factory.steps import KILL_TARGET, Context, advance

__all__ = ["build_prompt", "collect", "run", "start"]

STEP = "implement"

#: The skill the workflow names, and the one Codex cannot be asked for. Read at run
#: time and hashed into the evidence, so the record names the exact text that drove the
#: run rather than a skill name that could mean anything.
IMPLEMENT_SKILL = Path.home() / ".agents" / "skills" / "implement" / "SKILL.md"

POLL_INTERVAL_SECONDS = 10


def run(ctx: Context) -> None:
    """Start the attempt and stay with it until it ends — `factory run`'s path.

    `factory tick` uses `start` and `collect` separately instead, because a tick that
    blocked for the length of a model run would be a tick that could not reap anything
    else. The two paths share every line that matters: this one is `start`, a poll loop,
    and `collect`.
    """
    started = start(ctx)
    if started is None:
        return
    attempt_dir, handle = started
    _await_exit(ctx, attempt_dir, handle)
    collect(ctx, attempt_dir, handle.attempt)


def start(
    ctx: Context,
    *,
    resume_session: str | None = None,
    continuation: str | None = None,
    actor: str = AUTOMATIC,
) -> tuple[AttemptDir, RunHandle] | None:
    """Write the attempt's files and spawn the detached agent. Returns once it is running.

    `None` means a dry run, which walks the states and executes nothing.

    `resume_session` is §16.3's resume branch: the same worktree, the same attempt
    directory shape, and `codex exec resume <id>` instead of a fresh `codex exec`. Never
    `--last` — on a machine running several tickets that picks a session at random.
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
    worktree = ctx.worktree
    attempt_dir = AttemptDir.create(ctx.factory_dir, attempt)
    role = ctx.routing.role("builder")

    prompt, skill_sha = build_prompt(ctx, continuation=continuation)
    schema_source = _schema_path(ctx)

    invocation = AgentInvocation(
        model=role.model,
        effort=role.effort,
        workdir=str(worktree),
        prompt_path=attempt_dir.prompt,
        schema_path=attempt_dir.schema,
        output_path=attempt_dir.last_message,
        events_path=attempt_dir.events,
        stderr_path=attempt_dir.stderr,
        exit_path=attempt_dir.exit_file,
        heartbeat_path=attempt_dir.heartbeat,
        vault_directory=str(ctx.registry.vault.path),
        env=dict(ctx.project.env),
        resume_session=resume_session,
    )
    script = ctx.agent.wrapper_script(invocation)

    if ctx.dry_run:
        ctx.would(f"write {attempt_dir.prompt} ({len(prompt)} bytes)")
        ctx.would(f"write {attempt_dir.schema} (copy of {schema_source})")
        ctx.would(f"snapshot vault at {ctx.registry.vault.path}")
        # Built by the same function the real call uses, so the printed command cannot
        # drift from the executed one — which is the only thing that makes --dry-run
        # worth reading.
        preview = exec_argv(
            ctx.project.build_sandbox,
            ["/bin/sh", "-lc", "<the script below>"],
            workdir=str(worktree),
            env=dict(ctx.project.env),
            detach=True,
        )
        ctx.would(" ".join(preview))
        for line in script.strip().splitlines():
            ctx.would(f"    {line}")
        if ctx.state is not State.IMPLEMENTING:
            advance(ctx, State.IMPLEMENTING, actor=actor)
        ctx.would("poll heartbeat/exit; validate last-message.json; diff the vault")
        advance(ctx, State.VERIFYING)
        return None

    attempt_dir.prompt.write_text(prompt, encoding="utf-8")
    shutil.copyfile(schema_source, attempt_dir.schema)
    artifacts.write_json(
        attempt_dir.request,
        {
            "schemaVersion": 1,
            "run_id": ctx.run.id,
            "ticket": ctx.run.linear_id,
            "attempt": attempt,
            "role": role.name,
            "model": role.model,
            "effort": role.effort,
            "argv": list(ctx.agent.command(invocation)),
            "implement_skill_sha256": skill_sha,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "schema_sha256": artifacts.sha256_of(attempt_dir.schema),
            "started_at": int(time.time()),
        },
    )

    _write_vault_snapshot(ctx, attempt_dir)

    ctx.store.start_attempt(
        ctx.run.id,
        attempt,
        State.IMPLEMENTING,
        sandbox=ctx.project.build_sandbox,
        artifact_dir=str(attempt_dir.root),
    )
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
    ctx.sandbox.exec_detached(handle, script, dict(ctx.project.env))
    ctx.log(
        "implement.started",
        sandbox=handle.sandbox,
        model=role.model,
        effort=role.effort,
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


def _await_exit(ctx: Context, attempt_dir: AttemptDir, handle: RunHandle) -> None:
    """Watch three files. A timeout is never silently a success.

    On timeout the agent is killed and the wrapper's `exit` file is *waited for*, so
    the attempt ends with a real terminal record rather than a truncated one.
    """
    timeout = ctx.timeout_for(State.IMPLEMENTING)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if attempt_dir.exit_file.exists():
            return
        ctx.store.renew_lease(ctx.run.id, ttl_seconds=900)
        capture_session_id(ctx, attempt_dir, handle.attempt)
        time.sleep(POLL_INTERVAL_SECONDS)

    ctx.log("implement.timeout", level="warning", seconds=timeout)
    ctx.sandbox.kill_agent(handle.sandbox, KILL_TARGET[State.IMPLEMENTING])
    for _ in range(12):
        if attempt_dir.exit_file.exists():
            break
        time.sleep(5)
    raise Resumable("implement-timeout", f"no exit file after {timeout}s; the agent was signalled")


def capture_session_id(
    ctx: Context, attempt_dir: AttemptDir, attempt: int, state: State = State.IMPLEMENTING
) -> None:
    """Store the session id before the run is considered started (§16.3).

    Parsed from `thread.started`, which P0-7 confirmed carries `thread_id` and nothing
    else. Never `codex exec resume --last`: with several tickets on one machine that
    picks a session at random.

    Called from two places, and the second one is why this is not private: the
    foreground watch loop below, and `reap` on every tick that finds the attempt still
    running. Under the daemon the foreground loop does not exist, so without the reap
    call the column stays NULL for the whole of `implementing` — and `recovery` reads
    it, finds nothing, and restarts a session it could have resumed.
    """
    if ctx.store.session_id(ctx.run.id, attempt, state):
        return
    if not attempt_dir.events.exists():
        return
    first = attempt_dir.events.read_text(errors="replace").splitlines()[:1]
    if not first:
        return
    try:
        event = json.loads(first[0])
    except json.JSONDecodeError:
        return
    if event.get("type") == "thread.started" and event.get("thread_id"):
        ctx.store.set_session_id(ctx.run.id, attempt, state, str(event["thread_id"]))


def collect(ctx: Context, attempt_dir: AttemptDir, attempt: int) -> None:
    """Read the attempt back off the filesystem and decide what it proved.

    Called either by `run` moments after the agent exits, or by `steps/reap.py` on a
    later tick — possibly in a different process, after a reboot. Nothing here reads
    anything the starting call held in memory, which is the property that makes the
    second case work: the vault snapshot, the session id and the exit code are all on
    disk, in the attempt directory, under the same absolute path on both sides.
    """
    exit_code = attempt_dir.exit_code()
    vault_before = _read_vault_snapshot(ctx, attempt_dir)
    # A crash between `finish_attempt` and `advance` leaves the run at `implementing`
    # with the attempt already recorded, and the next tick re-enters here. The verdict
    # is re-derived — it is a pure function of files that have not changed — but the
    # append-only tables are not written twice, because two cost rows for one model
    # call is a spend report that is quietly wrong.
    recorded = _already_recorded(ctx, attempt)
    try:
        transcript = ctx.agent.read_transcript(attempt_dir.events, attempt_dir.stderr)
    except TranscriptError as exc:
        ctx.store.finish_attempt(
            ctx.run.id, attempt, State.IMPLEMENTING, exit_code=exit_code, outcome="truncated"
        )
        raise Resumable("transcript-truncated", str(exc)) from exc

    if transcript.session_id:
        ctx.store.set_session_id(ctx.run.id, attempt, State.IMPLEMENTING, transcript.session_id)

    if not recorded:
        _record_evidence(ctx, attempt_dir, attempt, transcript)

    _check_vault(ctx, attempt, vault_before)

    if transcript.failed or exit_code not in (0,):
        ctx.store.finish_attempt(
            ctx.run.id, attempt, State.IMPLEMENTING, exit_code=exit_code, outcome="failed"
        )
        tail = artifacts.tail_lines(attempt_dir.stderr, 40)
        raise Resumable(
            "agent-failed",
            f"exit {exit_code}; {transcript.failure or 'no turn.failed event'}\n{tail}",
        )

    result = _validated_result(ctx, attempt_dir, attempt, recorded=recorded)
    artifacts.write_manifest(attempt_dir.root, produced_by=str(State.IMPLEMENTING))
    ctx.store.finish_attempt(
        ctx.run.id,
        attempt,
        State.IMPLEMENTING,
        exit_code=exit_code,
        outcome=str(result.get("status")),
        session_id=transcript.session_id,
    )

    if result.get("status") == "blocked":
        raise Blocked("agent-blocked", str(result.get("blocked_reason") or "no reason given"))

    ctx.log(
        "implement.finished",
        status=result.get("status"),
        files=len(result.get("files_changed", [])),
        tests=len(result.get("tests_added", [])),
        behaviour_changed=result.get("behaviour_changed"),
        tokens_in=transcript.usage.input_tokens,
        tokens_out=transcript.usage.output_tokens,
    )
    advance(ctx, State.VERIFYING)


def _already_recorded(ctx: Context, attempt: int) -> bool:
    row = ctx.store.attempt_row(ctx.run.id, attempt, State.IMPLEMENTING)
    return row is not None and row["ended_at"] is not None


def _record_evidence(
    ctx: Context, attempt_dir: AttemptDir, attempt: int, transcript: Transcript
) -> None:
    ctx.store.record_cost(
        ctx.run.id,
        attempt,
        STEP,
        model=ctx.routing.role("builder").model,
        input_tokens=transcript.usage.input_tokens,
        output_tokens=transcript.usage.output_tokens,
        cached_tokens=transcript.usage.cached_input_tokens,
        # No price row covers an OpenAI model yet, and a missing price is recorded as
        # unknown rather than as free (§18.3).
        usd=None,
    )

    if transcript.hook_denials:
        # Not a failure on its own — a refused write is enforcement working — but it
        # never reaches the JSON stream, so without this the evidence would show a
        # clean run for a turn that was blocked.
        ctx.store.record_check(
            ctx.run.id,
            attempt,
            "hook_denials",
            "fail",
            detail="\n".join(transcript.hook_denials)[:2000],
        )
        ctx.log("implement.hook-denied", level="warning", count=len(transcript.hook_denials))


def _validated_result(
    ctx: Context, attempt_dir: AttemptDir, attempt: int, *, recorded: bool = False
) -> dict[str, Any]:
    """Schema-valid or the state does not advance. F6 — the raw file is kept either way."""
    if not attempt_dir.last_message.exists():
        raise Blocked("schema-invalid", "the agent wrote no last-message.json")
    try:
        payload = json.loads(attempt_dir.last_message.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Blocked("schema-invalid", f"last-message.json is not JSON: {exc}") from exc

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
    not the repo — so the factory takes a snapshot either side and blocks on any write
    outside the two locations layer A's own hooks own.
    """
    after = policy.snapshot_vault(
        ctx.registry.vault.path, exclude=ctx.registry.vault.snapshot_exclude
    )
    changes = policy.diff_vault(before, after)
    offending = policy.vault_writes_outside_allowlist(changes, ctx.registry.vault.write_allowlist)

    snapshot_dir = ctx.state_dir / "vault"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    artifacts.write_json(snapshot_dir / f"before-{attempt}.json", before)
    artifacts.write_json(snapshot_dir / f"after-{attempt}.json", after)

    ctx.store.record_check(
        ctx.run.id,
        attempt,
        "vault_snapshot",
        "fail" if offending else "pass",
        detail=json.dumps([{"path": c.path, "kind": c.kind} for c in changes])[:2000],
        artifact=str(snapshot_dir),
    )
    if offending:
        raise Blocked(
            "vault-write-outside-allowlist",
            "the run changed vault files it does not own: "
            + ", ".join(f"{c.kind} {c.path}" for c in offending),
        )


def build_prompt(ctx: Context, *, continuation: str | None = None) -> tuple[str, str]:
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

    sections = [
        f"# {ctx.issue.identifier} — {ctx.issue.title}",
        "",
        "You are implementing one approved ticket in a worktree that already exists, on a",
        f"branch that already exists (`{ctx.branch}`), based on `{ctx.project.base_ref}`.",
        "",
        "## The workflow you are running",
        "",
        "The text below is the `implement` skill, inlined verbatim. It is inlined rather",
        "than invoked because Codex removes a skill marked `allow_implicit_invocation:",
        "false` from the catalog entirely, so no prompt can reach it by name. Follow it as",
        "though it had been invoked.",
        "",
        '<skill name="implement">',
        skill_body.strip(),
        "</skill>",
        "",
        "`tdd`, `code-review`, `codebase-design`, `diagnosing-bugs`, `research` and",
        "`resolving-merge-conflicts` **are** in your catalog and can be used by name. Use",
        "`tdd` for anything that changes behaviour.",
        "",
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
