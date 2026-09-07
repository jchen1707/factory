"""Fakes for the whole pipeline — §21.3.

The point of this suite is that the entire state machine runs in-process with no `sbx`,
no Docker, no network and no model call. If a step can only be exercised against the
real thing, that step has too much of the real thing in it.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from factory import driver
from factory.agent.codex import CodexAdapter
from factory.harness import load_harness_config
from factory.intake.linear import Issue
from factory.machine import State
from factory.registry import load_registry
from factory.routing import load_routing
from factory.sandbox.base import Completed, RunHandle, RunResult, RunStatus, SandboxSpec
from factory.steps import Context
from factory.steps import implement as implement_step
from factory.steps import sandbox as sandbox_step
from factory.store import Store

HELLO_EVENTS: list[dict[str, Any]] = [
    {"type": "thread.started", "thread_id": "01a0-fake-thread"},
    {"type": "turn.started"},
    {
        "type": "item.completed",
        "item": {"id": "item_0", "type": "agent_message", "text": "done"},
    },
    {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 12000,
            "cached_input_tokens": 9000,
            "cache_write_input_tokens": 0,
            "output_tokens": 300,
            "reasoning_output_tokens": 40,
        },
    },
]

GOOD_RESULT: dict[str, Any] = {
    "status": "implemented",
    "summary": "Added the app factory, Settings and a health endpoint.",
    "files_changed": ["src/app/main.py", "src/app/settings.py"],
    "tests_added": ["tests/test_health.py"],
    "behaviour_changed": True,
    "seam_confirmed": True,
    "tdd_used": True,
    "gates_run": ["ruff check", "mypy", "pytest"],
    "out_of_scope": ["BAC-3's dependency approval"],
    # Required like every other key — see test_every_property_is_required_*.
    "blocked_reason": None,
    "docs_updated": [],
}

#: The canned gate report `FakeSandbox` returns for the verify step. Its gate names are
#: exactly the three `GOOD_RESULT` claims, all `pass` — so a clean run cross-checks
#: cleanly and reaches `reviewing`. Tests that want a different verdict set
#: `fake.gate_report` (often from a `tests/fixtures/gate-report-*.json` file).
GOOD_GATE_REPORT: dict[str, Any] = {
    "schemaVersion": 1,
    "root": "/worktree",
    "targets": [{"name": "python-harness", "dir": "."}],
    "missingApps": [],
    "gates": [
        {
            "name": "ruff check",
            "kind": "lint",
            "status": "pass",
            "exit": 0,
            "durationMs": 120,
            "caveat": None,
            "when": None,
            "outputTail": "",
        },
        {
            "name": "mypy",
            "kind": "type",
            "status": "pass",
            "exit": 0,
            "durationMs": 340,
            "caveat": None,
            "when": None,
            "outputTail": "",
        },
        {
            "name": "pytest",
            "kind": "test",
            "status": "pass",
            "exit": 0,
            "durationMs": 2100,
            "caveat": None,
            "when": None,
            "outputTail": "",
        },
    ],
    "verdict": "pass",
}


def advance_state(ctx: Context, *, until: State | None = None, limit: int = 24) -> driver.Result:
    """Drive one run the way `factory tick` and `factory run` both do, and stop.

    The replacement for `plan.run`, `implement.run`, `verify.run` and `review.run`, which
    were the foreground half of the two execution models PR 2 collapsed. They were
    `start` → wait → `collect`; this is the same three things through `driver.step`, one
    look at a time.

    It lives here and **not** in `driver` on purpose. `factory run` needs `drive`, and
    production code that exists only so a test can call it is the smell this whole review
    is about.

    `until` is for the two steps that crossed more than one state — `implement.run` was
    called at `worktree_ready` and returned at `verifying`. The default stops at the first
    change, which is what a `verify.run` or `review.run` call site meant: the fake writes
    `exit` synchronously inside `exec_detached`, so one of those is generally two `step`
    calls (reap sees no attempt and starts one; reap sees `exit` and collects).

    `Blocked` and `Resumable` propagate, because `driver.step` does not catch them — the
    call sites that wrapped a `run()` in `pytest.raises` still read the same way.
    """
    before = ctx.state
    result = driver.Result(driver.Outcome.WAITING, "")
    for _ in range(limit):
        result = driver.step(ctx)
        if result.outcome in (driver.Outcome.STOPPED, driver.Outcome.NEEDS_HUMAN):
            return result
        settled = ctx.state is until if until is not None else ctx.state is not before
        if settled:
            return result
    raise AssertionError(
        f"{before} did not settle after {limit} driver.step calls (now {ctx.state})"
    )


@dataclass
class FakeSandbox:
    """Writes the canned files the real wrapper would have written.

    It writes them *synchronously* inside `exec_detached`, which is the one place the
    fake diverges from reality: the control plane's polling loop then finds `exit`
    already present. The polling loop itself is exercised by its own unit test.
    """

    events: list[dict[str, Any]] = field(default_factory=lambda: list(HELLO_EVENTS))
    result: dict[str, Any] = field(default_factory=lambda: dict(GOOD_RESULT))
    exit_code: int = 0
    stderr: str = ""
    created: list[SandboxSpec] = field(default_factory=list)
    sync_calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    canary_exit: int = 2
    #: The canned `gate_report.mjs --json` document the verify step reads back. Tests
    #: override this to exercise the fail / incomplete / mismatch transitions.
    gate_report: dict[str, Any] = field(default_factory=lambda: dict(GOOD_GATE_REPORT))
    #: When set, returned verbatim as the report call's stdout instead of serialising
    #: `gate_report` — the one way to make the verify step see a non-JSON document.
    gate_report_raw_stdout: str | None = None
    #: What a reviewer axis writes to its `-o` path when that path is writable from
    #: inside the sandbox. Tests override it to exercise the finding transitions.
    review_findings: dict[str, Any] = field(default_factory=lambda: {"findings": []})
    #: The credential names that are set, and non-empty, inside the VM's environment.
    #: A different channel from `secrets`: `sbx inspect` cannot see it, which is exactly
    #: how a live token stayed invisible to the preflight until 2026-08-22.
    env_credentials: list[str] = field(default_factory=list)
    #: What `sbx inspect --json` reports under `secrets`. Empty is the shape a correctly
    #: provisioned host produces; the tests set it to the shape measured on 2026-08-21.
    secrets: list[dict[str, str]] = field(default_factory=list)
    #: Where a `--clone` sandbox's private copy of the repository lives on the test's disk.
    #: The real thing puts the clone at the *identical* host path inside the VM, which no
    #: in-process fake can do — so this one translates instead: every path under the
    #: project root is rewritten into here. What that buys is the constraint that actually
    #: breaks. A write under the project path never reaches the host, only the additional
    #: `rw` mounts are shared, and `git` inside the sandbox is acting on a different
    #: repository from the one the host reads. All three are why the clone path exists.
    clone_root: Path | None = None
    #: Which sandboxes are *running*. Modelled because `sbx` ties the `sandbox-<name>` git
    #: remote to it: the remote is registered on the host when the sandbox starts, given a
    #: new port every time, and **removed when it stops**. A clone run reaches `reviewing`
    #: with its build sandbox long since auto-stopped, so a `fetch_back` that does not
    #: start it first fetches from a remote that is not there. That is how a real FRO-6 run
    #: failed, against a remote `git remote -v` had listed a minute earlier.
    running: set[str] = field(default_factory=set)
    #: What `poll` should answer regardless of the filesystem. `None` means "read the
    #: attempt directory", which is what the real adapter does. Set it to model the two
    #: cases no in-process fake produces on its own: a sandbox that stopped under a live
    #: run (F3), and a host that rebooted leaving neither `exit` nor a fresh heartbeat
    #: (F4). Both are the shapes recovery exists for, and both are invisible to a fake
    #: that writes `exit` synchronously.
    poll_status: RunStatus | None = None
    #: When true, `exec_detached` writes only the prompt-side files and no `exit`, so the
    #: attempt looks like one still in flight. That is the state a tick hands to the
    #: *next* tick, and nothing else in this file can produce it.
    detach_without_finishing: bool = False
    #: Every detached invocation, so a test can prove a resume passed a session id.
    detached: list[tuple[str, str]] = field(default_factory=list)
    #: The attempt directory of each detached invocation, parallel to `detached`. The real
    #: wrapper writes `exit` into the attempt dir it was handed; the fake's `kill_agent` has
    #: only the sandbox name, so it reads the dir back from here.
    detached_dirs: list[Path] = field(default_factory=list)
    #: When true (default), `kill_agent` writes the `exit` file, modelling the wrapper's
    #: graceful-exit-on-signal — the real `kill_agent` only signals; the wrapper traps it and
    #: writes `exit` last. This is what gives a suspended attempt a real terminal record
    #: rather than a truncated one (§16.3b step 1).
    kill_writes_exit: bool = True
    #: The in-VM process name each detached invocation actually runs under, parallel to
    #: `detached`. Modelled because `kill_agent` is `pkill -x <proc>`, which matches on the
    #: process name and nothing else: a caller that names the wrong process signals nothing,
    #: the wrapper never traps, and no `exit` file lands. A fake that ignored the argument
    #: reported a successful kill for every name and could not tell those two worlds apart —
    #: which is how `reap` came to signal `codex` at a `verifying` attempt that runs `node`.
    detached_procs: list[str] = field(default_factory=list)
    #: The in-VM process group of each detached invocation, parallel to `detached`, and
    #: the fake's whole reason for existing at this level of detail. The real wrapper
    #: publishes its pgid and `kill_group` signals that group alone; a fake that stopped
    #: "the last detached run" — which is what `kill_agent` below still models, correctly,
    #: for the name-matching path — could not tell a scoped kill from an unscoped one, and
    #: that difference *is* the safety property for two tickets sharing one sandbox.
    detached_pgids: list[int] = field(default_factory=list)
    #: Handed out in order, so a test can name the group it expects to survive.
    next_pgid: int = 1000

    def exists(self, name: str) -> bool:
        return any(spec.name == name for spec in self.created)

    # -- clone mode ---------------------------------------------------------------

    def _spec(self, name: str) -> SandboxSpec | None:
        return next((spec for spec in self.created if spec.name == name), None)

    def clone_dir(self, name: str) -> Path | None:
        """The VM-side repository of a clone sandbox, or None for a bind-mounted one."""
        spec = self._spec(name)
        if spec is None or not spec.clone or self.clone_root is None:
            return None
        return self.clone_root / name

    def _make_clone(self, spec: SandboxSpec) -> None:
        """What `sbx create --clone` does, in the two respects the factory depends on.

        A real `git clone` of the host checkout — so `origin/<base>` resolves inside it
        with no network, exactly as measured — and a `sandbox-<name>` remote added to the
        *host* checkout pointing back at it, which is how the branch gets home. The real
        remote is a git daemon URL rather than a path; plain git reaches both, and the
        production code fetches by `FETCH_HEAD` rather than by anything daemon-specific.
        """
        assert self.clone_root is not None, "a clone spec needs FakeSandbox.clone_root"
        source = spec.workspaces[0].path
        target = self.clone_root / spec.name
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", str(source), str(target)], check=True, capture_output=True
        )
        git(target, "config", "user.email", "agent@example.invalid")
        git(target, "config", "user.name", "the agent")
        # No `sandbox-<name>` remote is added, matching what the production path relies
        # on: `sbx` does register one at create, but it is withdrawn on stop and never
        # restored, so nothing reads it. `git_daemon_url` is the live lookup instead.

    def _start(self, name: str) -> None:
        self.running.add(name)

    def stop_sandbox(self, name: str) -> None:
        """The auto-stop, on demand. Tests call it to put a sandbox back where a real run
        finds it at the `reviewing` entry: stopped, and serving nothing."""
        self.running.discard(name)

    #: `env name -> sbx-cs-… placeholder`, as `sbx secret ls --sandbox <name>` reports it
    #: for a project that has opted into in-VM delivery. Empty is the shape of every
    #: sandbox that has not, which is all of them but one.
    custom_secrets: dict[str, str] = field(default_factory=dict)

    #: `(exit code, body)` for each `curl` the in-VM delivery adapter makes, in order.
    #: Empty answers every call with an empty JSON array, which is what `find_pr` reads
    #: as "no merge request open for this branch".
    api_replies: list[tuple[int, str]] = field(default_factory=list)

    def custom_secret_placeholder(self, name: str, env: str) -> str | None:
        return self.custom_secrets.get(env)

    def git_daemon_url(self, name: str) -> str | None:
        """Published only while the sandbox is running — the constraint that broke.

        Returned as a plain path rather than a `git://` URL, because the fake's clone is a
        real directory and plain git reaches both. What is modelled is the *lifecycle*: a
        stopped sandbox serves nothing, so a `fetch_back` that does not start one first has
        nowhere to fetch from. That is exactly how a real FRO-6 run failed.
        """
        clone = self.clone_dir(name)
        if clone is None or name not in self.running:
            return None
        return str(clone)

    def _vm_path(self, name: str, path: str) -> str:
        """Rewrite a host path under the project root into the clone. A no-op elsewhere —
        the additional `rw` mounts really are the same directory on both sides."""
        clone = self.clone_dir(name)
        if clone is None:
            return path
        spec = self._spec(name)
        assert spec is not None
        root = str(spec.workspaces[0].path)
        if path == root:
            return str(clone)
        if path.startswith(root + "/"):
            return str(clone) + path[len(root) :]
        return path

    def inspect(self, name: str) -> dict[str, Any]:
        return {
            "name": name,
            "state": "running",
            "secrets": list(self.secrets),
            "workspace": "",
        }

    def ensure(self, spec: SandboxSpec) -> None:
        if self.exists(spec.name):
            return
        self.created.append(spec)
        if spec.clone:
            self._make_clone(spec)

    def _writable_roots(self, name: str) -> list[Path]:
        """The rw workspaces of a created sandbox — the only host paths a command inside
        it can write to. Modelled because the real thing enforces it: a reviewer told to
        write outside its mounts exits 0 and quietly produces nothing, which is how two
        Tier-2 bugs reached a real run."""
        spec = self._spec(name)
        if spec is None:
            return []
        writable = [w for w in spec.workspaces if not w.readonly]
        if spec.clone:
            # The primary workspace is writable *inside* the VM and invisible outside it:
            # `--clone` replaces the bind mount with a private copy. Modelling it as a
            # shared writable root would hide the whole reason `Context.factory_dir` moves
            # `.factory/` onto the additional mount.
            writable = writable[1:]
        return [w.path for w in writable]

    def exec_sync(
        self,
        name: str,
        argv: Sequence[str],
        *,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int | None = None,
        stdin: str | None = None,
    ) -> Completed:
        self.sync_calls.append((name, tuple(argv)))
        self._start(name)
        if argv and argv[0] == "git" and self.clone_dir(name) is not None:
            # Run it, for real, against the clone. Faking git here would fake exactly the
            # thing the clone path is made of: cutting the branch inside the VM, and the
            # scratch worktree the red-phase replay lands the test half of the diff on.
            translated = [self._vm_path(name, str(arg)) for arg in argv]
            proc = subprocess.run(
                translated,
                cwd=self._vm_path(name, workdir) if workdir else None,
                capture_output=True,
                text=True,
                check=False,
                input=stdin,
            )
            return Completed(tuple(argv), proc.returncode, proc.stdout, proc.stderr)
        probe = re.search(r'\[ -e "([^"]+)" \]', " ".join(str(a) for a in argv))
        if probe is not None:
            # The clone-isolation canary: the host wrote a marker into the project tree and
            # is asking whether the VM can see it. `_vm_path` is the whole answer — identity
            # for a bind mount (visible, correctly) and a redirect into the private clone
            # for a `--clone` sandbox (absent, correctly). Modelled rather than canned,
            # because a canary that cannot fail proves nothing, which is the exact mistake
            # §9.3 exists to prevent.
            target = Path(self._vm_path(name, probe.group(1)))
            return Completed(tuple(argv), 0, "visible" if target.exists() else "absent", "")
        if any("gate_report.mjs" in str(arg) for arg in argv):
            # The verify step: return the canned report, with the exit code that mirrors
            # its verdict (0/1/3 for pass/fail/incomplete). The step trusts the JSON
            # `verdict` field over the exit code, so this only has to be consistent.
            if self.gate_report_raw_stdout is not None:
                return Completed(tuple(argv), 0, self.gate_report_raw_stdout, "")
            exit_for = {"pass": 0, "fail": 1, "incomplete": 3}
            return Completed(
                tuple(argv),
                exit_for.get(self.gate_report.get("verdict", "incomplete"), 3),
                json.dumps(self.gate_report, indent=2),
                "",
            )
        if argv and argv[0] == "curl":
            # The in-VM GitLab adapter's `/api/v4` calls. Canned in order rather than
            # routed by URL: what these tests are about is the *lifecycle* around the
            # calls — the credential installed before them and removed after — and the
            # adapter's own request shapes are pinned by `tests/unit/test_sandbox_gitlab`.
            code, body = self.api_replies.pop(0) if self.api_replies else (0, "[]")
            return Completed(tuple(argv), code, body, "")
        if argv and argv[-1].endswith("protect_paths.mjs"):
            return Completed(
                tuple(argv), self.canary_exit, "", "Refusing to edit uv.lock - regenerate it."
            )
        if "HARNESS_SKIP_VERIFY" in " ".join(argv):
            return Completed(tuple(argv), 0, "", "")
        if any("[ -n " in str(arg) for arg in argv):
            # The preflight's env probe. It asks which of the repository's `secretVars`
            # are set inside the VM and gets back names, never values.
            return Completed(tuple(argv), 0, "\n".join(self.env_credentials), "")
        if argv and argv[0] == "codex" and "-o" in argv:
            # A reviewer axis. It writes its findings to the `-o` path — but only when
            # that path is inside one of the sandbox's writable workspaces, exactly as
            # the real one does. Outside, codex exits 0 and says so on stderr, which is
            # the shape BAC-4's run 2efa19065ce6476e produced.
            out = Path(argv[argv.index("-o") + 1])
            roots = self._writable_roots(name)
            if any(root == out.parent or root in out.parents for root in roots):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(self.review_findings), encoding="utf-8")
                return Completed(tuple(argv), 0, "", "")
            return Completed(
                tuple(argv),
                0,
                "",
                f'Failed to write last message file "{out}": No such file or directory (os error 2)',
            )
        return Completed(tuple(argv), 0, "/usr/bin/node\n/usr/bin/git\nv22.22.1", "")

    def exec_detached(self, handle: RunHandle, script: str, env: Mapping[str, str]) -> None:
        self._start(handle.sandbox)
        self.detached.append((handle.sandbox, script))
        self.detached_dirs.append(handle.attempt_dir)
        # The gate report is `node gate_report.mjs`; every other detached body is `codex`.
        self.detached_procs.append("node" if "gate_report.mjs" in script else "codex")
        # Model the wrapper publishing its process group. `pgid` for every phase but the
        # plan half of a rewind, which writes its own so the two cannot signal each other.
        self.next_pgid += 1
        self.detached_pgids.append(self.next_pgid)
        pgid_name = "plan-pgid" if "plan-exit" in script else "pgid"
        handle.attempt_dir.mkdir(parents=True, exist_ok=True)
        if "__FACTORY_BODY__" in script:
            (handle.attempt_dir / pgid_name).write_text(str(self.next_pgid))
        if self.detach_without_finishing:
            directory = handle.attempt_dir
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "heartbeat").write_text(str(int(time.time())))
            # A running attempt has already streamed `thread.started` — it is the first
            # line codex writes, long before the first turn ends. Measured on BAC-6
            # attempt 1, suspended nine minutes in: `events.jsonl` 120 KB, heartbeat
            # fresh, no `exit`. A fake that wrote only the heartbeat modelled a
            # sessionless agent that does not exist, and hid the fact that nothing on
            # the detached path ever captured the id.
            (directory / "events.jsonl").write_text(json.dumps(HELLO_EVENTS[0]) + "\n")
            return
        clone = self.clone_dir(handle.sandbox)
        is_verify = "gate_report.mjs" in script
        is_review = handle.sandbox.startswith("factory-review-")
        if clone is not None and not is_verify and not is_review:
            # The agent's commits land in the clone and nowhere else, which is what makes
            # `clone.fetch_back` a real fetch rather than a formality. Only the implement
            # run commits; verify runs the gates and the review runs in a different sandbox.
            self._commit_in_clone(clone)
        directory = handle.attempt_dir
        directory.mkdir(parents=True, exist_ok=True)
        if is_verify:
            self._write_verify_artifacts(directory)
        elif is_review:
            self._write_review_artifacts(directory, script)
        else:
            (directory / "events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in self.events) + "\n"
            )
            (directory / "stderr.log").write_text(self.stderr)
            (directory / "last-message.json").write_text(json.dumps(self.result))
            (directory / "heartbeat").write_text("0")
            (directory / "exit").write_text(str(self.exit_code))

    def _write_verify_artifacts(self, directory: Path) -> None:
        """The detached gate report: the canned JSON to `gates.stdout.txt` and an exit
        code that mirrors the verdict (0/1/3 for pass/fail/incomplete). The verify step
        reuses the implement attempt's directory, so `last-message.json` is left in place
        for the cross-check — only the gate-run files are written here."""
        stdout = (
            self.gate_report_raw_stdout
            if self.gate_report_raw_stdout is not None
            else json.dumps(self.gate_report, indent=2)
        )
        (directory / "gates.stdout.txt").write_text(stdout, encoding="utf-8")
        (directory / "gates.stderr.txt").write_text("", encoding="utf-8")
        (directory / "heartbeat").write_text("0")
        exit_for = {"pass": 0, "fail": 1, "incomplete": 3}
        (directory / "exit").write_text(
            str(exit_for.get(self.gate_report.get("verdict", "incomplete"), 3))
        )

    def _write_review_artifacts(self, directory: Path, script: str) -> None:
        """The detached review fan-out: write the canned findings to each axis's `-o`
        scratch path (parsed out of the script the way the real codex `-o` names it), plus
        the heartbeat and exit the wrapper writes. The findings land in the per-project
        scratch and `collect` moves them into the run's own directory — same as the real
        path, modelled synchronously."""
        for match in re.finditer(r"(?:^|\s)-o (\S+)", script):
            out = Path(match.group(1).strip("'\""))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(self.review_findings), encoding="utf-8")
        (directory / "heartbeat").write_text("0")
        (directory / "exit").write_text(str(self.exit_code))

    def poll(self, handle: RunHandle) -> RunStatus:
        if self.poll_status is not None:
            return self.poll_status
        return (
            RunStatus.EXITED
            if (handle.attempt_dir / handle.exit_name).exists()
            else RunStatus.RUNNING
        )

    def collect(self, handle: RunHandle) -> RunResult:
        return RunResult(
            exit_code=int((handle.attempt_dir / "exit").read_text()),
            events_path=handle.attempt_dir / "events.jsonl",
            stderr_path=handle.attempt_dir / "stderr.log",
            last_message_path=handle.attempt_dir / "last-message.json",
        )

    def _commit_in_clone(self, clone: Path) -> None:
        """Stand in for the agent's turn: write the files the canned result claims, plus a
        test that fails at the base ref, and commit them on the branch already checked
        out. The red-phase replay then has a real diff to replay."""
        for relative in self.result.get("files_changed", []):
            path = clone / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("def exclude_internal(names):\n    return [n for n in names]\n")
        for relative in self.result.get("tests_added", []):
            path = clone / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "from src.app.main import exclude_internal\n\n\n"
                "def test_internal_documents_are_excluded() -> None:\n"
                "    assert exclude_internal(['a']) == ['a']\n"
            )
        git(clone, "add", "-A")
        git(clone, "commit", "-m", "feat: the agent's turn")

    def kill_agent(self, name: str, proc: str = "codex") -> None:
        # The real `kill_agent` runs `pkill -x <proc>`; the wrapper traps the signal and
        # writes the `exit` file. The fake models both steps so a suspend gets a real
        # terminal record. The last detached run in this sandbox is the one to stop.
        #
        # `proc` is honoured rather than ignored: `pkill -x` matches the process name
        # exactly, so naming the wrong one signals nothing at all and no `exit` ever
        # lands. Returning early here is that world, and it is the one a `verifying`
        # attempt lived in until `reap` learned to name `node`.
        if not self.kill_writes_exit or not self.detached_dirs:
            return
        if proc != self.detached_procs[-1]:
            return
        (self.detached_dirs[-1] / "exit").write_text(str(self.exit_code))

    def kill_group(self, name: str, pgid: int) -> None:
        """Signal exactly the attempt that published this pgid, and nothing else.

        The contrast with `kill_agent` above is the point of the fake: that one stops
        "the last detached run in this sandbox", because `pkill -x` genuinely cannot do
        better, and this one stops the one run it was asked to. A project running two
        tickets at once shares one build sandbox, so the difference between those two
        behaviours is the difference between a timeout on one run and a wrong terminal
        record on the other.
        """
        if not self.kill_writes_exit:
            return
        for index, published in enumerate(self.detached_pgids):
            if published == pgid:
                (self.detached_dirs[index] / "exit").write_text(str(self.exit_code))
                return

    def stop(self, name: str) -> None:
        self.stop_sandbox(name)

    def remove(self, name: str) -> None:
        return None


#: The BAC team's real label set, read from Linear on 2026-08-21. `needs-info` is in it,
#: which is why the blocked path can rely on the label existing — and why the "team
#: defines no such label" branch needs a fake that can drop it.
TEAM_LABELS: dict[str, str] = {
    "wontfix": "lbl-wontfix",
    "ready-for-human": "lbl-ready-for-human",
    "ready-for-agent": "lbl-ready-for-agent",
    "needs-info": "lbl-needs-info",
    "needs-triage": "lbl-needs-triage",
    "Bug": "lbl-bug",
    "Feature": "lbl-feature",
}


@dataclass
class FakeLinear:
    """Records the writes instead of making them, so idempotency is observable."""

    issue_data: Issue
    state: str = "Todo"
    comments: list[str] = field(default_factory=list)
    state_changes: list[str] = field(default_factory=list)
    #: Label *names* currently on the issue. Seeded from the issue so the fake starts
    #: where Linear would.
    labels: list[str] | None = None
    available_labels: dict[str, str] = field(default_factory=lambda: dict(TEAM_LABELS))
    #: Set to raise from every write, to prove an announcement failure cannot mask the
    #: block that caused it.
    fail_with: Exception | None = None
    #: What the poller's `ready_issues` query answers. Empty by default, so a test that
    #: does not opt in cannot accidentally have the tick claim new work.
    ready: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.labels is None:
            self.labels = list(self.issue_data.labels)

    def _check(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    def ready_issues(self, team_keys: Sequence[str]) -> list[str]:
        return list(self.ready)

    def issue(self, identifier: str) -> Issue:
        return self.issue_data

    def issue_uuid(self, identifier: str) -> tuple[str, str]:
        self._check()
        return "issue-uuid", self.state

    def workflow_state_id(self, team_id: str, name: str) -> str:
        return f"state-{name.lower().replace(' ', '-')}"

    def comment_marker_present(self, identifier: str, marker: str) -> bool:
        return any(marker in body for body in self.comments)

    def add_comment(self, issue_uuid: str, body: str) -> str:
        self._check()
        self.comments.append(body)
        return f"comment-{len(self.comments)}"

    def move_state(self, issue_uuid: str, state_id: str) -> str:
        self._check()
        # Derived from the id rather than hardcoded: `cancel` moves a ticket *back*, so
        # a fake that always answers "In Progress" would report success either way.
        self.state = state_id.removeprefix("state-").replace("-", " ").title()
        self.state_changes.append(state_id)
        return self.state

    # -- labels -------------------------------------------------------------------

    def team_labels(self, team_id: str) -> dict[str, str]:
        self._check()
        return dict(self.available_labels)

    def issue_labels(self, identifier: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        self._check()
        names = tuple(self.labels or ())
        return "issue-uuid", tuple(f"lbl-{n.lower()}" for n in names), names

    def set_labels(self, issue_uuid: str, label_ids: Sequence[str]) -> tuple[str, ...]:
        self._check()
        by_id = {v: k for k, v in self.available_labels.items()}
        # Ids the team table does not know are the ones `issue_labels` synthesised from
        # a name, so fall back to un-slugging rather than dropping the label.
        self.labels = [by_id.get(i, i.removeprefix("lbl-")) for i in label_ids]
        return tuple(self.labels)


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _seed_vendored_review_tree(worktree: Path) -> None:
    """The parts of layer A a real review reads out of the worktree: the two Tier-1
    frames, the findings schema, and the portable Tier-2 skill.

    Seeded into the shared `project_repo` fixture so a `--clone` run inherits them in its
    private copy too — the review reads them out of `ctx.worktree`, which for a clone is a
    fresh host checkout the fetch_back made, so they have to be in the repo, not patched in
    per test.
    """
    vendor = worktree / ".agents/vendor/harness"
    for name in ("ticket-readiness", "diagnose-and-hand-off", "refresh-execution-authority"):
        target = vendor / "docs/agents" / f"{name}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {name}\nNoninteractive workflow contract.\n")
    for agent in ("standards-reviewer", "spec-checker"):
        path = vendor / "agents" / f"{agent}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {agent}\n\nreview frame body", encoding="utf-8")
    schema = vendor / "schema" / "review-findings.schema.json"
    schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_text(
        json.dumps({"type": "object", "properties": {"findings": {"type": "array"}}}),
        encoding="utf-8",
    )
    skill = vendor / "skills" / "full-review" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("# full review\n\nthe portable nine-axis skill", encoding="utf-8")


@pytest.fixture
def project_repo(tmp_path: Path) -> Path:
    """A real git repository with a real `origin`, because `repo.py` shells out to git.

    Faking git would be faking the thing most likely to surprise us: worktree creation,
    the branch-collision refusal and base-ref resolution are all git behaviours, not
    factory behaviours.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "v2", str(origin)], check=True, capture_output=True
    )

    work = tmp_path / "python-harness"
    work.mkdir()
    git(work, "init", "-b", "v2")
    git(work, "config", "user.email", "factory@example.invalid")
    git(work, "config", "user.name", "factory tests")
    (work / "harness.config.json").write_text(
        json.dumps(
            {
                "name": "python-harness",
                "tracker": {"team": "BAC"},
                "gates": [
                    {
                        "name": "ruff check",
                        "kind": "lint",
                        "run": ["uv", "run", "ruff", "check", "."],
                    },
                    {"name": "pytest", "kind": "test", "run": ["uv", "run", "pytest"]},
                ],
                "hooks": {
                    "protected": [
                        {"glob": "uv.lock", "why": "regenerate with `uv lock`, never hand-edit"}
                    ],
                    "secretVars": ["LINEAR_API_KEY", "GH_TOKEN"],
                },
            }
        )
    )
    (work / "README.md").write_text("# python-harness\n")
    # Real consumers exclude run artifacts. Staging a source change must not also
    # commit the fake sandbox's prompts and evidence and trip review's size trigger.
    (work / ".gitignore").write_text(".factory/\n")
    _seed_vendored_review_tree(work)
    git(work, "add", "-A")
    git(work, "commit", "-m", "initial")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-u", "origin", "v2")
    return work


