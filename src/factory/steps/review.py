"""`reviewing -> pr_ready | awaiting_human` — the two-tier review (§15.2).

Tier 1 is always run: Standards and Spec, independently, in a second, read-only sandbox
whose workspace is the worktree mounted `:ro` and whose Codex config is overridden
per-invocation to `sandbox_mode="read-only"`. Two enforcement layers, neither of them a
prompt. The prompt is **assembled, not authored** — frame plus checklist, concatenated
exactly the way layer A's `full-review.js` `axisPrompt()` does it — so a review that runs
here and a review that runs through the workflow cannot drift apart.

Tier 2 is the full nine-axis fan-out, and it runs only when the change warrants it. The
trigger rules are all deterministic and all recorded; otherwise Tier 2 is skipped and the PR
body names the rule that skipped it. This is "do not run every skill for every ticket",
made mechanical.

**Reviewers never repair.** The reviewer sandbox cannot write. A critical-or-high finding
is a human decision (AGENTS.md reserves "is this finding real or over-engineering bait?"),
so the run goes to `awaiting_human` rather than auto-looping; the §15.2 "finding goes back
to the implementer's session" repair loop is deferred to Phase 4's resume-by-session-id.

The red-phase replay (§15.3) runs here too, as the first thing at the REVIEWING entry: the
machine makes `verifying -> awaiting_human` illegal while §15.3's `escalate` option routes
there, so the replay runs in `reviewing` where every outcome is reachable. See
`redphase.py`'s header for the reasoning.
"""

from __future__ import annotations

import fnmatch
import json
import shutil
from pathlib import Path
from typing import Any

from factory import artifacts, repo
from factory.agent.base import SchemaInvalid, validate_against_schema
from factory.harness import HarnessConfig
from factory.machine import Blocked, State
from factory.sandbox.base import SandboxSpec, Workspace
from factory.steps import Context, advance, redphase
from factory.steps import block as block_step
from factory.steps import clone as clone_step

__all__ = ["run"]

STEP = "review"

#: §15.2's Tier-1 axes — Standards and Spec, independently. The agent names are the files
#: `axisPrompt` reads: `<agentDir>/<name>.md` (stack override) or the vendored shared frame,
#: plus `<checklistDir>/<name>.md` (the repo's checklist, optional).
_TIER1_AXES: tuple[tuple[str, str], ...] = (
    ("standards", "standards-reviewer"),
    ("spec", "spec-checker"),
)

#: §15.2's Tier-2 trigger thresholds.
_TIER2_FILE_THRESHOLD = 10
_TIER2_LINE_THRESHOLD = 400

#: The directories whose own `AGENTS.md` exists — a Tier-2 trigger per stack.
_SENSITIVE_DIRS: dict[str, tuple[str, ...]] = {
    "python": ("src/app/ai/**",),
    "frontend": ("src/**/routes/**",),
}

#: Severities that route to a human rather than a draft PR. `critical`/`high` only; a
#: `medium`/`low` finding is recorded and carried in the PR body but does not stop delivery.
_HUMAN_SEVERITIES = frozenset({"critical", "high"})

#: The vendored layer-A findings schema, passed to `codex exec --output-schema` and
#: used to validate what comes back. One file, so the Codex path and the workflow path
#: cannot produce different finding shapes.
_FINDINGS_SCHEMA = ".agents/vendor/harness/schema/review-findings.schema.json"


