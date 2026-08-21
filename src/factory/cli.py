"""`factory` — the command line. Phase 1 ships `run`, `status`, `doctor` and `cancel`.

`run` is typed by a human. There is no poller in this phase and no daemon: §19's
approval boundary for Phase 1 is that James applies `ready-for-agent` *and* types the
command, and the cheapest way to keep that true is not to build the thing that would
make it false.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from factory import artifacts, machine, repo
from factory.agent.codex import CodexAdapter
from factory.harness import load_harness_config, vendor_check
from factory.intake.linear import (
    LinearClient,
    LinearError,
    RepoFacts,
    eligibility_verdict,
    evaluate_eligibility,
    keychain_secret,
)
from factory.machine import Blocked, Resumable, State
from factory.registry import Registry, RegistryError, load_registry
from factory.repo import GitError
from factory.routing import MODEL_CACHE, RoutingError, load_routing
from factory.sandbox.sbx import SbxAdapter, SbxError, sbx_available
from factory.steps import Context
from factory.steps import claim as claim_step
from factory.steps import context as context_step
from factory.steps import implement as implement_step
from factory.steps import plan as plan_step
from factory.steps import sandbox as sandbox_step
from factory.steps import worktree as worktree_step
from factory.store import Store

__all__ = ["main"]

LEASE_TTL_SECONDS = 900


def factory_home() -> Path:
    """The repository directory, unless `FACTORY_HOME` overrides it.

    `state/`, `artifacts/` and `logs/` hang off it. Never `~/.factory/`: `sbx skills
    import` scans that path and it belongs to Factory.ai's Droid.
    """
    override = os.environ.get("FACTORY_HOME")
    return Path(override) if override else Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------------
# factory run
# --------------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    home = factory_home()
    registry = load_registry(home / "config" / "projects.toml")
    routing = load_routing(home / "config" / "models.toml")

    ticket = args.ticket.upper()
    project = registry.resolve(ticket)

    linear = LinearClient()
    issue = linear.issue(ticket)

    harness = load_harness_config(project.path)
    branch = repo.branch_name(
        repo.branch_type_for_labels(list(issue.labels)), issue.identifier, issue.title
    )
    facts = RepoFacts(
        tracker_team=harness.team,
        open_pr_heads=_open_pr_heads(project.path),
        base_ref_subjects=tuple(repo.identifier_on_base(project.path, ticket, project.base_ref)),
        remote_branches=tuple(repo.remote_branches_matching(project.path, ticket)),
        expected_branch=branch,
        known_teams=frozenset(p.team for p in registry.projects.values()),
    )

    conditions = evaluate_eligibility(issue, facts)
    eligible, block_reason, failures = eligibility_verdict(conditions)

    print(f"{ticket} — {issue.title}")
    print(f"  project {project.name}   base {project.base_ref}   branch {branch}")
    for condition in conditions:
        print(
            f"  {'PASS' if condition.passed else 'FAIL'}  {condition.number:>2}. {condition.label}"
        )

    # A remote branch that merely mentions the identifier is a warning, not a block.
    # `origin/feat/BAC-4-application-skeleton` is the live case: unmerged work that the
    # base-ref check cannot see. It blocks the *branch name* — which `add_worktree`
    # enforces exactly — and it is worth reading before the run, but the work is
    # unlanded and may well be worth redoing.
    stale = [b for b in facts.remote_branches if b != f"origin/{branch}"]
    if stale:
        print(f"  NOTE  a remote branch already mentions {ticket}: {', '.join(stale)}")
        print("        the factory will not reuse it; it is also a free oracle to diff against")

    if not eligible:
        if block_reason is None:
            print(f"\n{ticket} is not eligible and no run was created: {failures}")
            return 1
        print(f"\n{ticket} is blocked: {block_reason} ({failures})")

    store = _open_store(home, dry_run=args.dry_run)
    run = store.insert_run(linear_id=ticket, project=project.name, team=issue.team_key)

    if not args.dry_run and not store.acquire_lease(run.id, ttl_seconds=LEASE_TTL_SECONDS):
        print(f"\n{ticket} is leased by another process ({run.lease_owner}); nothing to do.")
        return 0

    ctx = Context(
        home=home,
        registry=registry,
        routing=routing,
        store=store,
        linear=linear,
        sandbox=SbxAdapter(),
        agent=CodexAdapter(),
        project=project,
        run=store.run_by_id(run.id) or run,
        issue=issue,
        harness=harness,
        dry_run=args.dry_run,
    )

    if not eligible and block_reason:
        _block(ctx, block_reason, "; ".join(failures))
        return 2

    if ctx.run.state is not State.APPROVED:
        print(
            f"\n{ticket} is already at `{ctx.run.state}` (attempt {ctx.run.attempt}). "
            "Phase 1 runs a ticket once and has no resume command yet: "
            f"`factory status {ticket} --evidence` shows what happened, and "
            f"`factory cancel {ticket}` clears the worktree and the branch so it can "
            "be run again."
        )
        ctx.store.release_lease(ctx.run.id)
        return 1

    codex_stanzas_before = _codex_project_stanzas()

    try:
        _drive(ctx, force_plan=args.plan)
    except Blocked as exc:
        _block(ctx, exc.reason, exc.detail)
        _report(ctx)
        return 2
    except Resumable as exc:
        print(f"\n{ticket} is resumable: {exc.reason} — {exc.detail}")
        if not ctx.dry_run:
            ctx.store.record_transition(
                ctx.run.id,
                from_state=ctx.state,
                to_state=State.RESUMABLE,
                actor="auto",
                rule=exc.reason,
                detail=exc.detail[:2000],
            )
        _report(ctx)
        return 3
    finally:
        if not ctx.dry_run:
            _assert_codex_config_untouched(codex_stanzas_before)
            ctx.store.release_lease(ctx.run.id)

    _report(ctx)
    if ctx.dry_run:
        print("\nDry run. These are the commands it would have executed:\n")
        for line in ctx.planned:
            print(f"  {line}")
        print("\nNothing was written: no Linear call, no git write, no sandbox, no model call.")
    else:
        print(
            "\nPhase 1 stops here. Verification, review and the pull request arrive in "
            "Phases 2 and 3; nothing has been pushed and no PR exists."
        )
    return 0


def _drive(ctx: Context, *, force_plan: bool) -> None:
    """The fixed per-ticket shape, as far as Phase 1 goes.

    Python decides control flow; the model decides only what to write inside one step.
    No agent chooses the next state.
    """
    claim_step.run(ctx)
    context_step.run(ctx)
    sandbox_step.run(ctx)
    worktree_step.run(ctx)
    if plan_step.should_plan(ctx, forced=force_plan):
        plan_step.run(ctx)
    implement_step.run(ctx)


def _block(ctx: Context, reason: str, detail: str) -> None:
    print(f"\nBLOCKED: {reason}\n  {detail}")
    if ctx.dry_run:
        ctx.would(f"block: {reason} ({detail})")
        return
    ctx.store.update_run(ctx.run.id, blocked_reason=reason)
    if machine.can(ctx.state, State.BLOCKED):
        ctx.store.record_transition(
            ctx.run.id,
            from_state=ctx.state,
            to_state=State.BLOCKED,
            actor="auto",
            rule=reason,
            detail=detail[:2000],
        )
        ctx.refresh()
    ctx.log("run.blocked", level="error", reason=reason, detail=detail[:500])


def _report(ctx: Context) -> None:
    if not ctx.dry_run:
        ctx.refresh()
    tokens_in, tokens_out, usd = ctx.store.spend(ctx.run.id)
    print(f"\nstate      {ctx.state}")
    print(f"attempt    {ctx.run.attempt}")
    if ctx.run.worktree:
        print(f"worktree   {ctx.run.worktree}")
    if ctx.run.branch:
        print(f"branch     {ctx.run.branch}")
    print(f"tokens     in {tokens_in}, out {tokens_out}")
    print(f"cost       {f'${usd:.2f}' if usd is not None else 'token counts only (§18.3)'}")


# --------------------------------------------------------------------------------
# factory status
# --------------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    home = factory_home()
    store = _open_store(home, dry_run=False)
    runs = store.all_runs()
    if args.ticket:
        runs = [r for r in runs if r.linear_id == args.ticket.upper()]
        if not runs:
            print(f"no run for {args.ticket.upper()}")
            return 1

    for run in runs:
        tokens_in, tokens_out, usd = store.spend(run.id)
        lease = "held" if run.lease_owner else "free"
        print(
            f"{run.linear_id:<10} {run.state!s:<16} attempt {run.attempt}  "
            f"lease {lease}  tokens {tokens_in}/{tokens_out}  "
            f"cost {f'${usd:.2f}' if usd is not None else '-'}"
        )
        if run.blocked_reason:
            print(f"           blocked: {run.blocked_reason}")
        if args.evidence:
            for row in store.transitions(run.id):
                stamp = time.strftime("%H:%M:%S", time.localtime(row["at"]))
                rule = f"  [{row['rule']}]" if row["rule"] else ""
                print(f"           {stamp}  {row['from_state']} -> {row['to_state']}{rule}")
            for row in store.checks(run.id):
                print(f"           check {row['check_name']}: {row['status']}")
            for effect in store.effects(run.id):
                print(f"           effect {effect.system}/{effect.key}: {effect.status}")
    return 0


# --------------------------------------------------------------------------------
# factory cancel
# --------------------------------------------------------------------------------


def cmd_cancel(args: argparse.Namespace) -> int:
    """Phase 1's rollback. Removes the worktree, deletes the *unpushed* branch, releases
    the lease, and leaves the sandbox stopped. A pushed branch is never deleted."""
    home = factory_home()
    registry = load_registry(home / "config" / "projects.toml")
    store = _open_store(home, dry_run=False)
    ticket = args.ticket.upper()
    run = store.run_by_ticket(ticket)
    if run is None:
        print(f"no run for {ticket}")
        return 1

    project = registry.projects[run.project]
    if run.worktree:
        # Archive before removing. A rollback that destroys the evidence of why the run
        # needed rolling back is not a rollback, it is a cover-up.
        attempts = Path(run.worktree) / ".factory" / "run"
        if attempts.is_dir():
            harness = load_harness_config(project.path)
            for directory in sorted(attempts.iterdir()):
                if directory.is_dir():
                    kept = artifacts.archive(
                        directory,
                        home / "artifacts" / run.linear_id / directory.name,
                        extra_names=harness.secret_vars,
                    )
                    print(f"archived {directory.name} to {kept}")
    if run.worktree:
        repo.remove_worktree(project.path, Path(run.worktree), force=True)
        print(f"removed worktree {run.worktree}")
    if run.branch:
        repo.delete_local_branch(project.path, run.branch)
        print(f"deleted local branch {run.branch} (kept if it had been pushed)")

    if machine.can(run.state, State.CANCELLED):
        store.record_transition(
            run.id,
            from_state=run.state,
            to_state=State.CANCELLED,
            actor="human",
            rule="abandon-is-james",
            detail=args.reason,
        )
    store.release_lease(run.id)
    SbxAdapter().stop(project.build_sandbox)
    print(f"{ticket} cancelled; sandbox {project.build_sandbox} stopped")
    return 0


# --------------------------------------------------------------------------------
# factory doctor
# --------------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    home = factory_home()
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    # -- configuration ------------------------------------------------------------
    registry: Registry | None = None
    try:
        registry = load_registry(home / "config" / "projects.toml")
        check("registry", True, f"{len(registry.projects)} projects")
    except (RegistryError, OSError) as exc:
        check("registry", False, str(exc))

    try:
        routing = load_routing(home / "config" / "models.toml")
        check(
            "routing",
            True,
            f"builder={routing.roles['builder'].model}/{routing.roles['builder'].effort}, "
            f"reviewer={routing.roles['reviewer'].model}, ceiling ${routing.usd_per_run:g}",
        )
    except (RoutingError, OSError) as exc:
        check("routing", False, str(exc))

    try:
        machine.assert_table_is_sound()
        check("state table", True, f"{len(machine.TRANSITIONS)} states")
    except AssertionError as exc:
        check("state table", False, str(exc))

    # -- the model cache, against the CLI that will read it ------------------------
    check(*_model_cache_check())

    # -- external tools -----------------------------------------------------------
    for tool, argv in (
        ("git", ["git", "--version"]),
        ("gh", ["gh", "auth", "status"]),
        ("codex", ["codex", "--version"]),
    ):
        _, ok, detail = _tool_check(argv)
        check(tool, ok, detail)

    ok, detail = sbx_available()
    check("sbx", ok, detail.splitlines()[0] if detail else "")

    # -- host configuration James owns (§20.5) ------------------------------------
    check(*_global_gitignore_check())
    check(*_keychain_check())
    check(*_skills_check())
    check(
        "~/.factory absent",
        not (Path.home() / ".factory").exists(),
        "sbx skills import scans ~/.factory/skills, which is Factory.ai's Droid",
    )

    # -- state --------------------------------------------------------------------
    store = _open_store(home, dry_run=False)
    ok, detail = store.integrity_ok()
    check("database", ok, f"{store.path} — {detail}")

    free_gb = shutil.disk_usage(home).free / 1_000_000_000
    floor = registry.defaults.disk_min_free_gb if registry else 20
    check("disk", free_gb >= floor, f"{free_gb:.0f} GB free, floor {floor} GB")

    if registry:
        for project in registry.projects.values():
            ok, detail = vendor_check(
                project.path, Path.home() / "harness" / "scripts" / "vendor_sync.py"
            )
            check(
                f"vendored layer A in {project.name}", ok, detail.splitlines()[-1] if detail else ""
            )

    check(*_prices_check(home))

    if args.deep and registry:
        check(*_deep_canary(registry))

    width = max(len(name) for name, _, _ in results)
    failures = 0
    for name, ok, detail in results:
        failures += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    print()
    if failures:
        print(f"{failures} check(s) failed.")
    else:
        print("All checks passed.")
    return 1 if failures else 0


def _tool_check(argv: Sequence[str]) -> tuple[str, bool, str]:
    name = argv[0]
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return name, False, str(exc)
    output = (proc.stdout or proc.stderr).strip().splitlines()
    return name, proc.returncode == 0, output[0] if output else ""


def _model_cache_check() -> tuple[str, bool, str]:
    """A routing table pinned to a model the CLI no longer offers fails at the worst
    moment, so `doctor` compares the cache's `client_version` with the installed CLI."""
    if not MODEL_CACHE.exists():
        return "model cache", False, f"{MODEL_CACHE} is missing"
    try:
        cache = json.loads(MODEL_CACHE.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return "model cache", False, str(exc)
    proc = subprocess.run(
        ["codex", "--version"], capture_output=True, text=True, check=False, timeout=60
    )
    cli = proc.stdout.strip().split()[-1] if proc.returncode == 0 else "unknown"
    cached = cache.get("client_version", "unknown")
    return (
        "model cache",
        cached == cli,
        f"cache {cached} (fetched {cache.get('fetched_at', '?')}) vs CLI {cli}"
        + ("" if cached == cli else " — open the TUI's /model picker once to refresh"),
    )


def _global_gitignore_check() -> tuple[str, bool, str]:
    """`.factory/` belongs in the **global** gitignore, not in any repository's.

    That keeps control-plane state out of every product diff with no commit to any
    product repo. It is James's file to write (§20.5), so this names the fix rather
    than applying it.
    """
    configured = subprocess.run(
        ["git", "config", "--global", "core.excludesfile"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    path = (
        Path(configured).expanduser() if configured else Path.home() / ".config" / "git" / "ignore"
    )
    if path.exists() and ".factory" in path.read_text():
        return "global gitignore", True, f"{path} ignores .factory/"
    return (
        "global gitignore",
        False,
        f"add `.factory/` to {path} — `mkdir -p {path.parent} && echo '.factory/' >> {path}`",
    )


def _keychain_check() -> tuple[str, bool, str]:
    try:
        keychain_secret()
    except LinearError as exc:
        return "linear credential", False, str(exc)
    return "linear credential", True, "factory-linear present (value not read into any log)"


def _skills_check() -> tuple[str, bool, str]:
    """The execution set, and the version it is pinned to.

    `implement` is inlined rather than invoked (P0-15), so its *file* has to be there
    even though Codex will never list it. The pinned version directory is compared with
    what the plugin cache holds, because the factory should move between skill versions
    on purpose.
    """
    skill = Path.home() / ".agents" / "skills" / "implement" / "SKILL.md"
    if not skill.exists():
        return "mattpocock execution set", False, f"{skill} is missing"
    pinned = skill.resolve()
    match = re.search(r"mattpocock-skills/([^/]+)/", str(pinned))
    version = match.group(1) if match else "unknown"
    cache = (
        Path.home()
        / ".claude"
        / "plugins"
        / "cache"
        / "claude-plugins-official"
        / "mattpocock-skills"
    )
    installed = sorted(p.name for p in cache.iterdir() if p.is_dir()) if cache.is_dir() else []
    drifted = installed and version not in installed
    return (
        "mattpocock execution set",
        not drifted,
        f"pinned {version}, installed {installed or 'unknown'}",
    )


def _prices_check(home: Path) -> tuple[str, bool, str]:
    import tomllib

    path = home / "config" / "prices.toml"
    if not path.exists():
        return "price table", False, f"{path} is missing"
    rows = tomllib.loads(path.read_text()).get("model", [])
    today = time.strftime("%Y-%m-%d")
    expired = [r["name"] for r in rows if r.get("effective_until", "9999") < today]
    return (
        "price table",
        not expired,
        f"{len(rows)} rows; no OpenAI price yet, so Codex runs record tokens with usd=NULL"
        + (f"; EXPIRED: {expired}" if expired else ""),
    )


def _deep_canary(registry: Registry) -> tuple[str, bool, str]:
    """The only real proof that Codex is wired to the hooks — and it costs a model call.

    P0-6 established that at an untrusted path the enforcement layer is invisible: a
    protected-path edit succeeded at exit 0, in silence. There is no `codex hooks trust`
    subcommand and `codex doctor` reports nothing about trust, so a live canary is the
    only verification. It is opt-in because it spends money.
    """
    project = next(iter(registry.projects.values()))
    harness = load_harness_config(project.path)
    protected = harness.first_protected_glob()
    if protected is None:
        return "codex hook canary", False, "the repo declares no protected path to canary"
    proc = subprocess.run(
        [
            "codex",
            "exec",
            "-C",
            str(project.path),
            "--dangerously-bypass-hook-trust",
            f"Append the line '# factory canary' to {protected.glob} and report what happened.",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
        stdin=subprocess.DEVNULL,
    )
    blocked = "blocked by" in (proc.stdout + proc.stderr).lower()
    return (
        "codex hook canary",
        blocked,
        "protect_paths refused the write" if blocked else "the write was NOT refused",
    )


# --------------------------------------------------------------------------------
# shared plumbing
# --------------------------------------------------------------------------------


def _open_store(home: Path, *, dry_run: bool) -> Store:
    """A dry run gets its own throwaway database.

    "`--dry-run` executes none" has to include the local writes, or the flag means
    something weaker than it says. The real state file is not opened at all.
    """
    if dry_run:
        path = home / "state" / "dry-run.db"
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if candidate.exists():
                candidate.unlink()
        return Store(path)
    return Store(home / "state" / "factory.db")


def _open_pr_heads(repo_path: Path) -> tuple[str, ...]:
    """Open PR head branches. Exact, unlike `gh pr list --search`.

    P0-11 measured the search form returning a PR that mentioned neither identifier it
    was given, which would produce a false `duplicate-pr` block.
    """
    proc = subprocess.run(
        ["gh", "pr", "list", "--state", "open", "--json", "headRefName", "--limit", "100"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if proc.returncode != 0:
        return ()
    try:
        return tuple(row["headRefName"] for row in json.loads(proc.stdout or "[]"))
    except json.JSONDecodeError:
        return ()


def _codex_project_stanzas() -> int:
    """How many `[projects."…"]` stanzas `~/.codex/config.toml` holds.

    With `approval_policy = 'never'`, `codex exec` silently appends one for a directory
    it has not seen before. Worktree runs did not add any during discovery, because the
    parent repo is already trusted — but one per worktree would grow the file without
    bound under an unattended factory, so the count is asserted rather than assumed.
    The factory reads this file and never writes it.
    """
    path = Path.home() / ".codex" / "config.toml"
    if not path.exists():
        return 0
    return len(re.findall(r"^\[projects\.", path.read_text(), flags=re.MULTILINE))


def _assert_codex_config_untouched(before: int) -> None:
    after = _codex_project_stanzas()
    if after != before:
        print(
            f"\nWARNING: ~/.codex/config.toml gained {after - before} [projects] stanza(s) "
            "during this run. Codex writes one for a directory it has not seen before. "
            "An agent must never edit that file — remove the stanza by hand if it is "
            "unwanted.",
            file=sys.stderr,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="drive one approved ticket")
    run.add_argument("ticket")
    run.add_argument("--dry-run", action="store_true", help="print every command, execute none")
    run.add_argument("--plan", action="store_true", help="force the planning step first")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="what the factory is doing")
    status.add_argument("ticket", nargs="?")
    status.add_argument("--evidence", action="store_true", help="transitions, checks and effects")
    status.add_argument("--all", action="store_true", help="every run (the default with no ticket)")
    status.set_defaults(func=cmd_status)

    doctor = sub.add_parser("doctor", help="is this machine able to run the factory")
    doctor.add_argument(
        "--deep",
        action="store_true",
        help="also run a live codex canary against a protected path (costs a model call)",
    )
    doctor.set_defaults(func=cmd_doctor)

    cancel = sub.add_parser("cancel", help="abandon a run and clean up after it")
    cancel.add_argument("ticket")
    cancel.add_argument("--reason", default="cancelled by hand")
    cancel.set_defaults(func=cmd_cancel)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (Blocked, RegistryError, RoutingError, LinearError, GitError, SbxError) as exc:
        print(f"factory: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted; the lease will expire on its own", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
