"""§21.3 — the whole of Phase 1, in-process, against fakes.

`approved -> claimed -> context_loaded -> sandbox_creating -> sandbox_ready ->
worktree_ready -> implementing -> verifying`, with no `sbx`, no network and no model
call. This is the suite that runs on every commit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.agent.codex import CodexAdapter
from factory.harness import load_harness_config
from factory.intake.linear import Issue
from factory.machine import Blocked, Resumable, State
from factory.registry import load_registry
from factory.routing import load_routing
from factory.steps import Context
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import implement as implement_step
from factory.steps import sandbox as sandbox_step
from factory.steps import worktree as worktree_step
from factory.store import Store
from tests.integration.conftest import FakeLinear, FakeSandbox, git


def _fake(ctx: Context) -> FakeSandbox:
    """The context types its adapter as the protocol; the tests need the fake's knobs."""
    assert isinstance(ctx.sandbox, FakeSandbox)
    return ctx.sandbox


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


def _registry_toml(project: Path, vault: Path) -> str:
    return f"""
[vault]
path = "{vault}"
write_allowlist = ["Project Learnings/**", "_VAULT_INDEX.md"]
snapshot_exclude = [".obsidian"]

[defaults]
worktree_subdir = ".factory/worktrees"
disk_min_free_gb = 0

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

[projects.python-harness.env]
UV_PROJECT_ENVIRONMENT = "/home/agent/venvs/python-harness"
"""


@pytest.fixture
def ctx(tmp_path: Path, project_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Context:
    home = tmp_path / "factory-home"
    (home / "schemas").mkdir(parents=True)
    (home / "schemas" / "implement_result.schema.json").write_text(
        (HOME / "schemas" / "implement_result.schema.json").read_text()
    )
    (home / "config").mkdir()

    vault = tmp_path / "vault"
    (vault / "Project Learnings").mkdir(parents=True)
    (vault / "_VAULT_INDEX.md").write_text("index\n")

    registry_path = home / "config" / "projects.toml"
    registry_path.write_text(_registry_toml(project_repo, vault))

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
    return Context(
        home=home,
        registry=registry,
        routing=load_routing(HOME / "config" / "models.toml"),
        store=store,
        linear=FakeLinear(TICKET),  # type: ignore[arg-type]
        sandbox=FakeSandbox(),
        agent=CodexAdapter(),
        project=registry.projects["python-harness"],
        run=store.run_by_id(run.id),  # type: ignore[arg-type]
        issue=TICKET,
        harness=load_harness_config(project_repo),
    )


def _drive(ctx: Context) -> None:
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)
    implement_step.run(ctx)


def test_a_clean_run_reaches_verifying(ctx: Context) -> None:
    _drive(ctx)
    assert ctx.run.state is State.VERIFYING
    assert ctx.run.branch == "feat/BAC-4-application-skeleton-settings-structured"
    assert ctx.run.base_ref == "origin/v2"

    # Every hop is recorded with its actor, and none of them was a human.
    hops = [
        (row["from_state"], row["to_state"], row["actor"])
        for row in ctx.store.transitions(ctx.run.id)
    ]
    assert [hop[1] for hop in hops] == [
        "claimed",
        "context_loaded",
        "sandbox_creating",
        "sandbox_ready",
        "worktree_ready",
        "implementing",
        "verifying",
    ]
    assert {hop[2] for hop in hops} == {"auto"}


def test_the_worktree_carries_the_context_and_the_run_record(ctx: Context) -> None:
    _drive(ctx)
    worktree = Path(ctx.run.worktree or "")
    for name in ("ticket.md", "spec.md", "breakdown.md", "comments.md"):
        assert (worktree / ".factory" / "context" / name).read_text().strip()
    record = json.loads((worktree / ".factory" / "run.json").read_text())
    assert record["ticket"] == "BAC-4"
    assert record["branch"] == ctx.run.branch