def run(ctx: Context) -> None:
    # The harness config is loaded at intake and carried on the context; the review cannot
    # run without it (the prompts are assembled from its `review.agentDir` / `checklistDir`).
    harness = ctx.harness
    if harness is None:
        raise Blocked("no-harness-config", f"no harness.config.json loaded for {ctx.run.linear_id}")

    # 0. A `--clone` project's branch lives inside the VM until now. Bring it home first:
    #    the replay's host-side diff, the reviewer sandbox (which mounts the host project
    #    `:ro` and has no clone of its own) and the host-execution guard all read an
    #    ordinary host worktree, and after this they get one. Nothing below knows.
    if ctx.project.requires_clone and not ctx.dry_run:
        clone_step.fetch_back(ctx)
    elif ctx.project.requires_clone:
        ctx.would(
            f"git -C {ctx.project.path} fetch "
            f"{clone_step.remote_name(ctx.project.build_sandbox)} {ctx.branch}"
        )
        ctx.would(
            f"git worktree add -b {ctx.branch} {clone_step.host_worktree_path(ctx)} FETCH_HEAD"
        )

    # 1. The red-phase replay (§15.3). May raise Blocked (test-proves-nothing,
    #    behaviour-change-without-test, inconclusive:block) or return "awaiting_human"
    #    (inconclusive:escalate).
    if redphase.replay(ctx) == "awaiting_human":
        _awaiting_human(ctx, "redphase-inconclusive", "the red-phase replay was inconclusive")
        return

    # 2. The test-weakening guard — a judgement, so it escalates rather than blocks.
    offending = redphase.weakening_guard(ctx)
    if offending:
        _awaiting_human(
            ctx,
            "test-weakening",
            "the diff removes assertions in existing tests:\n"
            + "\n".join(f"- {line}" for line in offending[:20]),
        )
        return

    if ctx.dry_run:
        # The replay and the weakening guard are dry-run aware and recorded their would-prints
        # above. The Tier-1/Tier-2 fan-out is real model spend, so a dry run stops short of it
        # and simulates the clean transition.
        ctx.would(
            f"Tier 1: standards + spec review in {ctx.project.review_sandbox} (read-only, sandbox_mode=read-only)"
        )
        ctx.would("Tier 2: evaluate trigger — run the full fan-out or skip + name the rule")
        ctx.would(
            f"advance {State.REVIEWING} -> {State.PR_READY} (clean) | {State.AWAITING_HUMAN} (finding)"
        )
        advance(ctx, State.PR_READY)
        return

    # 3. Tier 1 — always.
    review_dir = ctx.state_dir / "review"
    findings, tier1_has_human = _tier1(ctx, harness, review_dir)

    # 4. Tier 2 — when the change warrants it. The rule that decides is recorded either way.
    trigger = _tier2_trigger(ctx, harness, tier1_has_human)
    if trigger is None:
        # Tier 2 ran; merge its findings. The portable skill produces the same finding shape.
        full = _tier2(ctx, review_dir)
        findings += full
        tier2_rule = "ran"
    else:
        tier2_rule = trigger  # the skip rule, named in the PR body

    # Stash the review summary on the context for the PR body (§13.2). The deliver step
    # reads it back when assembling the body.
    _write_summary(ctx, review_dir, findings, tier2_rule)

    # 5. Transition.
    if tier1_has_human or any(f["severity"] in _HUMAN_SEVERITIES for f in findings):
        # A critical/high finding is James's reserved judgement: route to the human queue
        # rather than auto-looping (Phase 4 adds the resume-to-implementer repair path).
        human = [f for f in findings if f["severity"] in _HUMAN_SEVERITIES]
        raise Blocked(
            "review-finding",
            "the review returned a finding that needs a human to triage:\n"
            + "\n".join(
                f"- [{f['severity']}] {f.get('file', '?')}: {f['summary']}" for f in human[:10]
            ),
        )
    advance(ctx, State.PR_READY)


# --------------------------------------------------------------------------------
# Tier 1 — Standards + Spec, read-only, assembled prompts
# --------------------------------------------------------------------------------


