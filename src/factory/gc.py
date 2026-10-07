"""§16.5 — reclaiming what finished runs left behind.

Every rule here is a *floor*, never a promise: an age makes something eligible, and a
second condition decides whether it may actually go. A pushed branch, an open pull
request and a sandbox the factory did not create are untouchable at any age, because
the cost of keeping them is disk and the cost of removing one is somebody's work.

Two properties hold everywhere in this module.

**`--dry-run` changes nothing.** Every step returns the same `Action` list either way
and only the `done` flag differs, so the plan a dry run prints is the plan the real
sweep performs rather than a second implementation of it that could drift.

**It never removes a host directory tree itself.** `git worktree remove` — with `unlock`
then `--force` when git refuses (F15) — is the only path, and `shutil.rmtree` appears
here exactly once: on an artifact directory the factory wrote and owns end to end.
Inside a build VM it removes only the per-ticket directories a project's env names
with `{run}`, and only by their literal path.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from factory import artifacts, policy, repo
from factory.delivery import forge as forge_dispatch
from factory.isolation import project_for_run
from factory.machine import TERMINAL, State
from factory.registry import Project, Registry
from factory.sandbox import vm_disk
from factory.sandbox.base import SandboxAdapter
from factory.sandbox.sbx import SbxError
from factory.steps import bind_factory_dir
from factory.store import Run, Store

__all__ = ["Action", "sweep", "vm_dir_argv"]

#: The states whose leftovers are collectable. `failed` joins §5.1's two terminal states
#: because §16.5 names it explicitly: a run out of budget is finished in every sense that
#: matters to disk, and §5.3 still leaves it the one edge back to `resumable` — which
#: needs the *branch*, not the worktree, and the branch rules below protect that anyway.
COLLECTABLE: frozenset[State] = TERMINAL | {State.FAILED}

DAY_SECONDS = 86_400


@dataclass(frozen=True)
class Action:
    #: A stable slug — `worktree-remove`, `branch-delete`, `artifact-archive`,
    #: `vm-dir-remove`, `vm-disk`, `sandbox-stop`, `sandbox-remove`, `artifact-delete`,
    #: `distil-backlog`.
    kind: str
    target: str
    #: Why this was done, or — when `done` is False — why it was not. A refusal that
    #: does not say what it refused and why is indistinguishable from a no-op, and the
    #: next run fails on precisely the debris nobody was told about.
    why: str
    done: bool

    def __str__(self) -> str:
        return f"{'did ' if self.done else 'skip'} {self.kind:<18} {self.target}  — {self.why}"


def sweep(
    home: Path,
    registry: Registry,
    store: Store,
    sandbox: SandboxAdapter,
    *,
    dry_run: bool,
    now: float | None = None,
) -> list[Action]:
    """One garbage-collection pass. §16.5's steps, in its order."""
    clock = time.time() if now is None else now
    actions: list[Action] = []
    runs = store.all_runs()
    # A rerun recreates its ticket's worktree and branch at the cancelled run's path, so
    # the path belongs to the ticket's newest run that records it.
    owners: dict[tuple[str, str], Run] = {}
    for run in runs:
        if run.worktree:
            owners.setdefault((run.linear_id, run.worktree), run)
    for run in runs:
        if run.state not in COLLECTABLE:
            continue
        owner = owners.get((run.linear_id, run.worktree or ""), run)
        if owner is not run:
            actions.append(
                Action(
                    "worktree-remove",
                    str(run.worktree),
                    f"retained: run {owner.id} ({owner.state}) now uses it",
                    False,
                )
            )
            continue
        actions += _collect_run(home, registry, store, run, dry_run=dry_run, now=clock)
    try:
        existing = sandbox.names()
    except SbxError as exc:
        actions.append(Action("sandbox-list", "sbx ls", f"refused: {exc}", False))
    else:
        # Before the sandbox step, which may stop the VM this one needs running.
        actions += _sweep_vm_dirs(registry, store, sandbox, existing, dry_run=dry_run)
        actions += _sweep_sandboxes(registry, store, sandbox, existing, dry_run=dry_run, now=clock)
    actions += _distil(registry, dry_run=dry_run)
    actions += _trim_artifacts(home, registry, dry_run=dry_run, now=clock)
    return actions


# --------------------------------------------------------------------------------
# Steps 1-3 — worktree, branch, attempt evidence
# --------------------------------------------------------------------------------


