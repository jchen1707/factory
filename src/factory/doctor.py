"""`factory doctor` — what is true about this machine, and what could not be checked.

§20.5. Eighteen checks over the config, the external tools, the host, the state and the
projects, in a module of their own rather than in an argparse handler — which is where
they were, as ~100 lines of discovery, execution and formatting mixed together, over
checks with four different return shapes.

## Three statuses, not two

The bug this module is shaped to remove: three whole check families — the vendored
layer-A rows, the sensitive-path rows and the deep canary — were written behind
`if registry:`, so a broken `projects.toml` made them **vanish from the output**. A
doctor that reports fewer failures the more broken the machine is is worse than no
doctor. They now report `skipped`, and the summary counts them.

`skipped` is not a failure and does not change the exit code (still 1 on failures only):
"I could not check this" is an honest third answer, and treating it as a failure would
make a machine with no `sbx` installed look broken rather than unmeasured. But it is
counted out loud, so the output cannot be read as a clean bill of health. This is the
same distinction `gate_report.mjs` draws between `pass` and `skipped_unchanged`, which
already cost a Phase 2 defect when it was collapsed.

## One shape

Every check is `Callable[[DoctorContext], Sequence[Result]]`. The context (`home`,
`registry`, `store`) is produced by the config checks and passed to every other one, so
the `if registry:` threading that used to live in the handler has one place to live and
one place to be checked. Zero-argument closures were considered and rejected: half the
checks need the registry, so the threading would just move into the list construction.

Deliberately **no console view** in this module. That is a page with its own layout
decisions, and the console has its own plan.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from factory import doctrine, machine
from factory.harness import vendor_check
from factory.intake.linear import LinearError, keychain_secret
from factory.registry import (
    IMPLEMENT_SKILL,
    PLUGIN_CACHE,
    Project,
    Registry,
    RegistryError,
    load_registry,
)
from factory.routing import Routing, RoutingError, load_routing
from factory.sandbox import vm_disk
from factory.sandbox.sbx import SbxAdapter, SbxError, sbx_available
from factory.steps import review as review_step
from factory.steps.sandbox import layer_a_hooks_wired
from factory.store import Store

__all__ = ["CHECKS", "Check", "DoctorContext", "Result", "Status", "check", "report", "run"]


class Status(StrEnum):
    OK = "ok"
    FAIL = "fail"
    #: The check could not run because something it depends on did not load. Not a
    #: failure of the machine — a hole in what this report knows about it.
    SKIPPED = "skipped"


@dataclass(frozen=True)
class Result:
    name: str
    status: Status
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is Status.OK


@dataclass(frozen=True)
class DoctorContext:
    """What every check is handed. Produced by the config checks, which run first.

    `registry` and `store` are optional because the whole point is that a doctor run
    survives them failing to load, and says which checks that cost.
    """

    home: Path
    registry: Registry | None = None
    routing: Routing | None = None
    store: Store | None = None
    deep: bool = False


@dataclass(frozen=True)
class Check:
    name: str
    run: Callable[[DoctorContext], Sequence[Result]]
    #: What this check cannot run without: `"registry"` (the one that used to make checks
    #: disappear) or `"routing"`.
    needs: tuple[str, ...] = ()
    #: Opt-in, because it spends a model call.
    deep: bool = False


def _one(name: str, ok: bool, detail: str = "") -> list[Result]:
    return [Result(name, Status.OK if ok else Status.FAIL, detail)]


def _from_triple(triple: tuple[str, bool, str]) -> list[Result]:
    """Adapter for the checks that were written as `(name, ok, detail)` and stay that way.

    Kept rather than rewritten: their bodies are the measured knowledge this module is
    made of, and a mechanical reshaping of ten functions is ten chances to change one by
    accident.
    """
    name, ok, detail = triple
    return _one(name, ok, detail)


# --------------------------------------------------------------------------------
# the config checks, which produce the context
# --------------------------------------------------------------------------------


#: What a config file can fail with. `TOMLDecodeError` is in here and was not in the
#: handler this replaced: `load_registry` does not wrap a syntax error in `RegistryError`,
#: so `factory doctor` against an unparseable `projects.toml` — the exact machine that
#: most needs a doctor — exited with a traceback instead of a report.
_CONFIG_ERRORS = (RegistryError, RoutingError, OSError, KeyError, tomllib.TOMLDecodeError)


def load_context(home: Path, *, deep: bool = False) -> tuple[DoctorContext, list[Result]]:
    """Run the config checks and build what the rest of them need.

    First, and separately, because everything downstream is either impossible or a lie
    without it. What used to happen instead was that `registry` stayed `None` and three
    check families quietly did not appear.
    """
    results: list[Result] = []

    registry: Registry | None = None
    try:
        registry = load_registry(home / "config" / "projects.toml")
        results += _one("registry", True, f"{len(registry.projects)} projects")
    except _CONFIG_ERRORS as exc:
        results += _one("registry", False, str(exc))

    routing: Routing | None = None
    try:
        routing = load_routing(home / "config" / "models.toml")
        results += _one(
            "routing",
            True,
            f"builder={routing.roles['builder'].model}/{routing.roles['builder'].effort}, "
            f"reviewer={routing.roles['reviewer'].model}, ceiling ${routing.usd_per_run:g}",
        )
    except _CONFIG_ERRORS as exc:
        results += _one("routing", False, str(exc))

    try:
        machine.assert_table_is_sound()
        results += _one("state table", True, f"{len(machine.TRANSITIONS)} states")
    except AssertionError as exc:
        results += _one("state table", False, str(exc))

    store: Store | None = None
    try:
        store = Store(home / "state" / "factory.db")
        ok, detail = store.integrity_ok()
        results += _one("database", ok, f"{store.path} — {detail}")
    except Exception as exc:  # a database that will not open is a fact, not a crash
        results += _one("database", False, str(exc))

    return (
        DoctorContext(home=home, registry=registry, routing=routing, store=store, deep=deep),
        results,
    )


# --------------------------------------------------------------------------------
# the checks themselves
# --------------------------------------------------------------------------------


def _tools(_ctx: DoctorContext) -> list[Result]:
    results: list[Result] = []
    for argv in (["git", "--version"], ["gh", "auth", "status"]):
        results += _from_triple(_tool_check(argv))
    return results


def _sbx(_ctx: DoctorContext) -> list[Result]:
    ok, detail = sbx_available()
    return _one("sbx", ok, detail.splitlines()[0] if detail else "")


def _global_gitignore(_ctx: DoctorContext) -> list[Result]:
    return _from_triple(_global_gitignore_check())


def _keychain(_ctx: DoctorContext) -> list[Result]:
    return _from_triple(_keychain_check())


def _doctrine(ctx: DoctorContext) -> list[Result]:
    """`config/doctrine.toml` against the plugin cache, and the inlined `implement` skill."""
    results = _one(
        "implement skill (inlined)",
        IMPLEMENT_SKILL.exists(),
        str(IMPLEMENT_SKILL) if IMPLEMENT_SKILL.exists() else f"{IMPLEMENT_SKILL} is missing",
    )
    try:
        declared = doctrine.load(doctrine.config_path(ctx.home))
    except doctrine.DoctrineError as exc:
        return results + _one("doctrine", False, str(exc))
    problems = doctrine.problems(declared, PLUGIN_CACHE)
    sources = ", ".join(f"{s.plugin} {s.version}" for s in declared.sources)
    return results + _one(
        "doctrine",
        not problems,
        "; ".join(problems)
        or (
            f"{len(declared.skills)} skills from {sources}" if declared.skills else "none declared"
        ),
    )


def _factory_home_absent(_ctx: DoctorContext) -> list[Result]:
    return _one(
        "~/.factory absent",
        not (Path.home() / ".factory").exists(),
        "sbx skills import scans ~/.factory/skills, which is Factory.ai's Droid",
    )


def _disk(ctx: DoctorContext) -> list[Result]:
    free_gb = shutil.disk_usage(ctx.home).free / 1_000_000_000
    floor = ctx.registry.defaults.disk_min_free_gb if ctx.registry else 20
    return _one("disk", free_gb >= floor, f"{free_gb:.0f} GB free, floor {floor} GB")


def _vendored_layer_a(ctx: DoctorContext) -> list[Result]:
    registry = ctx.registry
    if registry is None:  # unreachable: `needs` guards it. Typed, not asserted.
        return []
    results: list[Result] = []
    for project in registry.projects.values():
        ok, detail = vendor_check(
            project.path, Path.home() / "harness" / "scripts" / "vendor_sync.py"
        )
        results += _one(
            f"vendored layer A in {project.name}", ok, detail.splitlines()[-1] if detail else ""
        )
    return results


def _sensitive_paths(ctx: DoctorContext) -> list[Result]:
    registry = ctx.registry
    if registry is None:  # unreachable: `needs` guards it. Typed, not asserted.
        return []
    results: list[Result] = []
    for project in registry.projects.values():
        results += _from_triple(_sensitive_paths_check(project))
    return results


def _sandbox_delivery(ctx: DoctorContext) -> list[Result]:
    """For each project that delivers from inside its own sandbox: is it provisioned?

    Only one project declares `[sandbox_delivery]`, and for every other one this check
    contributes no rows at all — which is the right shape. A row saying "not applicable"
    for four projects would bury the one row that means something.

    The placeholder is host state, not code: it survives a `git revert` and it disappears
    with an `sbx secret rm` nobody remembers doing. `steps/sandbox.py` already fails the
    preflight on its absence, but that is a *run* failing, and a doctor exists so the
    machine can be asked before a run is started.

    `skipped` rather than `fail` with no `sbx`. The placeholder cannot be looked up at
    all then, and "I could not check this" is the honest answer — the same distinction
    this module's header draws for every other check that depends on an external tool.
    """
    registry = ctx.registry
    if registry is None:  # unreachable: `needs` guards it. Typed, not asserted.
        return []
    declared = [
        (project, project.sandbox_delivery)
        for project in registry.projects.values()
        if project.sandbox_delivery is not None
    ]
    if not declared:
        return []
    available, _ = sbx_available()
    if not available:
        return [
            Result(f"sandbox delivery for {project.name}", Status.SKIPPED, "sbx is not available")
            for project, _ in declared
        ]
    adapter = SbxAdapter()
    results: list[Result] = []
    for project, delivery in declared:
        name = delivery.placeholder_env
        placeholder = adapter.custom_secret_placeholder(project.build_sandbox, name)
        results += _one(
            f"sandbox delivery for {project.name}",
            placeholder is not None,
            f"{name} -> {placeholder}"
            if placeholder
            else (
                f"no custom secret {name!r} is scoped to {project.build_sandbox}; "
                f"`sbx secret set-custom --sandbox {project.build_sandbox} --host "
                f"{delivery.api_url.removeprefix('https://')} --env {name} --value <token>` "
                "provisions it (the token stays on the host; the sandbox sees a placeholder)"
            ),
        )
    return results


def _build_vm_disk(ctx: DoctorContext) -> list[Result]:
    """The preflight's `vm-disk-floor`, asked of every running build VM before a run.

    A stopped VM is skipped, because `sbx exec` would start it (measured 2026-10-07) and
    a doctor must not undo a `factory suspend`. The preflight measures it when a run
    starts it. A VM not yet created passes: the factory creates it fresh.
    """
    registry = ctx.registry
    if registry is None:  # unreachable: `needs` guards it. Typed, not asserted.
        return []
    projects = list(registry.projects.values())
    adapter = SbxAdapter()
    try:
        existing = adapter.names()
    except (SbxError, OSError, subprocess.TimeoutExpired) as exc:
        return [
            Result(f"build VM disk for {p.name}", Status.SKIPPED, f"sbx is not available: {exc}")
            for p in projects
        ]
    defaults = registry.defaults
    results: list[Result] = []
    for project in projects:
        name = f"build VM disk for {project.name}"
        if project.build_sandbox not in existing:
            results += _one(name, True, f"{project.build_sandbox} is not created yet")
            continue
        try:
            stopped = adapter.inspect(project.build_sandbox).get("state") == "stopped"
        except (SbxError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
            results.append(Result(name, Status.SKIPPED, f"sbx inspect failed: {exc}"))
            continue
        if stopped:
            results.append(
                Result(name, Status.SKIPPED, f"{project.build_sandbox} is stopped; not started")
            )
            continue
        try:
            rows = vm_disk.measure(
                adapter, project.build_sandbox, (str(project.path), vm_disk.VM_HOME)
            )
        except vm_disk.ProbeError as exc:
            results += _one(name, False, str(exc))
            continue
        short = vm_disk.shortfalls(
            rows,
            min_free_gb=defaults.vm_min_free_gb,
            min_free_inodes=defaults.vm_min_free_inodes,
        )
        results += _one(name, not short, "; ".join(short) or vm_disk.summary(rows))
    return results


def _hooks_wired(ctx: DoctorContext) -> list[Result]:
    """The preflight's `layer-a-hooks-wired`, asked before a run rather than by one."""
    registry = ctx.registry
    if registry is None:  # unreachable: `needs` guards it. Typed, not asserted.
        return []
    results: list[Result] = []
    for project in registry.projects.values():
        results += _one(f"layer-A hooks in {project.name}", *layer_a_hooks_wired(project))
    return results


