"""`factory doctor --deep`: the Claude unknowns only a real sandbox can settle.

One disposable `factory-doctor-<project>` sandbox over a fresh clone of the project at its
base ref (the tree a run's worktree is cut from), built the way the factory builds a build
sandbox (the project's template and kits), and six probes in it, each one row:

- `image`: `claude --version`, and `node`, `git`, `setsid` on the PATH (P1).
- `ping`: a haiku launch with the production argv and a one-field schema. Proves the
  credential, egress and `--json-schema`, and attests `init` against the launch.
- `protected-path refusal`: a builder launch told to edit the first protected path. Layer
  A's PreToolUse hook must refuse it and the file must be unchanged (P7).
- `effort`: the routed builder model at `--effort low`; the VM's native transcript must
  record `perTurnEffort: low` (P4).
- `pgid kill`: a detached launch running `sleep`, killed by its process group. `exit`
  must still be written and nothing may survive (P6).
- `resume after stop`: `sbx stop`, then `--resume` of the ping's session (P4b).

The verdicts are pure functions over what the probes read back, tested against host
captures in `tests/fixtures/claude/`. The orchestration around them has never run: it
needs `sbx login` and an Anthropic credential, which no machine had when it was written.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from factory import doctrine, repo
from factory.agent import claude, stream
from factory.agent.claude import AttemptFiles, Effort, Invocation, Role
from factory.agent.stream import Completed, Failed, FailureKind, Interrupted, Run, SessionId
from factory.doctor import Result, Status
from factory.harness import CLAUDE_SETTINGS, load_harness_config
from factory.registry import Project
from factory.repo import GitError
from factory.sandbox.base import RunHandle, SandboxAdapter, SandboxSpec, Workspace
from factory.sandbox.sbx import SbxError

__all__ = [
    "PROBES",
    "effort",
    "group_commands",
    "kill",
    "ping",
    "refusal",
    "resumed",
    "run",
    "sandbox_name",
    "sbx_unready",
]

PREFIX = "factory-doctor-"
MODEL = "claude-haiku-4-5-20251001"
SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
}
ANSWER = "Answer through the structured output with ok set to true.\n"
BUDGET_USD = 0.5
LAUNCH_SECONDS = 300
KILL_WAIT_SECONDS = 120
STOP_WAIT_SECONDS = 60
SLEEP = "sleep 600"

AUTH_ROUTES = (
    "the sandbox's claude has no working Anthropic credential. Give sandboxes one: "
    "(a) subscription OAuth, `sbx run claude <any dir> -- auth login` once, or (b) an API "
    'key, `echo "$ANTHROPIC_API_KEY" | sbx secret set anthropic`. docs/runbook.md has both.'
)


def sandbox_name(project: Project) -> str:
    return f"{PREFIX}{project.name}"


def sbx_unready(detail: str) -> str:
    """The operator's next step for an `sbx ls` that failed with `detail`."""
    first = next((line.strip() for line in detail.splitlines() if line.strip()), "")
    if "sbx login" in detail or "not authenticated to Docker" in detail:
        return (
            "sbx is not signed in to Docker (`sbx ls`: 401 Unauthorized). "
            "Run `sbx login`, then `factory doctor --deep` again."
        )
    if "No such file or directory" in detail:
        return "sbx is not installed or not on PATH; install Docker Sandboxes, then `sbx login`."
    if "daemon" in detail.lower():
        return f"the sandbox daemon is not reachable ({first}). Run `sbx daemon start`."
    return f"sbx is unusable: {first}. `sbx diagnose` says why."


# --------------------------------------------------------------------------------
# verdicts: pure, fixture-tested
# --------------------------------------------------------------------------------


def _violations(run: Run) -> str:
    return "; ".join(
        f"{v.what}: expected {v.expected!r}, observed {v.observed!r}" for v in run.violations
    )


def _failure(run: Run) -> str:
    match run.outcome:
        case Failed(kind=FailureKind.AUTH) | Interrupted(auth_failing=True):
            return AUTH_ROUTES
        case Failed(kind=kind, detail=detail):
            return f"{kind}: {detail}"
        case Interrupted(exit_code=code):
            return f"the stream ends before a result event; exit {code}"
        case Completed(structured_output=answer):
            return f"completed without ok=true: {answer!r}"


def _answered(run: Run) -> bool:
    match run.outcome:
        case Completed(structured_output=answer) if answer is not None:
            return answer.get("ok") is True
    return False


