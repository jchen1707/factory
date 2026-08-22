"""§21.3 — the whole of Phase 1 (and Phase 2's verify step), in-process, against fakes.

`approved -> claimed -> context_loaded -> sandbox_creating -> sandbox_ready ->
worktree_ready -> implementing -> verifying -> reviewing`, with no `sbx`, no network and
no model call. This is the suite that runs on every commit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from factory import repo
from factory.intake.linear import LinearError
from factory.machine import Blocked, Resumable, State
from factory.steps import Context
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import implement as implement_step
from factory.steps import sandbox as sandbox_step
from factory.steps import verify as verify_step
from factory.steps import worktree as worktree_step
from tests.integration.conftest import (
    GOOD_GATE_REPORT,
    GOOD_RESULT,
    HOME,
    TICKET,
    FakeLinear,
    FakeSandbox,
    git,
)


def _fake(ctx: Context) -> FakeSandbox:
    """The context types its adapter as the protocol; the tests need the fake's knobs."""
    assert isinstance(ctx.sandbox, FakeSandbox)
    return ctx.sandbox


def _drive(ctx: Context) -> None:
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)
    implement_step.run(ctx)
    verify_step.run(ctx)


def _to_verifying(ctx: Context) -> None:
    """Through the implement step only, so a test can shape the gate report before the
    verify step reads it."""
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)
    implement_step.run(ctx)
    assert ctx.run.state is State.VERIFYING


def _gates_json(ctx: Context) -> dict[str, Any]:
    return json.loads(
        (Path(ctx.run.worktree or "") / ".factory" / "run" / "1" / "gates.json").read_text()
    )


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((HOME / "tests" / "fixtures" / name).read_text())


def test_a_clean_run_reaches_reviewing(ctx: Context) -> None:
    _drive(ctx)
    assert ctx.run.state is State.REVIEWING
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
        "reviewing",
    ]
    assert {hop[2] for hop in hops} == {"auto"}

    # The gate report is kept as evidence beside the implement last-message.
    assert _gates_json(ctx)["verdict"] == "pass"


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
    # Against the shipped routing table rather than a copy of it: the property is that
    # the request carries what `models.toml` routes the builder to, and pinning the
    # values here means every effort change breaks a test that is not about effort.
    builder = ctx.routing.role("builder")
    assert request["model"] == builder.model
    assert request["effort"] == builder.effort
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
    assert ctx.run.state is State.REVIEWING


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


# --------------------------------------------------------------------------------
# §13.1's `blocked` row — the write the first real run did not make
# --------------------------------------------------------------------------------

#: Verbatim from `sbx inspect factory-build-python-harness --json`, 2026-08-21, seconds
#: after the factory created that sandbox itself. P0-5 predicted an empty array.
MEASURED_SECRETS = [
    {"name": "github", "source": "uploaded"},
    {"name": "mcpgateway", "source": "uploaded"},
]


def _block(ctx: Context, reason: str, detail: str) -> None:
    """`cli._block`, which is the only place a block is recorded."""
    from factory import cli

    cli._block(ctx, reason, detail)


def test_a_service_secret_in_the_vm_still_blocks_the_preflight(ctx: Context) -> None:
    _fake(ctx).secrets = MEASURED_SECRETS
    claim_step.run(ctx)
    context_step.run(ctx)
    with pytest.raises(Blocked) as caught:
        sandbox_step.run(ctx)
    assert caught.value.reason == "enforcement-disabled"
    assert "github" in caught.value.detail


def test_the_gateway_credential_alone_does_not_block_the_preflight(ctx: Context) -> None:
    # The measured shape once `github` is re-scoped out of global scope. Asserting an
    # empty `secrets` array here would make the preflight unsatisfiable on this host.
    _fake(ctx).secrets = [{"name": "mcpgateway", "source": "uploaded"}]
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    assert ctx.run.state is State.SANDBOX_READY


def test_the_build_sandbox_denies_the_mcp_gateway_endpoint(ctx: Context) -> None:
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    assert _fake(ctx).created[0].deny_network == ("mcp.linear.app",)