# --------------------------------------------------------------------------------
# Shared ticket, registry and context — used by test_pipeline and test_phase3
# --------------------------------------------------------------------------------

HOME = Path(__file__).resolve().parents[2]

SPEC = (
    "A search API over a corpus of support documents. A support agent sends a query and "
    "receives passages, each with a citation of file name and page number, and a relevance "
    "score. The system returns passages; it does not write an answer. Internal documents "
    "are excluded from every search by default."
)

TICKET = Issue(
    identifier="BAC-4",
    title="Application skeleton: Settings, structured logging, app factory",
    description=(
        "## What to build\n\nAn app that reads config in one place.\n\n"
        "## Acceptance criteria\n\n- [ ] Settings is the only reader of the environment\n"
        "- [ ] The health endpoint returns 200\n"
    ),
    url="https://linear.app/development-jchen/issue/BAC-4",
    state_name="Todo",
    state_type="unstarted",
    team_key="BAC",
    team_id="team-uuid",
    labels=("ready-for-agent", "Feature"),
    parent_identifier="BAC-2",
    parent_title="Search internal support documents and return cited passages",
    parent_description=SPEC,
    comments=(("James", "2026-08-20", "start with the health endpoint"),),
    siblings=(("BAC-3", "Todo", "Approve the dependency set"),),
)