def _tier1(
    ctx: Context, harness: HarnessConfig, review_dir: Path
) -> tuple[list[dict[str, Any]], bool]:
    """Run the two Tier-1 axes. Returns `(findings, has_critical_or_high)`.

    Each axis runs `codex exec` in the read-only review sandbox, with a prompt assembled
    the way `axisPrompt` does it and the diff named in it, writing schema-valid findings
    to `<review_dir>/review-<axis>.json`.
    """
    if ctx.dry_run:
        for label, _agent in _TIER1_AXES:
            ctx.would(f"assemble {label} prompt from {harness.review_agent_dir} + vendored frame")
            ctx.would(
                f"codex exec -c sandbox_mode=read-only (in {ctx.project.review_sandbox}), "
                f"reviewing git diff {ctx.run.base_ref}...HEAD"
            )
        return [], False

    scratch = _review_scratch(ctx)
    spec = _review_spec(ctx, scratch)
    ctx.sandbox.ensure(spec)
    base_ref = ctx.run.base_ref or ctx.project.base_ref
    schema_path = ctx.worktree / _FINDINGS_SCHEMA
    review_dir.mkdir(parents=True, exist_ok=True)

    findings: list[dict[str, Any]] = []
    has_human = False
    for label, agent in _TIER1_AXES:
        prompt = _axis_prompt(ctx.worktree, harness, agent, base_ref)
        # The reviewer writes into the project-stable scratch, because that is what it can
        # reach; the run's own directory is where the evidence lives, so the file moves
        # there the moment the axis returns.
        scratch_out = _sandbox_out(scratch, f"review-{label}.json")
        out_path = review_dir / f"review-{label}.json"
        events_path = review_dir / f"review-{label}.events.jsonl"
        stderr_path = review_dir / f"review-{label}.stderr.log"
        argv = _review_argv(ctx, schema_path, scratch_out)
        completed = ctx.sandbox.exec_sync(
            ctx.project.review_sandbox,
            argv,
            workdir=str(ctx.worktree),
            env=dict(ctx.project.env),
            timeout=ctx.timeout_for(State.REVIEWING),
            stdin=prompt,
        )
        events_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        _land(scratch_out, out_path)
        axis_findings = _validated_findings(ctx, out_path, completed, label)
        artifacts.scan_for_secrets(completed.stdout + completed.stderr, f"review {label} stdout")
        findings += axis_findings
        has_human = has_human or any(f["severity"] in _HUMAN_SEVERITIES for f in axis_findings)
        ctx.store.record_check(
            ctx.run.id,
            ctx.run.attempt,
            f"review:{label}",
            "pass",
            detail=json.dumps(
                [{"sev": f["severity"], "file": f.get("file")} for f in axis_findings]
            )[:2000],
            artifact=str(review_dir),
        )
    return findings, has_human


def _review_argv(
    ctx: Context, schema_path: Path, out_path: Path, workdir: Path | None = None
) -> list[str]:
    """`codex exec` for one axis — the same invocation the implement step uses.

    Not `codex exec review --base <ref>`: codex refuses that flag together with a prompt
    (`the argument '--base <BRANCH>' cannot be used with '[PROMPT]'`), and the prompt is
    the axis. An axis-less review would run the same generic pass twice and call it
    Standards and Spec, which is the failure §15.2 exists to prevent. Layer A's own
    `full-review.js` reaches the same conclusion from the other side: it names the diff
    inside the prompt and never uses the `review` subcommand.

    `--dangerously-bypass-hook-trust` is included for the same reason the implement step
    includes it: the worktree path is untrusted in `~/.codex/config.toml`, and without it
    Codex appends a project stanza for it. The `:ro` mount and `sandbox_mode=read-only`
    are what make the review safe; this flag only stops a config-file side effect.
    """
    role = ctx.routing.role("reviewer")
    return [
        "codex",
        "exec",
        "-m",
        role.model,
        "-c",
        f"model_reasoning_effort={role.effort}",
        "-c",
        "sandbox_mode=read-only",
        "--dangerously-bypass-hook-trust",
        "--json",
        "--output-schema",
        str(schema_path),
        "-o",
        str(out_path),
        # `-C` goes before the positional, never after: `-` is the prompt argument and a
        # flag trailing it is a flag the parser has already stopped reading.
        *(["-C", str(workdir)] if workdir else []),
        "-",  # prompt from stdin
    ]


def _sandbox_out(scratch: Path, name: str) -> Path:
    """Where a reviewer is told to write, for every tier.

    The scratch is the reviewer's one writable mount, so it is the only path a `-o`
    argument may name. Told to write anywhere else, codex exits 0 and produces nothing —
    `Failed to write last message file …: No such file or directory` on stderr — which
    reads downstream as a review that returned no findings file. Tier 1 learned this on
    BAC-4's run 1effc543d83a459a and Tier 2 learned it again on 2efa19065ce6476e, so both
    now come through here.
    """
    path = scratch / name
    path.unlink(missing_ok=True)
    return path


