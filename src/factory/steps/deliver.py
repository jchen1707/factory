"""`pr_ready -> awaiting_human` — host-execution guard, push, PR (§13.2, §17.4, §24.8).

The last automatic step. The factory pushes from the host (the sandbox has no remote
credential) and opens the PR **ready for review** — the gates and the review have already
run, so "draft" was the wrong word for it (`delivery/github.py: create_pr` argues this in
full). Merge is always his.
Before any host-side command runs, the host-execution guard inspects the diff: a touch of
the vendored tree is a block (the enforcement layer is broken), a touch of a host-execution
deny-list path (`.husky`, `.github`, …) routes to `awaiting_human` rather than a push, and
only a clear diff is pushed.

On a clean delivery the run transitions `pr_ready -> awaiting_human` and announces it
(§13.1): the issue moves to In Review and the factory comments the PR link, the gate
report, the review summary and the cost. The `pr_ready` "attach the PR URL" write is
Linear's GitHub integration's job (the `Fixes <TEAM-NUM>` in the body triggers it); the
factory records the URL on the run row and carries it in the awaiting_human comment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factory import artifacts, policy, repo
from factory.delivery import github
from factory.machine import Blocked, State
from factory.steps import Context, advance
from factory.steps import block as block_step
from factory.steps import review as review_step

__all__ = ["run"]

STEP = "deliver"


def run(ctx: Context) -> None:
    worktree = ctx.worktree
    branch = ctx.branch
    if branch is None:
        raise Blocked("no-branch", f"run {ctx.run.id} has no branch to push")

    # 1. §17.4 host-execution guard — before any host-side command touches the worktree.
    base_ref = ctx.run.base_ref or ctx.project.base_ref
    changed = repo.changed_paths(worktree, base_ref)
    verdict, paths = policy.host_execution_verdict(changed)
    if verdict == "blocked":
        # A vendored-tree edit means the guard layer itself is broken; a human reading the
        # diff is not the right next step.
        raise Blocked(
            "host-execution-blocked",
            "the diff edits the vendored layer-A tree, which protect_paths should have "
            f"refused: {paths}",
        )
    if verdict == "awaiting_human":
        # .husky, .github, package.json … — files that make the host execute agent code.
        advance(
            ctx,
            State.AWAITING_HUMAN,
            rule="host-execution-deny",
            detail=f"the diff touches a host-execution deny-list path: {paths}",
        )
        block_step.announce_awaiting_human(
            ctx,
            pr_url=None,
            sections=[
                f"**Host-execution guard:** the diff touches `{paths}`, which would make "
                "the host execute agent-authored code. Not pushed; review before proceeding.",
            ],
        )
        return

    # 2. Assemble the PR body from the evidence the run already recorded.
    body = _render_body(ctx)
    body_path = ctx.state_dir / "pr-body.md"
    pr_title = f"{ctx.run.linear_id}: {ctx.issue.title}" if ctx.issue else ctx.run.linear_id

    if ctx.dry_run:
        ctx.would(f"git -C {worktree} -c core.hooksPath=/dev/null push -u origin {branch}")
        ctx.would(f"gh pr list --head {branch}  # duplicate guard")
        ctx.would(
            f"gh pr create --base {ctx.project.base_branch} --head {branch} --title {pr_title!r}"
        )
        ctx.would(f"  --body-file {body_path} ({len(body)} bytes)")
        ctx.would(f"transition {State.PR_READY} -> {State.AWAITING_HUMAN}")
        ctx.would("linear: move to In Review + comment (PR link, gates, review, cost)")
        return

    body_path.write_text(body, encoding="utf-8")
    # F18: a secret that reached the PR body is compromised and must not be pushed. The run
    # fails before any host-side write; the block announcement tells James to rotate without
    # naming the value. The archive scan below catches a secret in the attempt dir too.
    try:
        artifacts.scan_for_secrets(body, "the PR body")
    except artifacts.SecretFound as exc:
        raise Blocked(
            "secret-in-artifact",
            f"a secret ({exc.kind}) reached the PR body. The run stopped before any push or "
            "PR write. The value is compromised — rotation is James's call.",
        ) from exc

    # 3. Push, then open or edit the PR (F16 duplicate guard).
    github.push(worktree, branch)
    existing = github.find_pr(worktree, branch)
    if existing:
        pr_url = existing
        number = github.pr_number(existing)
        if number is not None:
            github.edit_pr(worktree, number, body_file=body_path)
    else:
        pr_url = github.create_pr(
            worktree,
            base=ctx.project.base_branch,
            head=branch,
            title=pr_title,
            body_file=body_path,
        )
    ctx.store.update_run(ctx.run.id, pr_url=pr_url)
    ctx.log("deliver.pr_opened", url=pr_url, draft=False)

    # 4. Archive the attempt evidence to artifacts/ (§14.1), then announce. A secret in the
    # attempt dir fails the archive the same way the body scan does.
    try:
        _archive(ctx)
    except artifacts.SecretFound as exc:
        raise Blocked(
            "secret-in-artifact",
            f"a secret ({exc.kind}) reached the attempt artifacts. The PR is open at "
            f"{pr_url}; the artifact is quarantined and the value must be rotated.",
        ) from exc
    advance(ctx, State.AWAITING_HUMAN, rule="delivered")
    tokens_in, tokens_out, usd = ctx.store.spend(ctx.run.id)
    block_step.announce_awaiting_human(
        ctx,
        pr_url=pr_url,
        sections=[
            f"**Gates:** {_gate_summary(ctx)}",
            f"**Review:** {_review_summary(ctx)}",
            f"**Cost:** in {tokens_in}, out {tokens_out} "
            f"({f'${usd:.2f}' if usd is not None else 'token counts only'})",
        ],
    )


# --------------------------------------------------------------------------------
# evidence gathering
# --------------------------------------------------------------------------------


def _render_body(ctx: Context) -> str:
    """Assemble the §13.2 PR body from the evidence the run recorded."""
    attempt_dir = ctx.factory_dir / "run" / str(ctx.run.attempt)
    result = _read_json(attempt_dir / "last-message.json")
    gates_doc = _read_json(attempt_dir / "gates.json")
    review_summary = _read_json(ctx.state_dir / "review" / "review-summary.json")
    tokens_in, tokens_out, usd = ctx.store.spend(ctx.run.id)
    return github.render_pr_body(
        ticket=ctx.run.linear_id,
        title=ctx.issue.title if ctx.issue else ctx.run.linear_id,
        restatement=str(result.get("summary", "")),
        gates=list(gates_doc.get("gates", [])),
        gate_verdict=str(gates_doc.get("verdict", "")),
        review_summary=review_summary,
        redphase=_redphase_row(ctx),
        out_of_scope=result.get("out_of_scope", []) or [],
        artifact_path=str(ctx.artifact_root / str(ctx.run.attempt)),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        usd=usd,
    )


def _gate_summary(ctx: Context) -> str:
    attempt_dir = ctx.factory_dir / "run" / str(ctx.run.attempt)
    gates_doc = _read_json(attempt_dir / "gates.json")
    verdict = gates_doc.get("verdict", "?")
    rows = [f"{g.get('name', '?')}={g.get('status', '?')}" for g in gates_doc.get("gates", [])]
    return f"verdict {verdict} ({', '.join(rows) or 'no gates'})"


def _review_summary(ctx: Context) -> str:
    summary = _read_json(ctx.state_dir / "review" / "review-summary.json")
    if not summary:
        return "no review summary recorded"
    tier2 = summary.get("tier2", "no-trigger")
    findings = summary.get("findings", [])
    if tier2 == "ran":
        head = "Tier 2 ran"
    elif tier2 == review_step.FORCED:
        # Said plainly, because a reader deciding how much this review is worth needs to
        # know the trigger rules did *not* think this diff warranted the fan-out.
        head = "Tier 2 ran (forced with `--full-review`; no trigger rule fired)"
    else:
        head = f"Tier 2 skipped (`{tier2}`)"
    if not findings:
        return f"{head}; Tier-1 found no defects."
    severities = [f.get("severity", "?") for f in findings]
    return f"{head}; {len(findings)} finding(s): {', '.join(severities)}."


def _redphase_row(ctx: Context) -> dict[str, str] | None:
    """The redphase check row, if one was recorded."""
    for row in ctx.store.checks(ctx.run.id):
        if row["check_name"] == "redphase":
            return {
                "status": str(row["status"]),
                "reason": str(row["reason"] or ""),
            }
    return None


def _archive(ctx: Context) -> None:
    """Copy the attempt directory out to `artifacts/`, scanning for secrets first (§14.1, F18)."""
    attempt_dir = ctx.factory_dir / "run" / str(ctx.run.attempt)
    if not attempt_dir.is_dir():
        return
    extra = list(ctx.harness.secret_vars) if ctx.harness else []
    artifacts.archive(attempt_dir, ctx.artifact_root / str(ctx.run.attempt), extra_names=extra)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