def _anthropic(_ctx: DoctorContext) -> list[Result]:
    name = "anthropic credential (sbx)"
    try:
        proc = subprocess.run(
            ["sbx", "secret", "ls"], capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [Result(name, Status.SKIPPED, f"sbx unusable: {exc}")]
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return [Result(name, Status.SKIPPED, detail[0] if detail else "sbx secret ls failed")]
    return [anthropic_credential(proc.stdout)]


def anthropic_credential(secret_ls: str) -> Result:
    """Does `sbx secret ls` list a global `anthropic` service secret? Presence, not validity.

    Measured: the service table's rows read `<scope> service <name> <state>`, e.g.
    `(global) service openai (oauth configured)`. How the anthropic secret is listed is
    not measured, for either route: the API key (`sbx secret set anthropic`) or the OAuth
    token `sbx run claude <dir> -- auth login` stores. So no global row is `skipped`, not
    `fail`: absent and listed some other way look the same from here, and the deep ping
    is the check that tells them apart.
    """
    name = "anthropic credential (sbx)"
    service_rows = secret_ls.split("CUSTOM SECRETS", 1)[0].splitlines()
    rows = [line.split() for line in service_rows if "anthropic" in line.split()]
    listed = "; ".join(" ".join(row) for row in rows)
    if any(row[0] == "(global)" for row in rows):
        return Result(name, Status.OK, f"listed ({listed}); validity is the deep ping's to prove")
    return Result(
        name,
        Status.SKIPPED,
        (f"anthropic is scoped to one sandbox only ({listed}). " if rows else "")
        + "No global anthropic row in `sbx secret ls`, and how sbx lists the OAuth route's "
        "token is unmeasured. `factory doctor --deep` decides; docs/runbook.md has both routes",
    )


def _plan_copy(ctx: DoctorContext) -> list[Result]:
    return _from_triple(_plan_copy_check(ctx.home))


def _live(ctx: DoctorContext) -> list[Result]:
    """`live_probe` against the first project, after naming what stops it from starting."""
    from factory import live_probe

    registry, routing = ctx.registry, ctx.routing
    if registry is None or routing is None:  # unreachable: `needs` guards it.
        return []
    available, detail = sbx_available()
    if not available:
        return [Result("live: sandbox", Status.FAIL, live_probe.sbx_unready(detail))]
    project = next(iter(registry.projects.values()))
    return live_probe.run(
        SbxAdapter(),
        project,
        root=ctx.home / "state" / "doctor" / project.name,
        builder_model=routing.roles["builder"].model,
        deny_network=registry.defaults.deny_network,
    )


#: Every check, in report order. The config four are not here — they run first, in
#: `load_context`, because they produce what the rest are handed.
CHECKS: tuple[Check, ...] = (
    Check("external tools", _tools),
    Check("sbx", _sbx),
    Check("global gitignore", _global_gitignore),
    Check("anthropic credential (sbx)", _anthropic),
    Check("linear credential", _keychain),
    Check("doctrine", _doctrine),
    Check("~/.factory absent", _factory_home_absent),
    Check("disk", _disk),
    Check("vendored layer A", _vendored_layer_a, needs=("registry",)),
    Check("sensitive paths", _sensitive_paths, needs=("registry",)),
    Check("layer-A hooks", _hooks_wired, needs=("registry",)),
    Check("sandbox delivery", _sandbox_delivery, needs=("registry",)),
    Check("build VM disk", _build_vm_disk, needs=("registry",)),
    Check("plan copy", _plan_copy),
    Check("claude live probe", _live, needs=("registry", "routing"), deep=True),
)


def check(name: str) -> Check:
    """The check with this name — the way a test reaches one.

    Named lookup rather than an imported private: three of these checks were tested by
    importing a `cli` private, which is testing past the interface. Exercising one
    through the same `Check` the report runs is the point of giving them all one shape.
    """
    for candidate in CHECKS:
        if candidate.name == name:
            return candidate
    raise KeyError(f"no doctor check named {name!r}; one of {[c.name for c in CHECKS]}")


def run(home: Path, *, deep: bool = False) -> list[Result]:
    """Every check, in order, against one machine. Nothing here prints."""
    ctx, results = load_context(home, deep=deep)
    have = {
        "registry": ctx.registry is not None,
        "routing": ctx.routing is not None,
        "store": ctx.store is not None,
    }

    for candidate in CHECKS:
        if candidate.deep and not deep:
            continue
        missing = [need for need in candidate.needs if not have[need]]
        if missing:
            # The whole reason this module exists. These rows used to not be printed at
            # all when the registry failed to load.
            results.append(
                Result(
                    candidate.name,
                    Status.SKIPPED,
                    f"{', '.join(missing)} did not load",
                )
            )
            continue
        results.extend(candidate.run(ctx))
    return results


def report(results: Sequence[Result]) -> tuple[list[str], int]:
    """The printed lines and the exit code.

    Exit 1 on failures **only**. A skip is "I could not check this", not a broken
    machine — but the summary names both counts, so no output where something was
    skipped can be read as a clean bill of health.
    """
    width = max((len(r.name) for r in results), default=0)
    lines = [f"{r.status.value.upper():<7} {r.name.ljust(width)}  {r.detail}" for r in results]
    failures = sum(1 for r in results if r.status is Status.FAIL)
    skipped = sum(1 for r in results if r.status is Status.SKIPPED)

    lines.append("")
    if failures and skipped:
        lines.append(f"{failures} check(s) failed, {skipped} skipped.")
    elif failures:
        lines.append(f"{failures} check(s) failed.")
    elif skipped:
        lines.append(f"All checks that ran passed; {skipped} skipped.")
    else:
        lines.append("All checks passed.")
    return lines, (1 if failures else 0)


# --------------------------------------------------------------------------------
# the check bodies, moved from `cli.py` unchanged
# --------------------------------------------------------------------------------


def _sensitive_paths_check(project: Project) -> tuple[str, bool, str]:
    """Does this project's §15.2 sensitive-path list still match anything it names?

    This check exists because the failure it catches is silent. `_SENSITIVE_DIRS` held
    `src/**/routes/**` for the frontend stack and there has never been a `routes`
    directory in that repository: measured 2026-08-23, the glob matched **0 of 192**
    tracked files. A Tier-2 trigger that cannot fire and a Tier-2 trigger that happened
    not to fire produce byte-identical evidence, so nothing in four phases of runs
    noticed. Moving the list to the registry makes it editable; only this makes it
    checkable.

    An empty list is fine — a project that has not named any sensitive directory is
    reporting a decision, not a drift. A non-empty list matching nothing is the bug.
    """
    name = f"sensitive paths in {project.name}"
    if not project.sensitive_paths:
        return name, True, "none declared"
    try:
        tracked = subprocess.run(
            ["git", "-C", str(project.path), "ls-files"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return name, False, str(exc)
    if tracked.returncode != 0:
        return (
            name,
            False,
            (tracked.stderr or "").strip().splitlines()[:1][0]
            if tracked.stderr
            else "git ls-files failed",
        )
    files = tracked.stdout.split()
    dead = [
        glob
        for glob in project.sensitive_paths
        if not any(review_step._matches_any(f, (glob,)) for f in files)
    ]
    if dead:
        return name, False, f"matches no tracked file: {', '.join(dead)}"
    hits = sum(1 for f in files if review_step._matches_any(f, project.sensitive_paths))
    return name, True, f"{len(project.sensitive_paths)} glob(s), {hits} files"


def _plan_copy_check(home: Path) -> tuple[str, bool, str]:
    """Is `.agents/plans/software-factory-plan.md` still the canonical plan, byte for byte?

    The copy exists for agents and tools that can read `.agents/` but not the repository
    root. The *previous* copy at that path was a condensed rewrite that said almost the same
    thing in slightly different words, and it misled two sessions before `9e33944` deleted
    it. A verbatim copy can only go stale, and this is what notices — the same argument as
    the vendored-layer-A rows above, applied to one file.

    Reported rather than failed when the script is absent: a checkout without
    `scripts/sync_plan_copy.py` has no copy to keep current, and `doctor` runs against
    fixture homes in the test suite.
    """
    script = home / "scripts" / "sync_plan_copy.py"
    if not script.exists():
        return "plan copy", True, "no scripts/sync_plan_copy.py in this tree"
    try:
        done = subprocess.run(
            [sys.executable, str(script), "--check"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "plan copy", False, str(exc)
    output = (done.stdout or done.stderr).strip().splitlines()
    return "plan copy", done.returncode == 0, output[-1] if output else ""


def _tool_check(argv: Sequence[str]) -> tuple[str, bool, str]:
    name = argv[0]
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return name, False, str(exc)
    output = (proc.stdout or proc.stderr).strip().splitlines()
    return name, proc.returncode == 0, output[0] if output else ""


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