def _resting_since(store: Store, run: Run) -> float:
    """When this run stopped moving — the timestamp §16.5's floor should count from.

    The transition that put the run in the state it is resting in, or `updated_at` when
    there is none (a row that reached its state before the transition log existed, or by
    a path that recorded no hop).

    It used to read `updated_at` directly, and that is not the age of the work — it is the
    last time *anything wrote the row*. Two consequences, both measured 2026-08-23:

    - `factory complete` writes the row, so recording a merge reset the clock. A run
      finished sixty seconds ago was "0 days old" against a 7-day floor, and `complete`
      printed "now collectable" while `gc --dry-run` listed nothing. Both were behaving as
      designed and they contradicted each other in front of the user.
    - `acquire_lease` and its renewal write it too (`store.py:446,456`), so any future
      lease on a finished run would silently grant it another week. A lease records no
      transition, so counting from the transition log closes that on its own.

    Deliberately not special-cased to delivered runs: a cancelled or blocked run is resting
    too, and "how long has this been sitting still" is one question with one answer. §16.5
    stays a floor either way — this only decides when the floor starts counting.
    """
    for row in reversed(store.transitions(run.id)):
        if str(row["to_state"]) == str(run.state):
            return float(row["at"])
    return float(run.updated_at)


def _collect_run(
    home: Path,
    registry: Registry,
    store: Store,
    run: Run,
    *,
    dry_run: bool,
    now: float,
) -> list[Action]:
    age_days = (now - _resting_since(store, run)) / DAY_SECONDS
    floor = registry.defaults.gc.worktree_days
    if age_days < floor:
        return []
    project = registry.projects.get(run.project)
    if project is None:
        return [
            Action(
                "worktree-remove",
                run.linear_id,
                f"project {run.project!r} is no longer in the registry; nothing is touched",
                False,
            )
        ]

    actions: list[Action] = []
    reason = f"{run.state} for {age_days:.0f}d (floor {floor}d)"
    # Order matters and is the reverse of how the run built it: the evidence lives
    # *inside* the worktree for a bind-mounted project, so archiving after the removal
    # would archive nothing and say it succeeded.
    actions += _archive_attempts(home, run, dry_run=dry_run)
    actions += _remove_worktree(project, run, reason, dry_run=dry_run)
    actions += _delete_branch(project, run, reason, dry_run=dry_run)
    return actions


def _archive_attempts(home: Path, run: Run, *, dry_run: bool) -> list[Action]:
    """Step 3 — copy the attempt evidence to `artifacts/` before the worktree goes.

    Scanned for secrets on the way out by `artifacts.archive`, which raises rather than
    redacting: a secret that reached a transcript is compromised and rotation is James's
    call, so hiding it would remove the one fact he needs.
    """
    if not run.worktree:
        return []
    source = bind_factory_dir(Path(run.worktree), run.id) / "run"
    if not source.is_dir():
        return []
    destination = home / "artifacts" / run.linear_id / run.id
    # Deliver and cancel may have archived some attempts already; their copies stay.
    pending = [
        attempt
        for attempt in sorted(source.iterdir())
        if attempt.is_dir() and not (destination / attempt.name).exists()
    ]
    if not pending:
        return [Action("artifact-archive", str(destination), "already archived", False)]
    names = ", ".join(attempt.name for attempt in pending)
    if dry_run:
        return [Action("artifact-archive", str(destination), f"would copy {names}", False)]
    for attempt in pending:
        artifacts.archive(attempt, destination / attempt.name)
    return [Action("artifact-archive", str(destination), f"copied {names} from {source}", True)]


def _remove_worktree(project: Project, run: Run, why: str, *, dry_run: bool) -> list[Action]:
    if not run.worktree:
        return []
    path = Path(run.worktree)
    if not repo.worktree_exists(project.path, path) and not path.exists():
        return []
    if dry_run:
        return [Action("worktree-remove", str(path), why, False)]
    try:
        repo.remove_worktree(project.path, path, force=True)
    except repo.GitError as exc:
        # Named rather than swallowed. `remove_worktree` already tries `unlock` then
        # `--force`; if git still refuses there is something here git will not discard,
        # and `rm -rf` is not the answer to that (F15).
        return [Action("worktree-remove", str(path), f"git refused: {exc}", False)]
    return [Action("worktree-remove", str(path), why, True)]