def test_the_attempt_directory_is_complete_and_hashed(ctx: Context) -> None:
    _drive(ctx)
    attempt = Path(ctx.run.worktree or "") / ".factory" / "run" / "1"
    for name in (
        "request.json",
        "prompt.md",
        "schema.json",
        "events.jsonl",
        "stderr.log",
        "last-message.json",
        "exit",
        "manifest.json",
    ):
        assert (attempt / name).exists(), name
    manifest = json.loads((attempt / "manifest.json").read_text())
    assert set(manifest["files"]) >= {"prompt.md", "events.jsonl", "last-message.json"}

    request = json.loads((attempt / "request.json").read_text())
    assert request["model"] == "gpt-5.6-sol"
    assert request["effort"] == "xhigh"
    assert len(request["implement_skill_sha256"]) == 64
    assert "--dangerously-bypass-hook-trust" in request["argv"]


def test_the_prompt_inlines_the_skill_rather_than_naming_it(ctx: Context) -> None:
    _drive(ctx)
    prompt = (Path(ctx.run.worktree or "") / ".factory" / "run" / "1" / "prompt.md").read_text()
    assert "Implement the work described by the user" in prompt
    assert "disable-model-invocation" not in prompt  # frontmatter stripped
    assert "/implement" not in prompt  # `codex exec` does not expand a slash command
    assert "tdd" in prompt  # the reachable skills are still named
    assert "Do not write to Linear" in prompt
    assert "harness.config.json" in prompt  # gates are read, never restated


def test_tokens_are_recorded_and_cost_is_null_not_zero(ctx: Context) -> None:
    _drive(ctx)
    tokens_in, tokens_out, usd = ctx.store.spend(ctx.run.id)
    assert (tokens_in, tokens_out) == (12000, 300)
    assert usd is None


def test_the_session_id_is_captured_for_the_resume_path(ctx: Context) -> None:
    _drive(ctx)
    assert ctx.store.session_id(ctx.run.id, 1, State.IMPLEMENTING) == "01a0-fake-thread"


def test_linear_is_written_once_and_reconciled_on_a_repeat(ctx: Context) -> None:
    claim_step.run(ctx)
    linear: FakeLinear = ctx.linear  # type: ignore[assignment]
    assert linear.state == "In Progress"
    assert len(linear.comments) == 1
    assert "factory:" in linear.comments[0]

    # F2's shape: the ledger already says confirmed, so a second pass performs nothing.
    ctx.store.record_transition(
        ctx.run.id, from_state=State.CLAIMED, to_state=State.RESUMABLE, actor="auto"
    )
    ctx.store.record_transition(
        ctx.run.id, from_state=State.RESUMABLE, to_state=State.IMPLEMENTING, actor="auto"
    )
    ctx.refresh()
    ctx.store.record_transition(
        ctx.run.id, from_state=State.IMPLEMENTING, to_state=State.RESUMABLE, actor="auto"
    )
    ctx.refresh()
    ctx.store._conn.execute("UPDATE runs SET state = 'approved' WHERE id = ?", (ctx.run.id,))
    ctx.refresh()
    claim_step.run(ctx)
    assert len(linear.comments) == 1
    assert len(linear.state_changes) == 1


def test_an_interrupted_claim_reconciles_instead_of_commenting_twice(ctx: Context) -> None:
    # F1: the ledger row is written before the call, and the process dies in between.
    linear: FakeLinear = ctx.linear  # type: ignore[assignment]
    marker = f"factory:{ctx.run.id}:0:claim"
    ctx.store.intend_effect(ctx.run.id, 0, "claim", "linear", "comment:claimed")
    linear.comments.append(f"<!-- {marker} --> written just before the crash")

    claim_step.run(ctx)
    assert len(linear.comments) == 1
    effect = ctx.store.find_effect(ctx.run.id, 0, "claim", "linear", "comment:claimed")
    assert effect is not None
    assert effect.status == "confirmed"


def test_a_schema_invalid_result_blocks_and_keeps_the_raw_file(ctx: Context) -> None:
    _fake(ctx).result = {"status": "implemented", "summary": "no required fields"}
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)
    with pytest.raises(Blocked) as caught:
        implement_step.run(ctx)
    assert caught.value.reason == "schema-invalid"
    assert ctx.run.state is State.IMPLEMENTING  # the state did not advance
    raw = Path(ctx.run.worktree or "") / ".factory" / "run" / "1" / "last-message.json"
    assert raw.exists()