def _land(scratch_out: Path, out_path: Path) -> None:
    """Move an axis's findings from the shared scratch into this run's own directory,
    where the evidence belongs."""
    if scratch_out.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(scratch_out), out_path)


def _review_scratch(ctx: Context) -> Path:
    """The reviewer's writable ground: one directory per **project**, not per run.

    §9.1 fixes a sandbox's workspace set at creation, and the reviewer sandbox is named
    once per project, so every path in its spec has to outlive the run that first created
    it. A per-run directory does not: the second run finds the sandbox mounted on the
    first run's path and `_assert_spec_matches` refuses it, correctly. BAC-4 measured
    exactly that. The axis outputs are moved into the run's own directory as soon as they
    land, so the evidence is still per-run — only the mount is shared.
    """
    scratch = ctx.home / "state" / "review" / ctx.project.name
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


def _review_spec(ctx: Context, scratch: Path) -> SandboxSpec:
    """The read-only review sandbox: the project `:ro` + a `rw` scratch for findings.

    The read-only mount is the **project root**, not the worktree, for the same reason the
    scratch is per-project: a worktree path contains the ticket, and a spec that changes
    per ticket cannot be satisfied by a sandbox named per project. The build sandbox has
    always mounted the root for this reason — worktrees live inside it at
    `.factory/worktrees/<TICKET>`, so mounting the root reaches every one of them, and
    `exec_sync(workdir=...)` still puts the reviewer in the worktree it is reviewing.

    `--no-share-skills` (§19 Phase 3 checklist): the reviewer has no skills store, so the
    portable `full-review` skill is reached by inlining it (Tier 2), not by loading a shared
    store a build sandbox could have polluted (R5).
    """
    return SandboxSpec(
        project=ctx.project.name,
        role="review",
        name=ctx.project.review_sandbox,
        # The rw scratch is *first*, and that is not a style choice: `sbx create` refuses
        # a read-only primary workspace outright — "primary workspace must be read/write
        # (remove ':ro' or ':readonly')" — and its own help says `:ro` applies to the
        # additional workspaces. So the reviewer's writable ground is the scratch it writes
        # findings to, and the code under review comes in beside it, read-only, at the
        # identical path. The boundary §15.2 asks for is unchanged; only the order is.
        workspaces=(
            Workspace(scratch),
            Workspace(ctx.project.path, readonly=True),
        ),
        template=ctx.project.template or None,
        kits=(),
        static_mcp=(),
        deny_network=ctx.registry.defaults.deny_network,
        env={},
        share_skills=False,
    )


def _axis_prompt(worktree: Path, harness: HarnessConfig, agent: str, base_ref: str) -> str:
    """Assemble one axis's prompt the way `full-review.js`'s `axisPrompt()` does.

    Frame = a stack override at `<review.agentDir>/<agent>.md`, else the shared vendored
    frame at `.agents/vendor/harness/agents/<agent>.md`. Checklist = `<review.checklistDir>/
    <agent>.md`, optional. If both resolve to nothing, the factory throws — matching layer A's
    own refusal to review on a one-line brief, because a silent "no findings" from an axis
    that never ran is the failure this system exists to prevent.
    """
    own = _body(worktree / harness.review_agent_dir / f"{agent}.md")
    frame = own or _body(worktree / ".agents/vendor/harness/agents" / f"{agent}.md")
    checklist = _body(worktree / harness.review_checklist_dir / f"{agent}.md")
    if not frame and not checklist:
        raise Blocked(
            "review-frame-missing",
            f"the {agent} axis resolved to nothing. Looked in "
            f"{harness.review_agent_dir}/{agent}.md, .agents/vendor/harness/agents/{agent}.md "
            f"and {harness.review_checklist_dir}/{agent}.md. Layer A is delivered as a plugin "
            "or as a vendored tree; one is missing.",
        )
    if not checklist:
        # spec-checker carries its whole review in the frame; no checklist
        return frame + _target(base_ref)
    return (
        frame + f"\n\n---\n\n## {harness.name}: what to look for, in this repo's terms\n\n"
        "This is the checklist the frame above told you to read. It is reproduced here so "
        "you have it without a file read; it is authoritative for this repository, and where "
        "it names a source file, that source outranks it.\n\n" + checklist + _target(base_ref)
    )