def test_a_block_comments_the_reason_and_labels_the_ticket(ctx: Context) -> None:
    claim_step.run(ctx)
    linear: FakeLinear = ctx.linear  # type: ignore[assignment]
    _block(ctx, "enforcement-disabled", "preflight could not prove the run was enforced")

    assert ctx.run.state is State.BLOCKED
    assert len(linear.comments) == 2  # the claim, then the block
    body = linear.comments[-1]
    assert "enforcement-disabled" in body
    assert "preflight could not prove" in body
    assert str(ctx.state_dir) in body  # §13.1: "reason and evidence path"
    assert "needs-info" in (linear.labels or [])
    # The label is not decorative: it is in BLOCKING_LABELS, so eligibility condition 7
    # now refuses to re-claim the ticket until a human takes it off.
    assert "ready-for-agent" in (linear.labels or [])  # and nothing else was clobbered


def test_a_repeated_block_does_not_comment_twice(ctx: Context) -> None:
    claim_step.run(ctx)
    linear: FakeLinear = ctx.linear  # type: ignore[assignment]
    _block(ctx, "enforcement-disabled", "same reason, second pass")
    _block(ctx, "enforcement-disabled", "same reason, second pass")
    assert len(linear.comments) == 2
    assert (linear.labels or []).count("needs-info") == 1


def test_a_linear_outage_while_announcing_does_not_mask_the_block(ctx: Context) -> None:
    # The reason a run stopped is the most useful thing it produced. Replacing it with
    # a transport error would be the worst possible trade.
    claim_step.run(ctx)
    linear: FakeLinear = ctx.linear  # type: ignore[assignment]
    linear.fail_with = LinearError("linear unreachable: timed out")
    _block(ctx, "enforcement-disabled", "the block still has to be recorded")
    assert ctx.run.state is State.BLOCKED
    assert ctx.run.blocked_reason == "enforcement-disabled"


def test_a_block_is_announced_even_where_the_team_has_no_such_label(ctx: Context) -> None:
    claim_step.run(ctx)
    linear: FakeLinear = ctx.linear  # type: ignore[assignment]
    linear.available_labels = {"Feature": "lbl-feature"}
    _block(ctx, "enforcement-disabled", "no needs-info label exists on this team")
    assert "enforcement-disabled" in linear.comments[-1]
    assert "needs-info" not in (linear.labels or [])


# --------------------------------------------------------------------------------
# `cancel` — the rollback `cmd_run` promises
# --------------------------------------------------------------------------------


def test_cancel_puts_the_ticket_back_where_the_factory_found_it(ctx: Context) -> None:
    from factory import cli

    claim_step.run(ctx)
    linear: FakeLinear = ctx.linear  # type: ignore[assignment]
    _block(ctx, "enforcement-disabled", "so the ticket carries needs-info too")
    assert linear.state == "In Progress"
    assert "needs-info" in (linear.labels or [])

    cli._restore_tracker_for_rerun(linear, "BAC-4")  # type: ignore[arg-type]

    # Eligibility conditions 2 and 7, which is the whole point: without both halves,
    # `factory run BAC-4` refuses the ticket it was just told it could retry.
    assert linear.state == "Todo"
    assert "needs-info" not in (linear.labels or [])
    assert "ready-for-agent" in (linear.labels or [])


def test_cancel_frees_the_ticket_locally_and_not_only_in_linear(ctx: Context) -> None:
    """The other half of the rollback, and the half that was missing.

    `test_cancel_puts_the_ticket_back_where_the_factory_found_it` covers eligibility
    conditions 2 and 7 — the Linear side — but `factory run` also refuses a ticket whose
    run row is not `approved`. Against a v1 database that row was `cancelled` forever,
    so a real `factory cancel BAC-4 && factory run BAC-4` got all the way past all ten
    eligibility conditions and then stopped at `already at cancelled (attempt 0)`.

    Asserting `APPROVED` here is asserting exactly `cmd_run`'s guard.
    """
    claim_step.run(ctx)
    _block(ctx, "enforcement-disabled", "the block that made a rerun necessary")
    ctx.store.record_transition(
        ctx.run.id,
        from_state=ctx.store.run_by_id(ctx.run.id).state,  # type: ignore[union-attr]
        to_state=State.CANCELLED,
        actor="human",
        rule="abandon-is-james",
    )
    ctx.store.release_lease(ctx.run.id)

    rerun = ctx.store.insert_run(linear_id="BAC-4", project="python-harness", team="BAC")

    assert rerun.id != ctx.run.id
    assert rerun.state is State.APPROVED
    assert ctx.store.acquire_lease(rerun.id, ttl_seconds=600)
    # The abandoned run keeps its evidence; the rerun starts with a clean ledger, so the
    # claim's Linear writes are performed again rather than reconciled away.
    assert ctx.store.effects(ctx.run.id) != []
    assert ctx.store.effects(rerun.id) == []