def test_a_non_zero_exit_is_resumable_and_carries_the_stderr_tail(ctx: Context) -> None:
    _fake(ctx).exit_code = 1
    _fake(ctx).stderr = "traceback line\n" * 60
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)
    with pytest.raises(Resumable) as caught:
        implement_step.run(ctx)
    assert caught.value.reason == "agent-failed"
    assert "traceback line" in caught.value.detail


def test_a_preflight_that_cannot_produce_a_refusal_blocks(ctx: Context) -> None:
    # F20 / R1: everything runs, nothing enforces. The canary returning anything but
    # exit 2 means the write guard is not live, and a green run would prove nothing.
    _fake(ctx).canary_exit = 0
    claim_step.run(ctx)
    context_step.run(ctx)
    with pytest.raises(Blocked) as caught:
        sandbox_step.run(ctx)
    assert caught.value.reason == "enforcement-disabled"


def test_a_write_outside_the_vault_allowlist_blocks_the_run(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = ctx.registry.vault.path
    original = _fake(ctx).exec_detached

    def also_write_the_vault(handle, script, env):  # type: ignore[no-untyped-def]
        original(handle, script, env)
        (vault / "Upskilling").mkdir(exist_ok=True)
        (vault / "Upskilling" / "notes.md").write_text("the agent wrote here")

    monkeypatch.setattr(ctx.sandbox, "exec_detached", also_write_the_vault)

    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)
    with pytest.raises(Blocked) as caught:
        implement_step.run(ctx)
    assert caught.value.reason == "vault-write-outside-allowlist"
    assert "Upskilling/notes.md" in caught.value.detail
    # Both snapshots are kept, so a wrong write is reconstructible after the fact.
    assert (ctx.state_dir / "vault" / "before-1.json").exists()
    assert (ctx.state_dir / "vault" / "after-1.json").exists()


def test_a_write_inside_the_allowlist_is_fine(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = ctx.registry.vault.path
    original = _fake(ctx).exec_detached

    def also_distil(handle, script, env):  # type: ignore[no-untyped-def]
        original(handle, script, env)
        (vault / "Project Learnings" / "2026-08-20.md").write_text("what this run taught")

    monkeypatch.setattr(ctx.sandbox, "exec_detached", also_distil)
    _drive(ctx)
    assert ctx.run.state is State.VERIFYING


def test_the_factory_refuses_to_reuse_an_existing_remote_branch(ctx: Context) -> None:
    # The `origin/feat/BAC-4-application-skeleton` case, reproduced: a complete,
    # unmerged implementation sitting on the name the factory wants.
    branch = "feat/BAC-4-application-skeleton-settings-structured"
    git(ctx.project.path, "branch", branch)
    git(ctx.project.path, "push", "origin", branch)
    git(ctx.project.path, "branch", "-D", branch)

    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    with pytest.raises(Blocked) as caught:
        worktree_step.run(ctx)
    assert caught.value.reason == "branch-exists"


def test_the_sandbox_is_created_with_the_measured_settings(ctx: Context) -> None:
    _drive(ctx)
    spec = _fake(ctx).created[0]
    assert spec.name == "factory-build-python-harness"
    assert spec.static_mcp == ()  # §13.1: nothing to leak, nothing to misuse
    assert spec.template is None  # P0-3: the stock image already has node 22 and uv
    assert spec.env["UV_PROJECT_ENVIRONMENT"] == "/home/agent/venvs/python-harness"
    assert len(spec.workspaces) == 2  # the repo, and the vault read-write
    assert not any(w.readonly for w in spec.workspaces)


def test_a_second_writer_on_the_same_project_is_refused(ctx: Context) -> None:
    other = ctx.store.insert_run(linear_id="BAC-9", project="python-harness", team="BAC")
    ctx.store.record_transition(
        other.id, from_state=None, to_state=State.IMPLEMENTING, actor="auto"
    )
    with pytest.raises(Blocked) as caught:
        claim_step.run(ctx)
    assert caught.value.reason == "project-busy"


def test_a_run_that_lost_its_lease_cannot_advance(ctx: Context) -> None:
    ctx.store.release_lease(ctx.run.id)
    with pytest.raises(Blocked) as caught:
        claim_step.run(ctx)
    assert caught.value.reason == "lease-lost"
