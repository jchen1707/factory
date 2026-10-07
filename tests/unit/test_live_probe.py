"""`factory doctor --deep`'s live probe: verdicts against host captures, wiring against a fake.

The captures in `tests/fixtures/claude/` were taken on the host with the production argv
(Claude Code 2.1.292). No sandbox run exists yet, so the orchestration is exercised only
against `ProbeSandbox`, which answers each launch the way the captures say the CLI does.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from factory import live_probe
from factory.agent import stream
from factory.agent.claude import READ_TOOLS, STRUCTURED_OUTPUT, WRITE_TOOLS
from factory.agent.stream import Expected, SessionId
from factory.doctor import Result, Status
from factory.registry import Project
from factory.sandbox.base import Completed, RunHandle, SandboxSpec
from tests.support import claude_stream as cs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "claude"
_MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())
MANIFEST, TRANSCRIPTS = _MANIFEST["fixtures"], _MANIFEST["transcripts"]
HAIKU = "claude-haiku-4-5-20251001"

#: `sbx ls` on 2026-10-07 with the daemon running and no `sbx login`.
SBX_NOT_LOGGED_IN = (
    "ERROR: list sandboxes: list local runtimes: list runtimes: request failed: 401 "
    "Unauthorized: user is not authenticated to Docker: secret not found\n"
    "no valid user session found, please sign in to Docker to proceed\n\n"
    "Sign in with: sbx login"
)


def _run(
    name: str, *, tools: frozenset[str] = READ_TOOLS, model: str = HAIKU, attest: bool = True
) -> stream.Run:
    """`attest=False` for the measure/ captures, whose argv pinned no session or tools."""
    entry = MANIFEST[name]
    expected = Expected(
        session=SessionId(entry.get("session", cs.SESSION)),
        model=model,
        tools=tools | {STRUCTURED_OUTPUT},
        plugins=frozenset(),
        permission_mode="default",
    )
    return stream.parse(
        FIXTURES / entry["events"],
        stderr_path=FIXTURES / entry["stderr"] if "stderr" in entry else None,
        expected=expected if attest else None,
    )


def _events(name: str) -> list[stream.Event]:
    return list(stream.events(stream.lines((FIXTURES / MANIFEST[name]["events"]).read_text())))


# --------------------------------------------------------------------------------
# verdicts against captures
# --------------------------------------------------------------------------------


def test_the_captured_ping_passes_with_its_init_attested() -> None:
    result = live_probe.ping(_run("ping"))
    assert result.status is Status.OK, result.detail
    assert "init attested" in result.detail


def test_a_ping_whose_init_disagrees_with_the_launch_fails_on_attestation() -> None:
    result = live_probe.ping(_run("ping", model="claude-opus-5-5"))
    assert result.status is Status.FAIL
    assert "init disagrees with the launch: model" in result.detail


@pytest.mark.parametrize("capture", ["not-logged-in", "auth-retry"])
def test_an_unauthenticated_ping_names_both_credential_routes(capture: str) -> None:
    result = live_probe.ping(_run(capture, attest=False))
    assert result.status is Status.FAIL
    assert "-- auth login" in result.detail
    assert "sbx secret set anthropic" in result.detail


def test_the_captured_resume_continues_the_pinned_session() -> None:
    session = SessionId(MANIFEST["resume-known"]["session"])
    assert live_probe.resumed(_run("resume-known"), session).status is Status.OK


def test_a_resume_the_cli_cannot_find_says_the_session_was_lost() -> None:
    run = _run("resume-unknown", tools=WRITE_TOOLS)
    result = live_probe.resumed(run, SessionId(cs.SESSION))
    assert result.status is Status.FAIL
    assert "lost the session across sbx stop/start" in result.detail


def test_the_captured_hook_refusal_passes_and_says_where_it_surfaced() -> None:
    result = live_probe.refusal(
        _events("protected-write"),
        _run("protected-write", tools=WRITE_TOOLS),
        "uv.lock",
        changed=False,
    )
    assert result.status is Status.OK, result.detail
    assert result.detail.startswith("PreToolUse:Edit hook error")
    assert "Refusing to edit uv.lock" in result.detail
    assert "result.permission_denials" in result.detail


def test_a_refusal_with_the_file_changed_fails_whatever_the_stream_says() -> None:
    result = live_probe.refusal(
        _events("protected-write"),
        _run("protected-write", tools=WRITE_TOOLS),
        "uv.lock",
        changed=True,
    )
    assert result.status is Status.FAIL
    assert "landed" in result.detail


def test_a_write_that_ran_unrefused_fails() -> None:
    # c1: the agent wrote notes.txt with no hook in the way.
    run = _run("build-tools", tools=WRITE_TOOLS)
    result = live_probe.refusal(_events("build-tools"), run, "notes.txt", changed=False)
    assert result.status is Status.FAIL
    assert "did not refuse the write" in result.detail


def test_a_write_refused_by_the_permission_layer_is_not_layer_a_refusing_it() -> None:
    # c2: Write was outside --allowedTools, so the CLI denied it; no hook ran.
    run = _run("denied", tools=WRITE_TOOLS, attest=False)
    result = live_probe.refusal(_events("denied"), run, "denied.txt", changed=False)
    assert result.status is Status.FAIL
    assert "did not refuse the write" in result.detail


def test_an_agent_that_never_tried_the_write_is_inconclusive_not_a_pass() -> None:
    run = _run("ping")
    result = live_probe.refusal(_events("ping"), run, "uv.lock", changed=False)
    assert result.status is Status.FAIL
    assert result.detail.startswith("inconclusive")


def test_the_captured_transcript_records_the_requested_effort() -> None:
    transcript = (FIXTURES / TRANSCRIPTS["effort-low"]["transcript"]).read_text()
    assert live_probe.effort(transcript, "low").status is Status.OK
    other = live_probe.effort(transcript, "high")
    assert other.status is Status.FAIL
    assert "['low']" in other.detail
    assert live_probe.effort("", "low").status is Status.FAIL


def test_the_kill_verdict_needs_the_exit_file_and_no_survivor() -> None:
    group = live_probe.group_commands("  41 sh\n  42 init\n  41 claude\n  41 sleep\n", 41)
    assert group == ["sh", "claude", "sleep"]
    assert live_probe.kill("143", "", group).status is Status.OK
    assert live_probe.kill(None, "", group).status is Status.FAIL
    assert live_probe.kill("143", "57\n", group).status is Status.FAIL


def test_sbx_without_login_says_to_run_sbx_login() -> None:
    message = live_probe.sbx_unready(SBX_NOT_LOGGED_IN)
    assert "Run `sbx login`" in message
    assert "factory doctor --deep" in message
    missing = live_probe.sbx_unready("sbx unusable: [Errno 2] No such file or directory: 'sbx'")
    assert "not installed" in missing


# --------------------------------------------------------------------------------
# the orchestration, against a fake that answers like the captures
# --------------------------------------------------------------------------------

PGID = 4242


def _claude_line(script: str) -> list[str]:
    body = next(line for line in script.splitlines() if line.startswith("claude "))
    return shlex.split(body)


@dataclass
class ProbeSandbox:
    """Answers `live_probe`'s calls the way the captured CLI and the measured `sbx` do."""

    ping: str = "success"
    write_lands: bool = False
    alive: set[str] = field(default_factory=set)
    specs: list[SandboxSpec] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    transcript: str = '{"type":"assistant","perTurnEffort":"low"}\n'
    kill_dir: Path = field(default_factory=Path)
    stopping: int = 0
    settings: list[dict[str, object]] = field(default_factory=list)

    def exists(self, name: str) -> bool:
        return name in self.alive

    def ensure(self, spec: SandboxSpec) -> None:
        self.specs.append(spec)
        self.alive.add(spec.name)

    def stop(self, name: str) -> None:
        self.stopped.append(name)
        self.stopping = 2

    def inspect(self, name: str) -> dict[str, str]:
        # `sbx stop` can return before the state flips; two reads say `running` first.
        self.stopping -= 1
        return {"state": "running" if self.stopping > 0 else "stopped"}

    def remove(self, name: str) -> None:
        assert self.inspect(name)["state"] == "stopped", "sbx remove refused: not stopped"
        self.alive.discard(name)

    def kill_group(self, name: str, pgid: int) -> None:
        assert pgid == PGID
        (self.kill_dir / "exit").write_text("143")

    def exec_detached(self, handle: RunHandle, script: str, env: object) -> None:
        argv = _claude_line(script)
        session = argv[argv.index("--session-id") + 1]
        use = cs.assistant_tool_use("Bash", {"command": live_probe.SLEEP}, session=session)
        (handle.attempt_dir / "events.jsonl").write_text(
            "\n".join(cs.lines(cs.init(session=session), use)) + "\n"
        )
        (handle.attempt_dir / "pgid").write_text(str(PGID))
        self.kill_dir = handle.attempt_dir

    def exec_sync(
        self, name: str, argv: list[str], *, workdir: str | None = None, **_: object
    ) -> Completed:
        script = argv[-1]
        out = ""
        if script.startswith("claude --version"):
            out = "2.1.292 (Claude Code)\n/usr/bin/node\n/usr/bin/git\n/usr/bin/setsid\n"
        elif "\nclaude -p " in f"\n{script}":
            self._launch(_claude_line(script), Path(str(workdir)))
        elif script.startswith("cat "):
            out = self.transcript
        elif script.startswith("ps "):
            out = f"{PGID} sh\n{PGID} claude\n{PGID} sleep\n1 init\n"
        elif script.startswith("pgrep -f "):
            # procps `pgrep -f` skips itself, not the `sh -lc` carrying the same pattern.
            out = "77\n" if live_probe.SLEEP in script else ""
        return Completed(tuple(argv), 0, out, "")

    def _launch(self, argv: list[str], cwd: Path) -> None:
        self.settings.append(json.loads(argv[argv.index("--settings") + 1]))
        resume = "--resume" in argv
        session = argv[argv.index("--resume" if resume else "--session-id") + 1]
        model = argv[argv.index("--model") + 1]
        tools = (*argv[argv.index("--tools") + 1].split(","), STRUCTURED_OUTPUT)
        events = Path(argv[argv.index(">") + 1])
        key = events.parent.name
        ok = {"ok": True}
        if key == "ping" and self.ping == "not-logged-in":
            scenario = cs.not_logged_in(session=session, model=model, tools=tools)
        elif key == "refusal":
            target = cwd / "uv.lock"
            if self.write_lands:
                target.write_text(target.read_text() + "# factory doctor canary\n")
            use = cs.assistant_tool_use("Edit", {"file_path": str(target)}, session=session)
            use_id = use["message"]["content"][0]["id"]
            refused = cs.tool_result(
                use_id,
                "PreToolUse:Edit hook error: [node protect_paths.mjs]: Refusing to edit uv.lock",
                is_error=True,
                session=session,
            )
            scenario = cs.Scenario(
                cs.lines(
                    cs.init(session=session, model=model, tools=tools),
                    use,
                    refused,
                    cs.result_success({"ok": False}, session=session, model=model),
                ),
                0,
                "",
            )
        else:
            scenario = cs.success(ok, session=session, model=model, tools=tools)
        events.write_text("\n".join(scenario.lines) + "\n")
        (events.parent / "exit").write_text(str(scenario.exit_code))