def test_cancel_does_not_overwrite_a_state_a_human_set(ctx: Context) -> None:
    from factory import cli

    linear: FakeLinear = ctx.linear  # type: ignore[assignment]
    linear.state = "In Review"
    cli._restore_tracker_for_rerun(linear, "BAC-4")  # type: ignore[arg-type]
    assert linear.state == "In Review"
    assert linear.state_changes == []


# --------------------------------------------------------------------------------
# defect 6 — the debris a run dies before recording
# --------------------------------------------------------------------------------

#: What `plan_branch` produces for `TICKET`. Spelled out rather than derived, because a
#: test that computes the name the way the code does cannot catch the code computing it
#: wrongly.
BRANCH = "feat/BAC-4-application-skeleton-settings-structured"


def _cancel(ctx: Context, monkeypatch: pytest.MonkeyPatch, ticket: str = "BAC-4") -> int:
    """`factory cancel <ticket>`, with only the two adapters that leave this machine
    faked. The registry on disk, the store the fixture wrote and git are all real."""
    from factory import cli

    monkeypatch.setenv("FACTORY_HOME", str(ctx.home))
    monkeypatch.setattr(cli, "SbxAdapter", FakeSandbox)
    monkeypatch.setattr(cli, "LinearClient", lambda: FakeLinear(TICKET))
    return cli.cmd_cancel(argparse.Namespace(ticket=ticket, reason="testing the rollback"))


def _debris(ctx: Context) -> Path:
    """Exactly what a run that dies inside `git worktree add` leaves behind: an
    unregistered directory holding nothing but `.factory/run/1/`, and an empty branch."""
    path = ctx.project.worktree_path(ctx.registry.defaults.worktree_subdir, "BAC-4")
    (path / ".factory" / "run" / "1").mkdir(parents=True)
    git(ctx.project.path, "branch", BRANCH, "origin/v2")
    return path