def _target(base_ref: str) -> str:
    """Which diff this axis reviews.

    The factory holds no review prompt — the frame and the checklist above are read from
    the vendored layer-A tree and the repository, and this adds no criterion to either.
    It names the target, which is the factory's own business: it decides what runs and
    against what. `full-review.js` appends the same two facts for the same reason
    (`Review the diff: git diff ${BASE}...HEAD`), because a reviewer given a frame and no
    target reviews whatever it happens to open, and one not told that silence is a valid
    answer invents findings to look thorough.
    """
    return (
        f"\n\n---\n\nReview the diff: `git diff {base_ref}...HEAD`\n\n"
        "Report ONLY real defects in this diff. An empty findings list is a valid and "
        "common result — do not manufacture findings to look thorough."
    )


def _body(path: Path) -> str:
    """Body of a Markdown definition with YAML frontmatter stripped. Empty when absent."""
    if not path.exists():
        return ""
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            raw = raw[end + 4 :]
    return raw.strip()


def _validated_findings(
    ctx: Context, out_path: Path, completed: Any, label: str
) -> list[dict[str, Any]]:
    """Read the `-o` findings file, validate against the layer-A schema, return the list.

    A missing or invalid file blocks rather than rounding to "no findings": an axis that
    produced nothing the schema recognises is not the same as an axis that found nothing.
    """
    if not out_path.exists():
        raise Blocked(
            "review-schema-invalid",
            f"the {label} review wrote no findings file at {out_path}. exit={completed.returncode}; "
            f"stderr={completed.stderr[:500]}",
        )
    try:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Blocked("review-schema-invalid", f"the {label} findings are not JSON: {exc}") from exc
    schema = json.loads((ctx.worktree / _FINDINGS_SCHEMA).read_text(encoding="utf-8"))
    try:
        validate_against_schema(payload, schema)
    except SchemaInvalid as exc:
        raise Blocked(
            "review-schema-invalid", f"the {label} findings failed schema validation: {exc}"
        ) from exc
    findings = payload.get("findings", [])
    if not isinstance(findings, list):
        raise Blocked("review-schema-invalid", f"the {label} findings `findings` is not a list")
    return [dict(f) for f in findings]


# --------------------------------------------------------------------------------
# Tier 2 — the full nine-axis fan-out, when warranted
# --------------------------------------------------------------------------------


def _tier2_trigger(ctx: Context, harness: HarnessConfig, tier1_has_human: bool) -> str | None:
    """The rule that fires Tier 2, or the skip rule name if it does not. All deterministic,
    all recorded. Returns None to mean "run Tier 2"; a string to mean "skip, name this rule."""
    worktree = ctx.worktree
    base_ref = ctx.run.base_ref or ctx.project.base_ref
    paths = repo.changed_paths(worktree, base_ref)
    lines = repo.changed_lines(worktree, base_ref)
    sensitive = _SENSITIVE_DIRS.get(ctx.project.stack, ())
    return _decide_tier2(
        paths=paths,
        lines=lines,
        protected_hits=harness.protected_hits(paths),
        sensitive=sensitive,
        tier1_has_human=tier1_has_human,
        bug_without_test=_bug_without_test(ctx),
    )


def _decide_tier2(
    *,
    paths: list[str],
    lines: int,
    protected_hits: list[str],
    sensitive: tuple[str, ...],
    tier1_has_human: bool,
    bug_without_test: bool,
) -> str | None:
    """The pure Tier-2 decision (§15.2). None = run; a string = the skip rule to name.

    Separated from `_tier2_trigger` so the whole trigger table is asserted without a context,
    a worktree or git — the rules are the part that must not drift from the spec.
    """
    if len(paths) >= _TIER2_FILE_THRESHOLD:
        return None
    if lines >= _TIER2_LINE_THRESHOLD:
        return None
    if protected_hits:
        return None
    if sensitive and any(_matches_any(path, sensitive) for path in paths):
        return None
    if tier1_has_human:
        return None
    if bug_without_test:
        return None
    return "no-trigger"


