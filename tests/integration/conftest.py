"""Fakes for the whole pipeline — §21.3.

The point of this suite is that the entire state machine runs in-process with no `sbx`,
no Docker, no network and no model call. If a step can only be exercised against the
real thing, that step has too much of the real thing in it.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from factory.intake.linear import Issue
from factory.sandbox.base import Completed, RunHandle, RunResult, RunStatus, SandboxSpec

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
    #: What `sbx inspect --json` reports under `secrets`. Empty is the shape a correctly
    #: provisioned host produces; the tests set it to the shape measured on 2026-08-21.
    secrets: list[dict[str, str]] = field(default_factory=list)

    def exists(self, name: str) -> bool:
        return any(spec.name == name for spec in self.created)

    def inspect(self, name: str) -> dict[str, Any]:
        return {
            "name": name,
            "state": "running",
            "secrets": list(self.secrets),
            "workspace": "",
        }

    def ensure(self, spec: SandboxSpec) -> None:
        if not self.exists(spec.name):
            self.created.append(spec)

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
        return Completed(tuple(argv), 0, "/usr/bin/node\n/usr/bin/git\nv22.22.1", "")

    def exec_detached(self, handle: RunHandle, script: str, env: Mapping[str, str]) -> None:
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

    def kill_agent(self, name: str) -> None:
        return None

    def stop(self, name: str) -> None:
        return None

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
