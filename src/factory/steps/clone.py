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

from factory import repo
from factory.machine import Blocked
from factory.sandbox.base import Completed
from factory.sandbox.sbx import SbxError
from factory.steps import Context

__all__ = [
    "create_branch",
    "fetch_back",
    "host_worktree_path",
    "remote_name",
    "scratch_add",
    "scratch_apply",
    "scratch_remove",
    "seed_context",
]


def remote_name(sandbox: str) -> str:
    """The remote `sbx create --clone` adds to the *host* checkout.

    Measured: it resolves to `git://127.0.0.1:<port>/<repo>` — a plain git daemon the
    sandbox runs, not the `ext::sbx exec` transport §19 Phase 7 sketched. Ordinary git
    reaches it, which is why nothing here needs a custom transport.
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
        env=dict(ctx.project.env),
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
    """
    project_path = str(ctx.project.path)
    exists = ctx.sandbox.exec_sync(
        ctx.project.build_sandbox,
        ["git", "-C", project_path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        env=dict(ctx.project.env),
        timeout=120,
    ).ok
    if exists:
        _exec(ctx, ["git", "-C", project_path, "checkout", branch])
        return
    _exec(ctx, ["git", "-C", project_path, "checkout", "-b", branch, ctx.project.base_ref])


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

    Idempotent by rebuilding: a re-entered tick fetches again and recreates the mirror
    from scratch. That is cheap because the mirror is pure derived state, and it avoids
    needing either of the two verbs this repository forbids on a tree the host does not
    author.
    """
    branch = ctx.branch
    if branch is None:
        raise Blocked("no-branch", f"run {ctx.run.id} has no branch to fetch back")
    remote = remote_name(ctx.project.build_sandbox)
    path = host_worktree_path(ctx)

    repo.fetch_from(ctx.project.path, remote, branch)

    # Torn down and rebuilt rather than updated in place. The mirror holds nothing: every
    # byte of it comes from `FETCH_HEAD`, and the host never commits to it. So a
    # re-entered tick gets a fresh copy for free, and this needs neither a hard reset nor
    # a merge — both of which this repository forbids outright, and rightly: on a
    # directory the host does not author, either one would be a way to lose evidence
    # quietly. `delete_local_branch` is a no-op once the branch exists on `origin`, which
    # is why the sha is checked below rather than assumed.
    repo.remove_worktree(ctx.project.path, path, force=True)
    repo.delete_local_branch(ctx.project.path, branch)
    repo.add_worktree_at_fetch_head(ctx.project.path, path, branch)

    fetched = repo.head_sha(ctx.project.path, "FETCH_HEAD")
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
        remote=remote,
        branch=branch,
        worktree=str(path),
        head=repo.head_sha(path),
    )
    return path


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
    dependencies = ctx.project.path / _DEPENDENCIES
    linked = ctx.sandbox.exec_sync(
        ctx.project.build_sandbox,
        [
            "/bin/sh",
            "-c",
            f'[ -d "{dependencies}" ] && ln -s "{dependencies}" "{scratch / _DEPENDENCIES}"',
        ],
        env=dict(ctx.project.env),
        timeout=120,
    )
    ctx.log("clone.scratch_ready", scratch=str(scratch), dependencies_linked=linked.ok)


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
