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

import json
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from factory import machine
from factory.harness import load_harness_config, vendor_check
from factory.intake.linear import LinearError, keychain_secret
from factory.registry import Project, Registry, RegistryError, load_registry
from factory.routing import MODEL_CACHE, RoutingError, load_routing
from factory.sandbox.sbx import SbxAdapter, sbx_available
from factory.steps import review as review_step
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
    store: Store | None = None
    deep: bool = False


@dataclass(frozen=True)
class Check:
    name: str
    run: Callable[[DoctorContext], Sequence[Result]]
    #: What this check cannot run without. `"registry"` is the only dependency today,
    #: and it is the one that used to make checks disappear.
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

    return DoctorContext(home=home, registry=registry, store=store, deep=deep), results


# --------------------------------------------------------------------------------
# the checks themselves
# --------------------------------------------------------------------------------


def _model_cache(_ctx: DoctorContext) -> list[Result]:
    return _from_triple(_model_cache_check())


def _tools(_ctx: DoctorContext) -> list[Result]:
    results: list[Result] = []
    for argv in (["git", "--version"], ["gh", "auth", "status"], ["codex", "--version"]):
        results += _from_triple(_tool_check(argv))
    return results


def _sbx(_ctx: DoctorContext) -> list[Result]:
    ok, detail = sbx_available()
    return _one("sbx", ok, detail.splitlines()[0] if detail else "")


def _openai_secret(_ctx: DoctorContext) -> list[Result]:
    return _from_triple(_openai_secret_check())


def _global_gitignore(_ctx: DoctorContext) -> list[Result]:
    return _from_triple(_global_gitignore_check())


def _keychain(_ctx: DoctorContext) -> list[Result]:
    return _from_triple(_keychain_check())


def _skills(_ctx: DoctorContext) -> list[Result]:
    return _from_triple(_skills_check())


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


def _plan_copy(ctx: DoctorContext) -> list[Result]:
    return _from_triple(_plan_copy_check(ctx.home))


def _prices(ctx: DoctorContext) -> list[Result]:
    return _from_triple(_prices_check(ctx.home))


def _canary(ctx: DoctorContext) -> list[Result]:
    registry = ctx.registry
    if registry is None:  # unreachable: `needs` guards it. Typed, not asserted.
        return []
    return _from_triple(_deep_canary(registry))


#: Every check, in report order. The config four are not here — they run first, in
#: `load_context`, because they produce what the rest are handed.
CHECKS: tuple[Check, ...] = (
    Check("model cache", _model_cache),
    Check("external tools", _tools),
    Check("sbx", _sbx),
    Check("openai credential (sbx)", _openai_secret),
    Check("global gitignore", _global_gitignore),
    Check("linear credential", _keychain),
    Check("mattpocock execution set", _skills),
    Check("~/.factory absent", _factory_home_absent),
    Check("disk", _disk),
    Check("vendored layer A", _vendored_layer_a, needs=("registry",)),
    Check("sensitive paths", _sensitive_paths, needs=("registry",)),
    Check("sandbox delivery", _sandbox_delivery, needs=("registry",)),
    Check("plan copy", _plan_copy),
    Check("price table", _prices),
    Check("codex hook canary", _canary, needs=("registry",), deep=True),
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
    have = {"registry": ctx.registry is not None, "store": ctx.store is not None}

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


def _openai_secret_check() -> tuple[str, bool, str]:
    """The sbx-stored, *globally held* OpenAI OAuth token — not `codex login`.

    This is the single point that silently disables the entire factory (Phase 5
    handoff): a sandboxed agent authenticates through `sbx`'s proxy against a token
    stored at `(global) service openai`, and when it expires or is removed every
    sandboxed run of both stacks fails with `401 token_expired` while the host looks
    healthy and `codex login` fixes nothing. The fix is `sbx secret set openai --oauth`.

    This check verifies **presence**, not validity. `sbx secret ls` reports
    `(oauth configured)` whether the token is live or expired, so expiry still needs a
    live probe — but absence (a fresh machine, a reset, a removed secret) is the case
    that took a transcript dive to find, and it is free to catch here.
    """
    try:
        proc = subprocess.run(
            ["sbx", "secret", "ls"], capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "openai credential (sbx)", False, f"sbx unusable: {exc}"
    if proc.returncode != 0:
        return "openai credential (sbx)", False, (proc.stderr or proc.stdout).strip()[:120]
    # The row of interest is `(global)  service  openai  (oauth configured)`. Match on
    # the scope and the service name so a reordering of columns cannot fool it.
    for line in proc.stdout.splitlines():
        fields = line.split()
        if "(global)" in fields and "openai" in fields and "oauth" in line.lower():
            return (
                "openai credential (sbx)",
                True,
                "global openai oauth configured — expiry needs a live probe",
            )
    return (
        "openai credential (sbx)",
        False,
        "no global openai oauth — run `sbx secret set openai --oauth`",
    )


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