def _delete_branch(project: Project, run: Run, why: str, *, dry_run: bool) -> list[Action]:
    """Step 2 — local branches only, and only when nothing else refers to them.

    Three independent refusals, and every one of them is somebody else's work: a branch
    that was pushed is visible to other people, a branch carrying commits beyond the base
    ref is an implementation, and a branch with an open PR is under review. `gh` being
    unreachable counts as "there might be a PR" — an unanswered question is not a yes.
    """
    branch = run.branch
    if not branch or not repo.local_branch_exists(project.path, branch):
        return []
    keep = repo.reason_to_keep_branch(project.path, branch, run.base_ref or project.base_ref)
    if keep:
        return [Action("branch-delete", branch, f"kept: {keep}", False)]
    if forge_dispatch.for_project(project).find_pr(project.path, branch):
        return [Action("branch-delete", branch, "kept: it has an open pull request", False)]
    if dry_run:
        return [Action("branch-delete", branch, why, False)]
    repo.delete_local_branch(project.path, branch)
    return [Action("branch-delete", branch, why, True)]


# --------------------------------------------------------------------------------
# Per-ticket directories inside a build VM
# --------------------------------------------------------------------------------

#: Prints `<status> <path>` for each candidate directory that exists. A path that resolves
#: elsewhere, through a symlink or `..` anywhere along it, is refused: the bind-mounted
#: workspace is writable from inside the VM, and a link must not aim the removal at it.
#: `rm` runs from inside the checked parent, so a link swapped in after the check is
#: removed itself rather than followed.
_VM_DIR_SCRIPT = """mode=$1; shift
for p; do
  [ -d "$p" ] || continue
  parent=${p%/*}
  if [ "$(realpath -e -- "$p")" != "$p" ]; then echo "refused $p"
  elif [ "$mode" = list ]; then echo "present $p"
  elif (cd -P -- "$parent" && [ "$(pwd -P)" = "$parent" ] &&
        rm -rf --one-file-system -- "./${p##*/}"); then echo "removed $p"
  else echo "failed $p"; fi
done"""

_VM_DIR_STATUS: dict[str, tuple[str, bool]] = {
    "present": ("would remove: every run of its ticket is terminal", False),
    "removed": ("every run of its ticket is terminal", True),
    "refused": ("refused: the path resolves elsewhere", False),
    "failed": ("refused: rm -rf failed", False),
}


def vm_dir_argv(mode: Literal["list", "remove"], paths: Sequence[str]) -> list[str]:
    return ["/bin/sh", "-c", _VM_DIR_SCRIPT, "sh", mode, *paths]


def _sweep_vm_dirs(
    registry: Registry,
    store: Store,
    sandbox: SandboxAdapter,
    existing: set[str],
    *,
    dry_run: bool,
) -> list[Action]:
    """Remove the VM-local directory a project's env gives each ticket through `{run}`.

    `UV_PROJECT_ENVIRONMENT=/home/agent/venvs/<project>/{run}` is the live case. A shared
    build VM outlives every ticket it serves, so these accumulate on its root filesystem,
    the one that ran out of inodes on 2026-09-13. A ticket's directory goes only when every
    run of that ticket is terminal; `failed` is not, because it can be re-authorised. There
    is no age floor: the directory holds no work, and removing it costs one `uv sync`.
    """
    tickets: dict[tuple[str, str], list[Run]] = {}
    for run in store.all_runs():
        tickets.setdefault((run.project, run.linear_id), []).append(run)
    by_vm: dict[str, set[str]] = {}
    for (project_name, ticket), runs in tickets.items():
        project = registry.projects.get(project_name)
        if project is None or any(run.state not in TERMINAL for run in runs):
            continue
        for run in runs:
            vm = project_for_run(project, run, store).build_sandbox
            by_vm.setdefault(vm, set()).update(_ticket_dirs(project, ticket))
    actions: list[Action] = []
    for vm, dirs in sorted(by_vm.items()):
        if dirs and vm in existing and policy.sandbox_is_factory_owned(vm):
            actions += _clear_vm_dirs(sandbox, vm, sorted(dirs), dry_run=dry_run)
    return actions