def _bug_without_test(ctx: Context) -> bool:
    """The ticket carries a `Bug` label and the fix added no test."""
    if ctx.issue is None:
        return False
    labels = {label.lower() for label in ctx.issue.labels}
    if "bug" not in labels:
        return False
    attempt_dir = ctx.worktree / ".factory" / "run" / str(ctx.run.attempt)
    if not attempt_dir.exists():
        return True
    try:
        payload = json.loads((attempt_dir / "last-message.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    return not payload.get("tests_added")


def _tier2(ctx: Context, review_dir: Path) -> list[dict[str, Any]]:
    """Run the portable `full-review` skill inside the reviewer sandbox.

    The portable SKILL.md is inlined rather than invoked, matching the implement step's
    pattern: a Codex skill's availability in the catalog is not guaranteed in an unattended
    sandbox, and inlining the body keeps the review independent of skill-store plumbing.
    """
    skill = ctx.worktree / ".agents/vendor/harness/skills/full-review/SKILL.md"
    if not skill.exists():
        raise Blocked(
            "review-skill-missing", f"the portable full-review skill is absent at {skill}"
        )
    base_ref = ctx.run.base_ref or ctx.project.base_ref
    body = _body(skill)
    prompt = (
        f"{body}\n\n---\n\nRun the full review now, against `{base_ref}...HEAD` in this "
        f"worktree. Report defects only, ranked most severe first, each with file:line and a "
        "one-sentence failure scenario. Emit the findings as the JSON schema this run was "
        f"given (`{_FINDINGS_SCHEMA}`). Do not push fixes; the sandbox is read-only.\n"
    )
    scratch_out = _sandbox_out(_review_scratch(ctx), "review-full.json")
    out_path = review_dir / "review-full.json"
    schema_path = ctx.worktree / _FINDINGS_SCHEMA
    # The same argv Tier 1 builds. Tier 2 had its own copy, and the copy is how the two
    # drifted: Tier 1 was moved onto the writable scratch and this one was not.
    argv = _review_argv(ctx, schema_path, scratch_out, workdir=ctx.worktree)
    completed = ctx.sandbox.exec_sync(
        ctx.project.review_sandbox,
        argv,
        workdir=str(ctx.worktree),
        env=dict(ctx.project.env),
        timeout=ctx.timeout_for(State.REVIEWING),
        stdin=prompt,
    )
    # `codex exec` (not `review`) has no `--base`; the prompt carries the range. Reuse the
    # same landing and validation path as Tier 1.
    _land(scratch_out, out_path)
    return _validated_findings(ctx, out_path, completed, "full")


# --------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    normalised = path.replace("\\", "/").removeprefix("./")
    for pattern in patterns:
        if fnmatch.fnmatchcase(normalised, pattern):
            return True
        if pattern.endswith("/**") and normalised.startswith(pattern[:-2]):
            return True
    return False


def _write_summary(
    ctx: Context, review_dir: Path, findings: list[dict[str, Any]], tier2_rule: str
) -> None:
    """Stash the review summary for the PR body (§13.2)."""
    review_dir.mkdir(parents=True, exist_ok=True)
    artifacts.write_json(
        review_dir / "review-summary.json",
        {
            "tier2": tier2_rule,
            "findings": [
                {
                    "severity": f.get("severity"),
                    "file": f.get("file"),
                    "line": f.get("line"),
                    "summary": f.get("summary"),
                }
                for f in findings
            ],
        },
    )


def _awaiting_human(ctx: Context, reason: str, detail: str) -> None:
    """Route to `awaiting_human` and announce it (§13.1). No PR is opened."""
    advance(ctx, State.AWAITING_HUMAN, rule=reason, detail=detail[:2000])
    tokens_in, tokens_out, usd = ctx.store.spend(ctx.run.id)
    block_step.announce_awaiting_human(
        ctx,
        pr_url=None,
        sections=[
            f"**Reason:** {reason}\n{detail[:1500]}",
            f"**Review summary:** see `{ctx.state_dir / 'review' / 'review-summary.json'}`",
            f"**Cost:** in {tokens_in}, out {tokens_out} "
            f"({f'${usd:.2f}' if usd is not None else 'token counts only'})",
        ],
    )