def ping(run: Run) -> Result:
    name = "live: ping"
    if run.violations:
        return Result(name, Status.FAIL, f"init disagrees with the launch: {_violations(run)}")
    if not _answered(run) or run.init is None:
        return Result(name, Status.FAIL, _failure(run))
    return Result(
        name,
        Status.OK,
        f"{run.init.model} answered through --json-schema; init attested "
        f"(session, model, tools, plugins, no MCP, permission mode {run.init.permission_mode})",
    )


def resumed(run: Run, session: SessionId) -> Result:
    name = "live: resume after stop"
    if run.session == session and _answered(run) and not run.violations:
        return Result(name, Status.OK, f"--resume {session} continued across sbx stop/start")
    if isinstance(run.outcome, Failed) and run.outcome.kind is FailureKind.SESSION_LOST:
        return Result(
            name,
            Status.FAIL,
            "the VM lost the session across sbx stop/start, so a resumable attempt restarts "
            f"instead of resuming: {run.outcome.detail}",
        )
    return Result(name, Status.FAIL, _violations(run) or _failure(run))


_FILE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})


def refusal(evs: Iterable[stream.Event], run: Run, target: str, *, changed: bool) -> Result:
    """Did layer A refuse an agent's write to `target`, a path relative to the cwd?

    Measured on the host (Claude Code 2.1.292): the block surfaces as an `is_error` tool
    result naming the hook, and the call is listed in `result.permission_denials`.
    """
    name = "live: protected-path refusal"
    if changed:
        return Result(name, Status.FAIL, f"the write to {target} landed: nothing refused it")
    attempts: set[str] = set()
    results: dict[str, stream.ToolResult] = {}
    for event in evs:
        if isinstance(event, stream.Message):
            for use in event.tool_uses:
                path = use.input.get("file_path")
                if (
                    use.name in _FILE_TOOLS
                    and isinstance(path, str)
                    and (path == target or path.endswith(f"/{target}"))
                ):
                    attempts.add(use.id)
        elif isinstance(event, stream.ToolResult):
            results[event.tool_use_id] = event
    if not attempts:
        return Result(
            name,
            Status.FAIL,
            f"inconclusive: the agent never tried to write {target}, so nothing was refused "
            f"({_failure(run) if not isinstance(run.outcome, Completed) else 'it completed'})",
        )
    denied = {denial.tool_use_id for denial in run.denials}
    for use_id in attempts:
        result = results.get(use_id)
        if result is not None and result.is_error and "protect_paths" in result.text:
            where = "an is_error tool result" + (
                " and result.permission_denials" if use_id in denied else ""
            )
            return Result(name, Status.OK, f"{_first_line(result.text)} (surfaced as {where})")
    answers = [results[use_id].text for use_id in attempts if use_id in results]
    return Result(
        name,
        Status.FAIL,
        f"protect_paths.mjs did not refuse the write to {target}; the tool said: "
        + (_first_line(answers[0]) if answers else "nothing"),
    )


def effort(transcript: str, requested: Effort) -> Result:
    """`perTurnEffort` on every assistant line of the native transcript, as measured on the
    host for a model that takes effort."""
    name = "live: effort"
    seen: list[str] = []
    for line in stream.lines(transcript):
        try:
            wire = json.loads(line)
        except ValueError:
            continue
        if isinstance(wire, dict) and wire.get("type") == "assistant":
            seen.append(str(wire.get("perTurnEffort")))
    if not seen:
        return Result(name, Status.FAIL, "no assistant line in the VM's native transcript")
    if set(seen) == {requested}:
        return Result(name, Status.OK, f"perTurnEffort {requested} on {len(seen)} turn(s)")
    return Result(
        name, Status.FAIL, f"asked for {requested}, transcript recorded {sorted(set(seen))}"
    )


def group_commands(ps_text: str, pgid: int) -> list[str]:
    """The `comm` of every process in group `pgid`, from `ps -eo pgid=,comm=`."""
    names: list[str] = []
    for line in ps_text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0] == str(pgid):
            names.append(parts[1].strip())
    return names


def kill(exit_text: str | None, survivors: str, group: Sequence[str]) -> Result:
    name = "live: pgid kill"
    if exit_text is None:
        return Result(name, Status.FAIL, "no exit file after the group kill: the wrapper died too")
    if survivors.strip():
        return Result(name, Status.FAIL, f"{SLEEP!r} survived the group kill: {survivors.strip()}")
    return Result(
        name,
        Status.OK,
        f"exit {exit_text.strip()} written, nothing survived; the group ran {', '.join(group)}",
    )


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


# --------------------------------------------------------------------------------
# the probes
# --------------------------------------------------------------------------------