def test_cancel_clears_the_debris_a_run_never_recorded(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defect 6. The run row is empty because `worktree.py` writes `worktree` and
    `branch` only after `git worktree add` returns — so the window in which the command
    can fail is exactly the window in which cancel is blind to its own debris.

    The assertion that matters is the last one: against the unfixed code `add_worktree`
    raises `fatal: ... already exists`, which is how this arrived twice on 2026-08-21.
    """
    path = _debris(ctx)
    assert not ctx.run.worktree
    assert not ctx.run.branch

    assert _cancel(ctx, monkeypatch) == 0

    assert not path.exists()
    repo.add_worktree(ctx.project.path, path, BRANCH, "origin/v2")


def test_cancel_keeps_a_branch_that_carries_commits(
    ctx: Context,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Rule 2, and the half that makes this fix safe rather than worse than the gap.

    A local branch naming the ticket is not necessarily the factory's: this one is the
    human's own attempt, with a commit on it. Deleting it would turn a rollback gap into
    a way to lose an implementation.
    """
    human = "fix/BAC-4-james-had-a-go"
    scratch = tmp_path / "scratch"
    git(ctx.project.path, "worktree", "add", "-b", human, str(scratch), "origin/v2")
    (scratch / "notes.md").write_text("half a fix\n")
    git(scratch, "add", "-A")
    git(scratch, "commit", "-m", "BAC-4: half a fix")
    git(ctx.project.path, "worktree", "remove", str(scratch))
    _debris(ctx)

    assert _cancel(ctx, monkeypatch) == 0

    assert human in git(ctx.project.path, "branch", "--list", human)
    assert git(ctx.project.path, "rev-list", "--count", f"origin/v2..{human}") == "1"
    assert human in capsys.readouterr().out


def test_cancel_keeps_a_worktree_directory_holding_work(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rule 1. An unregistered directory is removed only when it holds nothing but the
    factory's own scaffolding. Anything else is somebody's work, and `rm -rf` on an
    arbitrary tree is F15."""
    path = _debris(ctx)
    (path / "src").mkdir()
    (path / "src" / "main.py").write_text("the agent got this far\n")

    assert _cancel(ctx, monkeypatch) == 0

    assert (path / "src" / "main.py").read_text() == "the agent got this far\n"
    assert str(path) in capsys.readouterr().out


def test_cancel_leaves_another_tickets_branch_alone(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deriving a branch from a ticket means matching names, and `feat/BAC-40-...`
    contains `BAC-4`. An empty, unpushed branch is exactly the shape this cleanup
    deletes, so the identifier match has to be a word boundary rather than a substring
    or cancelling one ticket would quietly remove another's branch."""
    neighbour = "feat/BAC-40-a-different-ticket"
    git(ctx.project.path, "branch", neighbour, "origin/v2")
    _debris(ctx)

    assert _cancel(ctx, monkeypatch) == 0

    assert neighbour in git(ctx.project.path, "branch", "--list", neighbour)
    assert BRANCH not in git(ctx.project.path, "branch", "--list", BRANCH)


# --------------------------------------------------------------------------------
# Phase 2 — the verify step (§15.1's third signal)
# --------------------------------------------------------------------------------


def test_the_verify_step_invokes_the_report_hook_by_path_not_a_gate_name(
    ctx: Context,
) -> None:
    # The factory holds no gate command. The argv names the vendored report hook and
    # `--json`; it does not name ruff, mypy, pytest or any other gate.
    _to_verifying(ctx)
    verify_step.run(ctx)
    report_calls = [
        argv for _name, argv in _fake(ctx).sync_calls if any("gate_report.mjs" in a for a in argv)
    ]
    assert len(report_calls) == 1
    argv = report_calls[0]
    assert "--json" in argv
    assert ".agents/vendor/harness/hooks/gate_report.mjs" in argv
    # No gate name leaks into the invocation.
    assert not any(token in argv for token in ("ruff", "mypy", "pytest"))


def test_the_verify_step_passes_the_run_base_ref_so_post_commit_gates_run(
    ctx: Context,
) -> None:
    # The factory commits the agent's work before it verifies, so the working tree is
    # clean: `gate_report.mjs`'s default `git status --porcelain` "did this app change?"
    # check sees nothing and every gate would come back `skipped_unchanged` regardless of
    # what the change touched. The factory passes `--base <run.base_ref>` so the report's
    # `gatedChangeSince` reads `git diff <base>..HEAD` against the committed change instead.
    # On the unfixed code `--base` is absent and the assertion below fails — which is how
    # the BAC-5 real run blocked on evidence-mismatch even though its gates had passed.
    _to_verifying(ctx)
    assert ctx.run.base_ref == "origin/v2"  # set at worktree creation
    verify_step.run(ctx)
    report_calls = [
        argv for _name, argv in _fake(ctx).sync_calls if any("gate_report.mjs" in a for a in argv)
    ]
    assert len(report_calls) == 1
    argv = report_calls[0]
    assert "--base" in argv
    # The base ref follows the flag as its value, not glued with `=`.
    assert argv[argv.index("--base") + 1] == "origin/v2"


def test_a_pass_report_advances_to_reviewing(ctx: Context) -> None:
    _to_verifying(ctx)
    # The default fake report is the all-pass one; exercise it explicitly anyway.
    _fake(ctx).gate_report = dict(GOOD_GATE_REPORT)
    verify_step.run(ctx)
    assert ctx.run.state is State.REVIEWING
    assert _gates_json(ctx)["verdict"] == "pass"


def test_a_fail_report_loops_back_to_implementing(ctx: Context) -> None:
    _to_verifying(ctx)
    _fake(ctx).gate_report = _fixture("gate-report-fail.json")
    verify_step.run(ctx)
    assert ctx.run.state is State.IMPLEMENTING  # loop back for a real failure
    gates = _gates_json(ctx)
    assert gates["verdict"] == "fail"
    assert next(g for g in gates["gates"] if g["name"] == "pytest")["status"] == "fail"


def test_an_incomplete_report_blocks_without_evidence_mismatch(ctx: Context) -> None:
    # A gate whose binary is absent comes back `unavailable` → `verdict: incomplete`. The
    # agent did not claim that gate, so this is an environment problem (blocked), not a
    # disagreement with the agent's claim (evidence-mismatch).
    _to_verifying(ctx)
    _fake(ctx).gate_report = _fixture("gate-report-unavailable.json")
    with pytest.raises(Blocked) as caught:
        verify_step.run(ctx)
    assert caught.value.reason == "gates-incomplete"
    assert "evidence-mismatch" not in caught.value.detail
    assert "playwright smoke" in caught.value.detail  # the unavailable gate is named


def test_a_claimed_gate_the_report_omits_is_evidence_mismatch(ctx: Context) -> None:
    _to_verifying(ctx)
    report = json.loads(json.dumps(GOOD_GATE_REPORT))
    # The agent claimed mypy; the report does not show it as pass or fail.
    report["gates"] = [g for g in report["gates"] if g["name"] != "mypy"]
    _fake(ctx).gate_report = report
    with pytest.raises(Blocked) as caught:
        verify_step.run(ctx)
    assert caught.value.reason == "evidence-mismatch"
    assert "mypy" in caught.value.detail


def test_a_claimed_gate_the_report_marks_unavailable_is_evidence_mismatch(
    ctx: Context,
) -> None:
    # The absent-binary case, but the agent *claimed* the gate — so the disagreement
    # outranks the incompleteness. This is the distinction the §15.1 cross-check draws.
    _to_verifying(ctx)
    report = json.loads(json.dumps(GOOD_GATE_REPORT))
    for gate in report["gates"]:
        if gate["name"] == "mypy":
            gate.update(
                {
                    "status": "unavailable",
                    "exit": None,
                    "durationMs": None,
                    "outputTail": "spawn mypy ENOENT",
                }
            )
    report["verdict"] = "incomplete"
    _fake(ctx).gate_report = report
    with pytest.raises(Blocked) as caught:
        verify_step.run(ctx)
    assert caught.value.reason == "evidence-mismatch"


def test_a_monorepo_report_with_a_skipped_app_advances(ctx: Context) -> None:
    # Two apps: the turn touched app-a (its gates pass) and not app-b (its gates come back
    # `skipped_unchanged`). The agent claimed only app-a's gates, so there is no mismatch
    # and the pass verdict advances the run.
    _to_verifying(ctx)
    _fake(ctx).gate_report = _fixture("gate-report-monorepo.json")
    verify_step.run(ctx)
    assert ctx.run.state is State.REVIEWING
    gates = _gates_json(ctx)
    assert len(gates["targets"]) == 2
    statuses = {g["status"] for g in gates["gates"]}
    assert "skipped_unchanged" in statuses
    assert gates["verdict"] == "pass"


def test_a_command_string_gate_claim_cross_checks_by_name(ctx: Context) -> None:
    # The real agent reports `gates_run` as the commands it ran with the outcome appended
    # ("uv run ruff check . — passed"), not the bare gate names the report uses
    # ("ruff check"). The cross-check must resolve a claim to the report gate whose name
    # it contains, or every real run blocks on evidence-mismatch regardless of whether
    # the gates passed. This is the bug the BAC-3 real run surfaced: the fakes all used
    # bare names that matched exactly, so it was invisible to the suite. On the unfixed
    # exact-match cross-check this raises evidence-mismatch and never reaches reviewing.
    _fake(ctx).result = {
        **GOOD_RESULT,
        "gates_run": [
            "uv run ruff check . — passed",
            "uv run mypy — passed",
            "uv run pytest — 158 passed, 3 deselected",
        ],
    }
    _to_verifying(ctx)
    _fake(ctx).gate_report = dict(GOOD_GATE_REPORT)  # ruff check, mypy, pytest — all pass
    verify_step.run(ctx)
    assert ctx.run.state is State.REVIEWING


def test_an_integration_command_claim_matches_the_longer_gate_name(ctx: Context) -> None:
    # "uv run pytest -m integration — 3 passed" contains both "pytest" and
    # "pytest -m integration". Longest-name-first must pair it with the integration gate,
    # not the bare pytest gate — otherwise a claim for a skipped integration gate would
    # silently cross-check against the passing pytest gate and the over-claim would be
    # missed. Here the integration gate is `skipped_unchanged`, so the claim is an
    # over-claim and must block as evidence-mismatch.
    _fake(ctx).result = {**GOOD_RESULT, "gates_run": ["uv run pytest -m integration — 3 passed"]}
    _to_verifying(ctx)
    _fake(ctx).gate_report = {
        "schemaVersion": 1,
        "root": "/worktree",
        "targets": [{"name": "python-harness", "dir": "."}],
        "missingApps": [],
        "gates": [
            {
                "name": "pytest",
                "kind": "test",
                "status": "pass",
                "exit": 0,
                "durationMs": 100,
                "caveat": None,
                "when": None,
                "outputTail": "",
            },
            {
                "name": "pytest -m integration",
                "kind": "integration",
                "status": "skipped_unchanged",
                "exit": None,
                "durationMs": None,
                "caveat": None,
                "when": None,
                "outputTail": "",
            },
        ],
        "verdict": "pass",
    }
    with pytest.raises(Blocked) as caught:
        verify_step.run(ctx)
    assert caught.value.reason == "evidence-mismatch"
    assert "pytest -m integration" in caught.value.detail


def test_the_report_is_validated_before_the_verdict_is_trusted(ctx: Context) -> None:
    _to_verifying(ctx)
    # A report that drops a required field on a gate entry is not trusted, even with a
    # `pass` verdict and a clean exit code.
    report = json.loads(json.dumps(GOOD_GATE_REPORT))
    del report["gates"][0]["status"]
    report["verdict"] = "pass"
    _fake(ctx).gate_report = report
    with pytest.raises(Blocked) as caught:
        verify_step.run(ctx)
    assert caught.value.reason == "schema-invalid"
    assert ctx.run.state is State.VERIFYING  # the state did not advance


def test_a_non_json_report_blocks(ctx: Context) -> None:
    _to_verifying(ctx)
    # A clean exit code and a non-JSON stdout: the verdict is never read, because the
    # document never parsed. This is the path the schema-invalid guard exists for.
    _fake(ctx).gate_report_raw_stdout = "not json at all"
    with pytest.raises(Blocked) as caught:
        verify_step.run(ctx)
    assert caught.value.reason == "schema-invalid"
    assert ctx.run.state is State.VERIFYING


def test_a_rejected_report_leaves_its_raw_streams_on_disk(ctx: Context) -> None:
    _to_verifying(ctx)
    # The shape a real run produced (BAC-4, run 6e2681471d4c485c): one complete report
    # followed by a line of something else, which `json.loads` rejects as `Extra data`.
    # Nobody can say what that trailing line was, because the block discarded the stream
    # it arrived on — so the evidence is what this test is about, not the block.
    _fake(ctx).gate_report_raw_stdout = '{"schemaVersion": 1, "verdict": "pass"}\ntrailing\n'
    with pytest.raises(Blocked) as caught:
        verify_step.run(ctx)
    assert caught.value.reason == "schema-invalid"

    attempt = Path(ctx.run.worktree or "") / ".factory" / "run" / "1"
    assert not (attempt / "gates.json").exists()  # the parsed document never existed
    assert (attempt / "gates.stdout.txt").read_text() == (
        '{"schemaVersion": 1, "verdict": "pass"}\ntrailing\n'
    )
    assert (attempt / "gates.stderr.txt").exists()


def test_a_credential_in_the_vm_environment_blocks_before_any_model_call(
    ctx: Context,
) -> None:
    """The second channel. `sbx inspect` reports proxy-managed secrets and cannot see an
    environment variable, so a credential injected there passed the preflight in silence
    until this check existed — measured on 2026-08-22, when every live sandbox carried a
    40-character `gho_` value that `inspect` did not mention.

    The registry acknowledges `GH_TOKEN` for this project, so the tracker key is the one
    that blocks here: acknowledging one name says nothing about the next one to appear.
    """
    _fake(ctx).env_credentials = ["GH_TOKEN", "LINEAR_API_KEY"]

    claim_step.run(ctx)
    context_step.run(ctx)
    with pytest.raises(Blocked) as caught:
        sandbox_step.run(ctx)

    assert caught.value.reason == "enforcement-disabled"
    assert "LINEAR_API_KEY" in caught.value.detail
    assert ctx.run.state is not State.SANDBOX_READY


def test_an_acknowledged_credential_is_warned_about_rather_than_blocking(
    ctx: Context,
) -> None:
    _fake(ctx).env_credentials = ["GH_TOKEN"]

    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)

    assert ctx.run.state is State.SANDBOX_READY  # the run continues
    warned = [
        row
        for row in ctx.store.checks(ctx.run.id)
        if row["check_name"] == "preflight:acknowledged-env-credential"
    ]
    assert len(warned) == 1
    assert warned[0]["status"] == "warn"
    assert "GH_TOKEN" in warned[0]["detail"]
