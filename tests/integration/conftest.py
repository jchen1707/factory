"""Fakes for the whole pipeline — §21.3.

The point of this suite is that the entire state machine runs in-process with no `sbx`,
no Docker, no network and no model call. If a step can only be exercised against the
real thing, that step has too much of the real thing in it.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from factory.agent.codex import CodexAdapter
from factory.harness import load_harness_config
from factory.intake.linear import Issue
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
        clone = self.clone_dir(handle.sandbox)
        if clone is not None:
            # The agent's commits land in the clone and nowhere else, which is what makes
            # `clone.fetch_back` a real fetch rather than a formality.
            self._commit_in_clone(clone)
        directory = handle.attempt_dir
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in self.events) + "\n"
        )
        (directory / "stderr.log").write_text(self.stderr)
        (directory / "last-message.json").write_text(json.dumps(self.result))
        (directory / "heartbeat").write_text("0")
        (directory / "exit").write_text(str(self.exit_code))

    def poll(self, handle: RunHandle) -> RunStatus:
        return RunStatus.EXITED if (handle.attempt_dir / "exit").exists() else RunStatus.RUNNING

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

    def kill_agent(self, name: str) -> None:
        return None

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

    def __post_init__(self) -> None:
        if self.labels is None:
            self.labels = list(self.issue_data.labels)

    def _check(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

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
