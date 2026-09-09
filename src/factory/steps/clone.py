"""The `--clone` path — a project whose workspace cannot be bind-mounted (§19 Phase 7).

`frontend-harness` is the case. `node_modules` has to sit beside `package.json`, a macOS
host tree is not loadable in a Linux VM, and a sandbox `pnpm install` against a bind mount
overwrites the host's tree (p0-10). `sbx create --clone` is the only shape that keeps
`node_modules` VM-local: the VM runs on a private in-container clone of the repository
instead of on the host's directory.

**What that costs, measured on 2026-08-22 against `sbx` v0.38.0** — the plan assumed the
clone breaks §4.2's filesystem protocol outright. It does not; it breaks one half of it:

| | |
| --- | --- |
| the clone's location in the VM | the *identical host path*, and writable |
| host visibility of VM writes there | none — the isolation is the point |
| an additional `rw` workspace | mounted at its identical path; writes visible on the host at once |
| untracked/ignored content in the clone | absent — the clone carries tracked content only |
| `origin/<base>` inside the clone | resolves; the branch can be cut with no network and no credential |
| the agent's commits, on the host | `git fetch sandbox-<name> <branch>` from the project checkout |
| `git push` from the VM | fails — `could not read Username`. §13.2 holds by itself |

So the repair is two seams, not a rewrite:

1. **`.factory/` moves onto an additional `rw` mount** (`Context.factory_dir`), because the
   clone does not carry untracked files and the host could not see them if it did. Path
   identity — the property §4.2 actually depends on — survives untouched.
2. **The branch comes back to the host once, between `verifying` and `reviewing`**
   (`fetch_back`). From there the run has an ordinary host worktree, and redphase, review
   and deliver work against it exactly as they do for a bind-mounted project. The host
   keeps push authority because the VM never had it.

The one step that cannot follow the branch home is the red-phase replay's *gate*: it runs
the repository's test command, which for this project needs the VM-local `node_modules`.
Its scratch worktree therefore stays inside the clone — see `scratch_add`/`scratch_remove`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from factory import authority, repo
from factory.harness import config_tree
from factory.machine import Blocked
from factory.registry import Project
from factory.repo import GitError
from factory.sandbox.base import Completed, SandboxAdapter
from factory.sandbox.sbx import SbxError
from factory.steps import Context
from factory.store import Run

__all__ = [
    "create_branch",
    "ensure_on_branch",
    "fetch_back",
    "host_worktree_path",
    "refresh_base",
    "release_branch",
    "remote_name",
    "scratch_add",
    "scratch_apply",
    "scratch_remove",
    "seed_context",
]


def remote_name(sandbox: str) -> str:
    """The remote `sbx create --clone` adds to the *host* checkout, for diagnostics only.

    Nothing in the fetch path uses it, and that is deliberate. Measured 2026-08-22: the
    remote's port is reassigned by Docker on every start, the remote is withdrawn when the
    sandbox stops, and starting it again does not restore it. Fetching by this name at the
    `reviewing` entry — which is where a run always is by then — fetches from either a
    missing name or a dead port. `SandboxAdapter.git_daemon_url` reads the live mapping
    instead.
    """
    return f"sandbox-{sandbox}"


def host_worktree_path(ctx: Context) -> Path:
    return ctx.project.worktree_path(ctx.registry.defaults.worktree_subdir, ctx.run.linear_id)


def _exec(
    ctx: Context, argv: list[str], *, workdir: str | None = None, stdin: str | None = None
) -> Completed:
    """One `sbx exec` in the build sandbox, with a failure that names the command.

    Every clone-side git operation goes through here rather than through `repo._git`,
    because the repository these commands act on is the one inside the VM.
    """
    completed = ctx.sandbox.exec_sync(
        ctx.project.build_sandbox,
        argv,
        workdir=workdir,
        env=ctx.env,
        timeout=600,
        stdin=stdin,
    )
    if not completed.ok:
        raise SbxError(
            f"{' '.join(argv)} failed in the clone ({ctx.project.build_sandbox}), exit "
            f"{completed.returncode}:\n{completed.stdout.strip()}\n{completed.stderr.strip()}"
        )
    return completed


# -- worktree_ready: the branch is cut inside the VM -----------------------------------


def create_branch(ctx: Context, branch: str) -> None:
    """Cut the ticket's branch inside the clone. The clone project's `worktree.py`.

    There is no host worktree to make: the clone *is* the working tree, at the same
    absolute path the host uses for the project. `origin/<base>` resolves inside it
    without a network call, so this needs no credential — the property that lets the
    sandbox stay push-less.

    Re-entrant on purpose. A tick that dies after this and before `implementing` comes
    back to a branch that already exists, and the recovery model requires the second call
    to attach to the agent's commits rather than reset over them. So an existing branch is
    checked out, never `-B`-forced.

    `origin` is refreshed first, and only on the cut. See `refresh_base`: a clone is made
    once per project and outlives every run in it, so `origin/<base>` inside it is as old
    as the sandbox. On the re-entrant path there is nothing to refresh into — the branch
    already carries the agent's commits, and moving `origin/<base>` under it would change
    nothing but the diff the review reads.
    """
    project_path = str(ctx.project.path)
    exists = ctx.sandbox.exec_sync(
        ctx.project.build_sandbox,
        ["git", "-C", project_path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        env=ctx.env,
        timeout=120,
    ).ok
    if exists:
        _exec(ctx, ["git", "-C", project_path, "checkout", branch])
        return
    refresh_base(ctx)
    _exec(ctx, ["git", "-C", project_path, "checkout", "-b", branch, ctx.project.base_ref])


def refresh_base(ctx: Context) -> None:
    """Bring the clone's `origin/<base>` up to date before a branch is cut from it.

    **The defect this closes did not fail; it silently built the wrong thing.** The clone
    is created once per project and shared by every run in it, so its `origin/<base>` is
    frozen at whenever the sandbox was made. Measured 2026-08-22 on frontend-harness: the
    host's `origin/v2` was `910a2f1` and the clone's was `1c4422d`, **three merges
    behind**. A branch cut there starts from a base with neither the previous ticket's
    work nor the vendored hook the run is about to be verified by, and every gate, review
    and PR downstream is honest about a tree nobody asked for.

    A failed fetch blocks rather than falling through to the stale ref. Continuing would
    reproduce exactly the failure this exists to prevent, and the staleness would be
    invisible in every artefact the run produces — the run has no way to say "this base is
    of unknown age" once it is past here. A network blip costs a `factory cancel` and a
    re-claim; a stale base costs a ticket's worth of work aimed at the wrong tree.

    The host fetches the private remote and sends only Git objects through a temporary
    bundle on the protocol mount. A sandbox has no remote-read capability: fetching
    `origin` there failed with "could not read Username" in the real CRUD acceptance run.
    The shared host Git lock covers FETCH_HEAD through bundle creation, so another run
    cannot substitute its fetch. The clone must resolve to that exact fetched commit.
    """
    project_path = str(ctx.project.path)
    try:
        ctx.factory_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="base-transfer-", dir=ctx.factory_dir) as directory:
            bundle = Path(directory) / "base.bundle"
            with repo.serialized_git(ctx.project.path):
                repo.fetch(ctx.project.path, ctx.project.base_branch)
                expected = repo.head_sha(ctx.project.path, "FETCH_HEAD")
                repo._git(ctx.project.path, "bundle", "create", str(bundle), "FETCH_HEAD")
            _exec(
                ctx,
                [
                    "git",
                    "-C",
                    project_path,
                    "fetch",
                    str(bundle),
                    f"+FETCH_HEAD:refs/remotes/origin/{ctx.project.base_branch}",
                ],
            )
            actual = _exec(
                ctx, ["git", "-C", project_path, "rev-parse", ctx.project.base_ref]
            ).stdout.strip()
            if actual != expected:
                raise GitError(f"clone base is {actual}, expected fetched commit {expected}")
    except (GitError, SbxError, OSError) as exc:
        raise Blocked(
            "clone-fetch-failed",
            f"could not refresh {ctx.project.base_ref} in {ctx.project.build_sandbox} "
            f"from the host. Refusing to cut a branch from a stale base: {exc}",
        ) from exc
    ctx.log("clone.base_refreshed", base=ctx.project.base_ref, head=expected)


def release_branch(sandbox: SandboxAdapter, project: Project, run: Run) -> list[str]:
    """Undo the run's branch *inside the clone*, at cancel. The clone half of
    `_release_local_debris`, and the second of the two defects that do not fail loudly.

    `_release_local_debris` deletes the **host** branch. For a `--clone` project the branch
    the agent actually worked on lives in the VM and survives untouched — and
    `create_branch` is re-entrant by design, so the next run of the same ticket checks the
    abandoned branch out instead of cutting a fresh one from the refreshed base. The run
    then builds on a dead run's commits and says nothing.

    **Nothing is deleted, only unnamed.** The branch is first written to
    `refs/factory/cancelled/<run id>/<branch>` and only then removed from `refs/heads`.
    The host rule keeps an unpushed branch because its commits are the only copy; the same
    reasoning applies here and more so, since a clone's commits were never pushable at all
    (the VM has no credential — §13.2). So the commits stay reachable, under a ref no step
    ever checks out, and the name that misdirected the next run is gone. `git log
    refs/factory/cancelled/<run id>/<branch>` in the sandbox reads them back.

    Best-effort and never fatal. A stopped or deleted sandbox has no branch to release,
    and a rollback must not fail on debris that is already gone — so every failure is
    reported as a line and the cancel proceeds. The lines are printed, like the host's
    refusals, because what is left behind has to be visible before the next run meets it.
    """
    if not project.requires_clone or not run.branch:
        return []
    branch = run.branch
    project_path = str(project.path)
    env = {k: v.replace("{run}", run.linear_id) for k, v in project.env.items()}

    def git(*argv: str) -> Completed:
        return sandbox.exec_sync(
            project.build_sandbox, ["git", "-C", project_path, *argv], env=env, timeout=120
        )

    if not git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").ok:
        return [f"clone {project.build_sandbox}: no branch {branch} to release"]

    rescue = f"refs/factory/cancelled/{run.id}/{branch}"
    if not git("update-ref", rescue, f"refs/heads/{branch}").ok:
        # Never delete what could not be saved first. Leaving the branch misdirects the
        # next run, which is bad; deleting commits with no copy anywhere is worse.
        return [
            f"clone {project.build_sandbox}: could not save {branch} to {rescue}, so it was "
            f"left in place. The next run of this ticket will check it out instead of "
            f"cutting a fresh branch — delete it by hand first."
        ]

    # Off the branch before deleting it: git refuses to delete the checked-out branch, and
    # the base is where a clone should sit between runs anyway.
    git("checkout", project.base_branch)
    deleted = git("branch", "-D", branch)
    if not deleted.ok:
        return [
            f"clone {project.build_sandbox}: saved {branch} to {rescue} but could not "
            f"delete it ({deleted.stderr.strip() or deleted.stdout.strip()}); delete it by "
            f"hand or the next run will check it out"
        ]
    return [f"clone {project.build_sandbox}: deleted branch {branch} (kept at {rescue})"]


def ensure_on_branch(ctx: Context) -> None:
    """Make sure the clone is on the run's branch before a step reads its tree.

    A `--clone` build sandbox is named once per project and shared across the project's
    runs, and `create_branch` checks out each run's branch inside it at `worktree_ready`.
    So a second run of the same project leaves the clone on *its* branch, and a
    `--from verifying` resume long after (or a re-run after another run repointed the clone)
    would spawn the gate report against the wrong tree — the run's own evidence pointing at
    one branch, the gates running against another. `--from reviewing` dodges this because
    `review.start` calls `fetch_back` and rebuilds a host mirror; verify does not, so this
    is the repair.

    Idempotent: a no-op when the clone is already on the branch (the forward path, where
    the implementer just committed on it). The `exec_sync` checkout starts a stopped
    sandbox, the same way `_fetch_with_the_sandbox_running` does.

    If the branch is gone — the sandbox was recreated, or the clone was reset — block as
    `clone-branch-missing` rather than silently cutting a fresh empty one. A resume that
    has lost the agent's commits is a human decision, not a re-run; `create_branch`'s
    `checkout -b <base>` is right for `worktree_ready` and wrong here.
    """
    branch = ctx.branch
    if branch is None:
        raise Blocked("no-branch", f"run {ctx.run.id} has no branch to check out")
    project_path = str(ctx.project.path)
    exists = ctx.sandbox.exec_sync(
        ctx.project.build_sandbox,
        ["git", "-C", project_path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        env=ctx.env,
        timeout=120,
    ).ok
    if not exists:
        raise Blocked(
            "clone-branch-missing",
            f"the run's branch {branch} is not in the clone {ctx.project.build_sandbox}. "
            "The sandbox was recreated or the clone was reset, and the agent's commits are "
            "gone with it; a resume from here cannot re-run the gates against work that no "
            "longer exists. Re-run the ticket from the start, or restore the branch in the "
            "clone by hand.",
        )
    _exec(ctx, ["git", "-C", project_path, "checkout", branch])


def seed_context(ctx: Context) -> None:
    """`.factory/` on the `rw` mount — the clone project's `worktree._seed`.

    Identical content, different ground: the clone carries no untracked files, so writing
    this into the worktree would put it somewhere the host cannot read and the next run
    would not find. `Context.factory_dir` is the single place that decision is spelled.
    """
    factory_dir = ctx.factory_dir
    (factory_dir / "run").mkdir(parents=True, exist_ok=True)

    staged = ctx.state_dir / "context"
    if staged.is_dir():
        destination = factory_dir / "context"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(staged, destination)

    (factory_dir / "run.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "run_id": ctx.run.id,
                "ticket": ctx.run.linear_id,
                "attempt": ctx.run.attempt,
                "branch": ctx.run.branch,
                "base_ref": ctx.run.base_ref,
                "project": ctx.project.name,
                "clone": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


# -- verifying -> reviewing: the branch comes home -------------------------------------


def fetch_back(ctx: Context) -> Path:
    """Bring the agent's branch out of the VM and into a host worktree. Returns its path.

    Called once at the `reviewing` entry, and that placement is the whole design. §19
    Phase 7 put the fetch at *deliver*, but three things between `verifying` and the push
    need the branch on the host — the red-phase replay's host-side diff, the reviewer
    sandbox (which mounts the host project `:ro` and has no clone of its own), and the
    host-execution guard. Fetching once, early, means every one of them runs unchanged
    against an ordinary worktree, and only this function knows the run was ever a clone.

    Re-entered ticks fetch again, but retain an unchanged clean mirror. Review and
    certification processes may hold its directory open across ticks. Replacing an
    unchanged tree invalidates those directories even when the new bytes are identical.
    Changed mirrors may be replaced only when clean and no run agent is active.
    """
    with repo.serialized_git(ctx.project.path):
        return _fetch_back_locked(ctx)


def _fetch_back_locked(ctx: Context) -> Path:
    branch = ctx.branch
    if branch is None:
        raise Blocked("no-branch", f"run {ctx.run.id} has no branch to fetch back")
    path = host_worktree_path(ctx)

    _fetch_with_the_sandbox_running(ctx, branch)

    fetched = repo.head_sha(ctx.project.path, "FETCH_HEAD")
    if repo.worktree_exists(ctx.project.path, path):
        if not repo.is_clean(path) or repo.in_progress_operation(path):
            raise Blocked("clone-mirror-dirty", "Preserve and reconcile host mirror changes")
        if repo.head_sha(path) == fetched:
            ctx.store.update_run(ctx.run.id, worktree=str(path))
            ctx.refresh()
            return path
    if ctx.store.runtime.db.execute(
        "SELECT 1 FROM agent_leases WHERE run_id=? AND status='active'", (ctx.run.id,)
    ).fetchone():
        raise Blocked(
            "clone-mirror-in-use", "Reconcile active agents before replacing their mirror"
        )

    # A changed, clean, unused mirror is derived from FETCH_HEAD. Rebuild it without
    # resetting or merging host work. A remote-tracking branch may survive deletion,
    # so verify the resulting SHA rather than assuming replacement succeeded.
    repo.remove_worktree(ctx.project.path, path, force=True)
    repo.delete_local_branch(ctx.project.path, branch)
    repo.add_worktree_at_fetch_head(ctx.project.path, path, branch)

    if repo.head_sha(path) != fetched:
        raise Blocked(
            "clone-mirror-stale",
            f"the host mirror of {branch} is at {repo.head_sha(path)[:12]} but the sandbox "
            f"has {fetched[:12]}. A local branch of that name survived on the host and could "
            "not be replaced, so the review would run against code the agent did not write. "
            f"Delete it (`git -C {ctx.project.path} branch -D {branch}`) and re-run.",
        )

    ctx.store.update_run(ctx.run.id, worktree=str(path))
    ctx.refresh()
    ctx.log(
        "clone.fetched_back",
        branch=branch,
        worktree=str(path),
        head=repo.head_sha(path),
    )
    return path


def _fetch_with_the_sandbox_running(ctx: Context, branch: str) -> None:
    """Fetch the branch, having first made sure the daemon on the other end is up.

    Two measured facts, neither visible to the earlier calibration because it never let the
    sandbox stop between the commit and the fetch:

    1. **The `sandbox-<name>` remote is not durable.** `sbx` registers it at `create`,
       withdraws it when the sandbox stops, and does not restore it on the next start. A
       real FRO-6 run failed here with "does not appear to be a git repository" against a
       remote `git remote -v` had listed a minute earlier.
    2. **The daemon's host port is reassigned on every start** (49155 → 49157 → 49159 over
       one afternoon), so even a preserved remote would point at nothing.

    So the URL is looked up live, every time, and the sandbox is started first — the port
    only exists while it is running. This is squarely on the path: the build sandbox's last
    session is the implement step's detached exec, so by `reviewing` it has auto-stopped
    (30 s after the last session, §4.2). The retry covers losing a race against exactly
    that timer between the lookup and the fetch.
    """
    last: str = "the sandbox never reported a git daemon"
    for attempt in (1, 2):
        # An `exec_sync` on a stopped sandbox starts it, and only a running sandbox has a
        # published port. `/bin/true` is the cheapest thing that does it.
        ctx.sandbox.exec_sync(ctx.project.build_sandbox, ["/bin/true"], timeout=300)
        url = ctx.sandbox.git_daemon_url(ctx.project.build_sandbox)
        if url is None:
            continue
        # `sbx` serves the clone under the repository's own directory name.
        source = f"{url}/{ctx.project.path.name}" if url.startswith("git://") else url
        try:
            repo.fetch_from(ctx.project.path, source, branch)
            ctx.log("clone.fetched", source=source, attempt=attempt)
            return
        except GitError as exc:
            last = str(exc)
            ctx.log("clone.fetch_retry", level="warning", attempt=attempt, detail=last[:300])
    raise Blocked(
        "clone-remote-unreachable",
        f"the branch could not be fetched out of {ctx.project.build_sandbox}. Its git "
        "daemon is published only while the sandbox is running, and its host port is "
        "reassigned on every start, so there is nothing durable to fetch from when it is "
        f"down. The agent's commits are still in the VM. Last error: {last}",
    )


# -- reviewing: the red-phase replay's scratch stays in the VM -------------------------


def scratch_path(ctx: Context) -> Path:
    """Inside the clone, because the gate that runs there needs the VM's `node_modules`.

    Untracked, so it exists only in the VM — which is the point. The host never reads it;
    the replay's verdict is read from the gate's output, not from the tree.
    """
    return ctx.project.path / ".factory" / "worktrees" / f"scratch-{ctx.run.id}-{ctx.run.attempt}"


#: The installed-dependency directory a node stack keeps beside `package.json`, and the
#: whole reason this project cannot be bind-mounted. It is gitignored, so a `git worktree
#: add` inside the clone does not carry it.
_DEPENDENCIES = "node_modules"


def scratch_add(ctx: Context, scratch: Path, base_ref: str) -> None:
    """A detached checkout at the base ref, inside the clone, with the dependencies linked.

    The link is not a convenience. Measured 2026-08-22: without it `pnpm test` in the
    scratch exits 1 with "vitest: not found" and "node_modules missing" — no test runs at
    all, and the replay's whole question ("does this test fail without the implementation?")
    goes unanswered on every single frontend run. `redphase._RUNNER_MISSING_SIGNS` is the
    safety net for when this cannot be done; this is the repair.

    A symlink rather than a copy: `pnpm`'s tree is large and mostly symlinks into a store
    already, and the scratch is deleted minutes later. What is linked is the *installed*
    tree, which is the diff's dependency set rather than the base ref's — a real difference
    if the change edited `package.json`, and the honest one to prefer: a replay that runs
    against slightly-newer dependencies answers the question, and one that cannot start
    answers nothing.
    """
    _exec(
        ctx,
        ["git", "-C", str(ctx.project.path), "worktree", "add", "--detach", str(scratch), base_ref],
    )
    scopes = (
        config_tree(ctx.harness, authority.current(ctx) or ctx.worktree)
        if ctx.harness is not None
        else [(Path("."), None)]
    )
    for relative, _ in scopes:
        dependencies = ctx.project.path / relative / _DEPENDENCIES
        linked = ctx.sandbox.exec_sync(
            ctx.project.build_sandbox,
            [
                "/bin/sh",
                "-c",
                '[ -d "$1" ] && mkdir -p "$2" && ln -s "$1" "$3"',
                "scratch-dependencies",
                str(dependencies),
                str(scratch / relative),
                str(scratch / relative / _DEPENDENCIES),
            ],
            env=ctx.env,
            timeout=120,
        )
        ctx.log(
            "clone.scratch_ready", scratch=str(scratch / relative), dependencies_linked=linked.ok
        )


def scratch_apply(ctx: Context, scratch: Path, patch: str) -> None:
    """`git apply` on stdin inside the VM. The patch itself was computed on the host, from
    the worktree `fetch_back` created — the two trees agree because one was fetched from
    the other."""
    _exec(ctx, ["git", "-C", str(scratch), "apply", "-"], stdin=patch)


def scratch_remove(ctx: Context, scratch: Path) -> None:
    """Best effort, and deliberately so: this runs in a `finally`, and a failure to tidy a
    VM-local directory must never replace the replay's verdict with a cleanup error."""
    try:
        _exec(
            ctx,
            [
                "git",
                "-C",
                str(ctx.project.path),
                "worktree",
                "remove",
                "--force",
                str(scratch),
            ],
        )
    except SbxError as exc:
        ctx.log("clone.scratch_remove_failed", level="warning", detail=str(exc)[:500])