def _project(tmp_path: Path) -> Project:
    repo = tmp_path / "demo"
    repo.mkdir()
    (repo / "harness.config.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "gates": [{"name": "t", "kind": "test", "run": ["true"]}],
                "hooks": {"protected": [{"glob": "uv.lock", "why": "generated"}]},
            }
        )
    )
    (repo / "uv.lock").write_text("version = 1\n")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"pstack@pstack-claude": True}})
    )
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "seed"],
        ["update-ref", "refs/remotes/origin/main", "HEAD"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    return Project(
        name="demo",
        team="DEM",
        path=repo,
        remote=str(repo),
        base_branch="main",
        stack="python",
        template="claude-python:v1",
        kits=(),
        static_mcp=(),
        build_sandbox="factory-build-demo",
        review_sandbox="factory-review-demo",
        vault_mount="none",
        network_allow=(),
    )


def _probe_project(tmp_path: Path, project: Project, sandbox: ProbeSandbox) -> list[Result]:
    return live_probe.run(
        sandbox,  # type: ignore[arg-type]
        project,
        root=tmp_path / "doctor",
        builder_model="claude-opus-5-5",
    )


def _probe(tmp_path: Path, sandbox: ProbeSandbox) -> list[Result]:
    return _probe_project(tmp_path, _project(tmp_path), sandbox)


def test_every_probe_reports_a_row_and_the_sandbox_and_clone_are_removed(tmp_path: Path) -> None:
    sandbox = ProbeSandbox()
    results = _probe(tmp_path, sandbox)

    assert [(r.name, r.status) for r in results] == [
        ("live: sandbox", Status.OK),
        *((label, Status.OK) for label, _, _ in live_probe.PROBES),
    ], results
    assert sandbox.specs[0].name == "factory-doctor-demo"
    assert sandbox.specs[0].template == "claude-python:v1"
    assert sandbox.specs[0].workspaces[0].path == tmp_path / "doctor"
    assert sandbox.stopped[0] == "factory-doctor-demo"  # before the resume
    # The clone's own plugins are off on every launch, as on every factory launch.
    assert sandbox.settings
    assert all(s == {"enabledPlugins": {"pstack@pstack-claude": False}} for s in sandbox.settings)
    assert not sandbox.alive
    assert not (tmp_path / "doctor").exists()


def test_a_ping_that_cannot_authenticate_skips_every_later_probe(tmp_path: Path) -> None:
    sandbox = ProbeSandbox(ping="not-logged-in")
    by_name = {r.name: r for r in _probe(tmp_path, sandbox)}

    assert by_name["live: ping"].status is Status.FAIL
    assert "sbx secret set anthropic" in by_name["live: ping"].detail
    for label in ("live: protected-path refusal", "live: effort", "live: resume after stop"):
        assert by_name[label].status is Status.SKIPPED
        assert by_name[label].detail == "live: ping did not pass"
    assert not sandbox.alive


def test_the_probe_clones_the_base_ref_not_the_local_branch(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project.path / "harness.config.json").write_text(json.dumps({"name": "demo", "gates": []}))
    subprocess.run(
        [
            *("git", "-C", str(project.path), "-c", "user.name=t", "-c", "user.email=t@x.invalid"),
            *("commit", "-qam", "local only: no gates, no protected path"),
        ],
        check=True,
    )
    by_name = {r.name: r for r in _probe_project(tmp_path, project, ProbeSandbox())}

    # origin/main declares uv.lock protected; the local branch declares nothing at all.
    assert by_name["live: protected-path refusal"].status is Status.OK


def test_a_probe_that_raises_is_a_failed_row_and_the_sandbox_still_goes(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project.path / "harness.config.json").unlink()
    subprocess.run(
        [
            *("git", "-C", str(project.path), "-c", "user.name=t", "-c", "user.email=t@x.invalid"),
            *("commit", "-qam", "no harness config"),
            "--quiet",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(project.path), "update-ref", "refs/remotes/origin/main", "HEAD"],
        check=True,
    )
    sandbox = ProbeSandbox()
    by_name = {r.name: r for r in _probe_project(tmp_path, project, sandbox)}

    assert by_name["live: protected-path refusal"].status is Status.FAIL
    assert by_name["live: protected-path refusal"].detail.startswith("Blocked")
    assert by_name["live: resume after stop"].status is Status.OK
    assert not sandbox.alive


def test_a_protected_write_that_lands_on_disk_fails_the_refusal(tmp_path: Path) -> None:
    by_name = {r.name: r for r in _probe(tmp_path, ProbeSandbox(write_lands=True))}

    assert by_name["live: protected-path refusal"].status is Status.FAIL
    assert "landed" in by_name["live: protected-path refusal"].detail