@dataclass
class _Probe:
    sandbox: SandboxAdapter
    name: str
    root: Path
    builder_model: str
    #: Set by `_ping`; read by `_resume`.
    session: SessionId | None = None
    #: What the clone's own settings enable, switched off as every factory launch does.
    disabled_plugins: tuple[str, ...] = ()

    @property
    def repo(self) -> Path:
        return self.root / "repo"

    def files(self, key: str) -> AttemptFiles:
        attempt = self.root / "attempts" / key
        attempt.mkdir(parents=True, exist_ok=True)
        return AttemptFiles(
            prompt=attempt / "prompt.md",
            events=attempt / "events.jsonl",
            stderr=attempt / "stderr.log",
            exit=attempt / "exit",
            heartbeat=attempt / "heartbeat",
            pgid=attempt / "pgid",
            last_message=attempt / "last-message.json",
        )

    def invocation(
        self,
        key: str,
        prompt: str,
        *,
        role: Role = Role.REVIEWER,
        model: str = MODEL,
        effort: Effort | None = None,
        session: SessionId | None = None,
    ) -> Invocation:
        files = self.files(key)
        files.prompt.write_text(prompt, encoding="utf-8")
        return Invocation(
            role=role,
            model=model,
            effort=effort,
            max_turns=8,
            max_budget_usd=BUDGET_USD,
            session=session or claude.new_session(),
            resume=session is not None,
            files=files,
            schema=SCHEMA,
            disabled_plugins=self.disabled_plugins,
        )

    def launch(self, inv: Invocation) -> Run:
        self.sandbox.exec_sync(
            self.name,
            ["/bin/sh", "-lc", claude.script(inv)],
            workdir=str(self.repo),
            timeout=LAUNCH_SECONDS,
        )
        files = inv.files
        return stream.parse(
            files.events, exit_path=files.exit, stderr_path=files.stderr, expected=inv.expected
        )

    def sh(self, script: str, *, timeout: int = 60) -> str:
        return self.sandbox.exec_sync(
            self.name, ["/bin/sh", "-lc", script], workdir=str(self.repo), timeout=timeout
        ).stdout


# One `command -v` per tool: the image's /bin/sh is dash, whose `command -v` reports only its
# first operand (measured 2026-10-07), so `command -v node git setsid` never checked git or setsid.
_IMAGE_TOOLS_CHECK = 'for c in node git setsid; do command -v "$c" || exit 1; done'


def _image(p: _Probe) -> Result:
    done = p.sandbox.exec_sync(
        p.name,
        ["/bin/sh", "-lc", f"claude --version && {_IMAGE_TOOLS_CHECK}"],
        workdir=str(p.repo),
        timeout=120,
    )
    lines = done.stdout.strip().splitlines()
    if not done.ok or len(lines) < 4:
        detail = (done.stderr or done.stdout).strip()
        return Result("live: image", Status.FAIL, f"claude, node, git or setsid missing: {detail}")
    return Result("live: image", Status.OK, f"{lines[0]}; {', '.join(lines[1:])}")


def _ping(p: _Probe) -> Result:
    inv = p.invocation("ping", ANSWER)
    result = ping(p.launch(inv))
    if result.ok:
        p.session = inv.session
    return result


def _refusal(p: _Probe) -> Result:
    protected = load_harness_config(p.repo).first_protected_glob()
    if protected is None:
        return Result("live: protected-path refusal", Status.FAIL, "no protected path declared")
    target = p.repo / protected.glob
    before = _digest(target)
    inv = p.invocation(
        "refusal",
        f"Use the Edit or Write tool to append the line `# factory doctor canary` to "
        f"{protected.glob}. Try exactly once. If the tool is refused, do not retry and do "
        "not use any other tool; answer ok=false. If it succeeded, answer ok=true.\n",
        role=Role.BUILDER,
    )
    run = p.launch(inv)
    events = list(stream.events(stream.lines(inv.files.events.read_text(encoding="utf-8"))))
    return refusal(events, run, protected.glob, changed=_digest(target) != before)


def _effort(p: _Probe) -> Result:
    inv = p.invocation("effort", ANSWER, model=p.builder_model, effort="low")
    run = p.launch(inv)
    if not _answered(run):
        return Result("live: effort", Status.FAIL, _failure(run))
    transcript = p.sh(f'cat "$HOME"/.claude/projects/*/{inv.session}.jsonl')
    return effort(transcript, "low")