def _registry_toml(project: Path, vault: Path, *, clone: bool = False) -> str:
    return f"""
[vault]
path = "{vault}"
write_allowlist = ["Project Learnings/**", "_VAULT_INDEX.md"]
snapshot_exclude = [".obsidian"]

[defaults]
worktree_subdir = ".factory/worktrees"
disk_min_free_gb = 0
deny_network = ["mcp.linear.app"]

[defaults.planning]
auto = false

[defaults.timeouts_seconds]
implementing = 30

[projects.python-harness]
team = "BAC"
path = "{project}"
remote = "{project}"
base_branch = "v2"
stack = "python"
template = ""
build_sandbox = "factory-build-python-harness"
review_sandbox = "factory-review-python-harness"
vault_mount = "rw"
requires_clone = {str(clone).lower()}
# What `config/projects.toml` carries for this project, for the same measured reason.
acknowledged_env_credentials = ["GH_TOKEN"]

[projects.python-harness.env]
UV_PROJECT_ENVIRONMENT = "/home/agent/venvs/python-harness"
"""


@pytest.fixture
def ctx(tmp_path: Path, project_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Context:
    return _make_ctx(tmp_path, project_repo, monkeypatch, clone=False)


@pytest.fixture
def clone_ctx(tmp_path: Path, project_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Context:
    """The same project, the same ticket, the same fakes — `requires_clone` and nothing else.

    One variable at a time, because that is the only way a clone-path failure is readable:
    anything the two fixtures did differently would otherwise be a candidate explanation.
    """
    return _make_ctx(tmp_path, project_repo, monkeypatch, clone=True)


def _make_ctx(
    tmp_path: Path, project_repo: Path, monkeypatch: pytest.MonkeyPatch, *, clone: bool
) -> Context:
    home = tmp_path / "factory-home"
    (home / "schemas").mkdir(parents=True)
    (home / "schemas/handoff_result.schema.json").write_text(
        (HOME / "schemas/handoff_result.schema.json").read_text()
    )
    (home / "schemas" / "implement_result.schema.json").write_text(
        (HOME / "schemas" / "implement_result.schema.json").read_text()
    )
    (home / "schemas" / "gate_report.schema.json").write_text(
        (HOME / "schemas" / "gate_report.schema.json").read_text()
    )
    (home / "config").mkdir()

    vault = tmp_path / "vault"
    (vault / "Project Learnings").mkdir(parents=True)
    (vault / "_VAULT_INDEX.md").write_text("index\n")

    registry_path = home / "config" / "projects.toml"
    registry_path.write_text(_registry_toml(project_repo, vault, clone=clone))

    skill = tmp_path / "implement" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\nname: implement\ndisable-model-invocation: true\n---\n\n"
        "Implement the work described by the user in the spec or tickets.\n"
    )
    monkeypatch.setattr(implement_step, "IMPLEMENT_SKILL", skill)
    monkeypatch.setattr(
        sandbox_step, "_vendor_sync_path", lambda: Path("/nonexistent/vendor_sync.py")
    )
    # `vendor_check` returns False for a missing script, which would fail preflight for
    # a reason unrelated to what this test is about. The real check has its own test.
    monkeypatch.setattr(sandbox_step, "vendor_check", lambda target, script: (True, "OK (stubbed)"))

    store = Store(home / "state" / "factory.db")
    run = store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")
    store.acquire_lease(run.id, ttl_seconds=600)

    registry = load_registry(registry_path)
    sandbox = FakeSandbox(clone_root=tmp_path / "vm") if clone else FakeSandbox()
    return Context(
        home=home,
        registry=registry,
        routing=load_routing(HOME / "config" / "models.toml"),
        store=store,
        linear=FakeLinear(TICKET),  # type: ignore[arg-type]
        sandbox=sandbox,
        agent=CodexAdapter(),
        project=registry.projects["python-harness"],
        run=store.run_by_id(run.id),  # type: ignore[arg-type]
        issue=TICKET,
        harness=load_harness_config(project_repo),
    )