def _ticket_dirs(project: Project, ticket: str) -> list[str]:
    """Only an env value under the agent's home whose last component is `{run}` names one
    directory per ticket. Any other value is not the factory's to remove."""
    # `PurePosixPath` drops a `.` or empty component, which would name every ticket's
    # directory at once.
    if ticket in ("", ".", "..") or "/" in ticket:
        return []
    dirs = []
    for value in project.env.values():
        template = PurePosixPath(value)
        if (
            template.name == "{run}"
            and value.count("{run}") == 1
            and template.parent.is_relative_to(vm_disk.VM_HOME)
            and ".." not in template.parts
        ):
            dirs.append(str(template.parent / ticket))
    return dirs


def _clear_vm_dirs(
    sandbox: SandboxAdapter, vm: str, dirs: list[str], *, dry_run: bool
) -> list[Action]:
    # `sbx exec` starts a stopped VM, and a sweep must not undo a `factory suspend`; the
    # doctor's VM disk check skips one for the same reason.
    try:
        state = sandbox.inspect(vm).get("state")
    except (SbxError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return [Action("vm-dir-remove", vm, f"refused: {exc}", False)]
    if state != "running":
        why = f"{state}; gc does not start a VM to clean it"
        return [Action("vm-dir-remove", vm, why, False)]
    before = None if dry_run else _home_free(sandbox, vm)
    try:
        done = sandbox.exec_sync(
            vm, vm_dir_argv("list" if dry_run else "remove", dirs), timeout=600
        )
    except subprocess.TimeoutExpired as exc:
        return [Action("vm-dir-remove", vm, f"refused: timed out after {exc.timeout}s", False)]
    actions: list[Action] = []
    for line in done.stdout.splitlines():
        status, _, path = line.partition(" ")
        if status in _VM_DIR_STATUS:
            why, did = _VM_DIR_STATUS[status]
            actions.append(Action("vm-dir-remove", f"{vm}:{path}", why, did))
    if not done.ok:
        first = (done.stderr or done.stdout).strip().splitlines()
        why = f"refused: exit {done.returncode}: {first[0] if first else ''}"
        actions.append(Action("vm-dir-remove", vm, why, False))
    if before is not None and any(action.done for action in actions):
        why = f"before {before}; after {_home_free(sandbox, vm)}"
        actions.append(Action("vm-disk", vm, why, True))
    return actions


def _home_free(sandbox: SandboxAdapter, vm: str) -> str:
    try:
        return vm_disk.summary(vm_disk.measure(sandbox, vm, (vm_disk.VM_HOME,)))
    except vm_disk.ProbeError as exc:
        return f"unmeasured ({exc})"


# --------------------------------------------------------------------------------
# Step 4 — sandboxes
# --------------------------------------------------------------------------------


def _sweep_sandboxes(
    registry: Registry,
    store: Store,
    sandbox: SandboxAdapter,
    existing: set[str],
    *,
    dry_run: bool,
    now: float,
) -> list[Action]:
    """Stop an idle factory sandbox; remove one only after much longer.

    The names come from the **registry**, never from `sbx ls`, which only drops the
    names with no sandbox behind them. That is the difference between collecting the
    factory's own sandboxes and collecting whatever happens to be on the machine, and
    `policy.assert_factory_sandbox` in the adapter is the second lock on the same door: a
    `codex-*` sandbox is James's live `csbx` session and an unattended writer inside a
    live human session is the failure that rule prevents.
    """
    actions: list[Action] = []
    idle_floor = registry.defaults.gc.sandbox_idle_hours * 3600
    rm_floor = registry.defaults.gc.sandbox_rm_days * DAY_SECONDS

    for project in registry.projects.values():
        active = store.active_runs_for_project(project.name)
        users_of: dict[str, list[Run]] = {project.build_sandbox: [], project.review_sandbox: []}
        builders: dict[str, list[Run]] = {}
        for run in store.all_runs():
            if run.project == project.name:
                resolved = project_for_run(project, run, store)
                for used in {resolved.build_sandbox, resolved.review_sandbox}:
                    users_of.setdefault(used, []).append(run)
                builders.setdefault(resolved.build_sandbox, []).append(run)
        for name in sorted(users_of.keys() & existing):
            if not name or not policy.sandbox_is_factory_owned(name):
                continue
            users = users_of[name]
            busy = [run for run in users if run in active]
            if busy:
                actions.append(
                    Action("sandbox-stop", name, f"{len(busy)} run(s) still using it", False)
                )
                continue
            last_activity = max(
                (run.updated_at for run in users), default=_last_activity(store, project.name)
            )
            idle = now - last_activity
            # A stopped build VM keeps its disk; a removed one loses the Claude session the
            # run's next attempt resumes with `--resume` (#130). Review launches never resume.
            resumer = next(
                (run for run in builders.get(name, []) if run.state not in TERMINAL), None
            )
            if idle >= rm_floor and resumer is None:
                actions += _act_on_sandbox(
                    sandbox,
                    "sandbox-remove",
                    name,
                    f"idle {idle / DAY_SECONDS:.0f}d",
                    dry_run=dry_run,
                )
            elif idle >= idle_floor:
                if idle >= rm_floor and resumer is not None:
                    why = f"kept: run {resumer.id} ({resumer.state}) can still resume in it"
                    actions.append(Action("sandbox-remove", name, why, False))
                actions += _act_on_sandbox(
                    sandbox, "sandbox-stop", name, f"idle {idle / 3600:.0f}h", dry_run=dry_run
                )
    return actions


def _act_on_sandbox(
    sandbox: SandboxAdapter, kind: str, name: str, why: str, *, dry_run: bool
) -> list[Action]:
    if dry_run:
        return [Action(kind, name, why, False)]
    if not sandbox.exists(name):
        return [Action(kind, name, "no such sandbox", False)]
    try:
        if kind == "sandbox-remove":
            sandbox.remove(name)
        else:
            sandbox.stop(name)
    except (PermissionError, OSError, SbxError) as exc:
        return [Action(kind, name, f"refused: {exc}", False)]
    return [Action(kind, name, why, True)]


def _last_activity(store: Store, project: str) -> float:
    stamps = [run.updated_at for run in store.all_runs() if run.project == project]
    return float(max(stamps)) if stamps else 0.0


# --------------------------------------------------------------------------------
# Step 5 — the distiller the detached runs never got to run
# --------------------------------------------------------------------------------


def _distil(registry: Registry, *, dry_run: bool) -> list[Action]:
    """`distil_backlog.mjs --run` in each project — §16.5 step 5.

    A detached run that is killed never fires layer A's `SessionEnd`, so its learnings sit
    in the backlog unwritten. This is the sweep that catches them, and it is layer A's
    script rather than a second implementation: the factory holds no distillation rule.
    """
    actions: list[Action] = []
    for project in registry.projects.values():
        script = project.path / ".agents" / "vendor" / "harness" / "hooks" / "distil_backlog.mjs"
        if not script.exists():
            actions.append(Action("distil-backlog", project.name, "no vendored script", False))
            continue
        if dry_run:
            actions.append(Action("distil-backlog", project.name, f"would run {script}", False))
            continue
        proc = subprocess.run(
            ["node", str(script), "--run"],
            cwd=str(project.path),
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        actions.append(
            Action(
                "distil-backlog",
                project.name,
                "ok" if proc.returncode == 0 else f"exit {proc.returncode}",
                proc.returncode == 0,
            )
        )
    return actions


# --------------------------------------------------------------------------------
# Step 6 — artifacts, oldest first, only while the disk is below the floor
# --------------------------------------------------------------------------------


def _trim_artifacts(home: Path, registry: Registry, *, dry_run: bool, now: float) -> list[Action]:
    """Delete artifact directories past `artifact_days`, oldest first, and stop as soon
    as the disk is above `disk.min_free_gb` again.

    Age alone is not enough to delete evidence. The stopping condition is the whole
    point: this is the one step that destroys the record of a run, so it does the least
    it can to get back over the floor and then stops, rather than clearing everything
    that happens to be old enough.
    """
    root = home / "artifacts"
    if not root.is_dir():
        return []
    floor = registry.defaults.disk_min_free_gb
    if _free_gb(home) >= floor:
        return []

    cutoff = now - registry.defaults.gc.artifact_days * DAY_SECONDS
    stale = sorted(
        (d for d in root.rglob("*") if d.is_dir() and d.stat().st_mtime < cutoff),
        key=lambda d: d.stat().st_mtime,
    )
    actions: list[Action] = []
    for directory in stale:
        if _free_gb(home) >= floor:
            break
        why = f"older than {registry.defaults.gc.artifact_days}d, disk below {floor} GB"
        if dry_run:
            actions.append(Action("artifact-delete", str(directory), why, False))
            continue
        shutil.rmtree(directory, ignore_errors=True)
        actions.append(Action("artifact-delete", str(directory), why, True))
    return actions


def _free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 1_000_000_000