def _kill(p: _Probe) -> Result:
    inv = p.invocation(
        "kill", f"Run this exact command with the Bash tool and wait for it: {SLEEP}\n"
    )
    files = inv.files
    handle = RunHandle(
        run_id="doctor",
        attempt=0,
        sandbox=p.name,
        workdir=str(p.repo),
        attempt_dir=files.exit.parent,
    )
    p.sandbox.exec_detached(handle, claude.script(inv), {})
    deadline = time.monotonic() + LAUNCH_SECONDS
    while SLEEP not in stream.parse(files.events).commands:
        if files.exit.exists() or time.monotonic() > deadline:
            return Result("live: pgid kill", Status.FAIL, f"the agent never ran {SLEEP!r}")
        time.sleep(2)
    pgid = int(files.pgid.read_text().strip())
    group = group_commands(p.sh("ps -eo pgid=,comm="), pgid)
    p.sandbox.kill_group(p.name, pgid)
    deadline = time.monotonic() + KILL_WAIT_SECONDS
    while not files.exit.exists() and time.monotonic() < deadline:
        time.sleep(1)
    exit_text = files.exit.read_text() if files.exit.exists() else None
    # `[s]` keeps the pattern from matching the `sh -lc` that carries it.
    return kill(exit_text, p.sh(f"pgrep -f '[{SLEEP[0]}]{SLEEP[1:]}' || true"), group)


def _resume(p: _Probe) -> Result:
    if p.session is None:
        return Result("live: resume after stop", Status.SKIPPED, "the ping did not pass")
    p.sandbox.stop(p.name)
    return resumed(p.launch(p.invocation("resume", ANSWER, session=p.session)), p.session)


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


#: In order. A failed gate probe skips every probe after it: nothing later can pass.
PROBES: tuple[tuple[str, Callable[[_Probe], Result], bool], ...] = (
    ("live: image", _image, True),
    ("live: ping", _ping, True),
    ("live: protected-path refusal", _refusal, False),
    ("live: effort", _effort, False),
    ("live: pgid kill", _kill, False),
    ("live: resume after stop", _resume, False),
)


def _spec(project: Project, root: Path, deny_network: tuple[str, ...]) -> SandboxSpec:
    return SandboxSpec(
        project=project.name,
        role="build",
        name=sandbox_name(project),
        workspaces=(Workspace(root),),
        template=project.template or None,
        kits=project.kits,
        deny_network=deny_network,
    )


def _discard(sandbox: SandboxAdapter, name: str) -> None:
    if not sandbox.exists(name):
        return
    with contextlib.suppress(SbxError):
        sandbox.stop(name)
    # `remove` refuses a sandbox `inspect` does not yet report as stopped.
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while sandbox.inspect(name).get("state") != "stopped" and time.monotonic() < deadline:
        time.sleep(1)
    sandbox.remove(name)


def _probes(probe: _Probe) -> list[Result]:
    results: list[Result] = []
    blocked: str | None = None
    for label, step, gate in PROBES:
        if blocked is not None:
            results.append(Result(label, Status.SKIPPED, f"{blocked} did not pass"))
            continue
        try:
            result = step(probe)
        except Exception as exc:  # a probe that cannot finish is a row, not a crash
            result = Result(label, Status.FAIL, f"{type(exc).__name__}: {exc}")
        results.append(result)
        if gate and result.status is not Status.OK:
            blocked = label
    return results


def run(
    sandbox: SandboxAdapter,
    project: Project,
    *,
    root: Path,
    builder_model: str,
    deny_network: tuple[str, ...] = (),
) -> list[Result]:
    """Every probe against a fresh sandbox and clone, removed afterwards whatever happens."""
    name = sandbox_name(project)
    probe = _Probe(sandbox, name, root, builder_model)
    results: list[Result] = []
    try:
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        repo.clone_at(project.path, project.base_ref, probe.repo)
        settings = probe.repo / CLAUDE_SETTINGS
        probe.disabled_plugins = doctrine.enabled_plugins(
            settings.read_text(encoding="utf-8") if settings.exists() else None
        )
        _discard(sandbox, name)
        sandbox.ensure(_spec(project, root, deny_network))
        results.append(Result("live: sandbox", Status.OK, f"{name} over a clone of {project.name}"))
        results += _probes(probe)
    except (GitError, SbxError, OSError) as exc:
        results.append(Result("live: sandbox", Status.FAIL, f"{name}: {_first_line(str(exc))}"))
    finally:
        try:
            _discard(sandbox, name)
        except SbxError as exc:
            results.append(Result("live: cleanup", Status.FAIL, f"`sbx rm {name}` by hand: {exc}"))
        shutil.rmtree(root, ignore_errors=True)
    return results
