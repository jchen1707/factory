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

    def exists(self, name: str) -> bool:
        return any(spec.name == name for spec in self.created)

    def inspect(self, name: str) -> dict[str, Any]:
        return {"name": name, "state": "running", "secrets": [], "workspace": ""}

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


@dataclass
class FakeLinear:
    """Records the writes instead of making them, so idempotency is observable."""

    issue_data: Issue
    state: str = "Todo"
    comments: list[str] = field(default_factory=list)
    state_changes: list[str] = field(default_factory=list)

    def issue(self, identifier: str) -> Issue:
        return self.issue_data

    def issue_uuid(self, identifier: str) -> tuple[str, str]:
        return "issue-uuid", self.state

    def workflow_state_id(self, team_id: str, name: str) -> str:
        return f"state-{name.lower().replace(' ', '-')}"

    def comment_marker_present(self, identifier: str, marker: str) -> bool:
        return any(marker in body for body in self.comments)

    def add_comment(self, issue_uuid: str, body: str) -> str:
        self.comments.append(body)
        return f"comment-{len(self.comments)}"

    def move_state(self, issue_uuid: str, state_id: str) -> str:
        self.state = "In Progress"
        self.state_changes.append(state_id)
        return self.state


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
