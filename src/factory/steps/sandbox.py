"""`context_loaded -> sandbox_creating -> sandbox_ready` — create-or-attach, then prove
the enforcement layer is attached.

The preflight is the whole point of this step. R1 is the risk the plan rates highest:
everything runs, nothing enforces, and the run goes green having proved nothing. So the
preflight asserts **positively** — it makes `protect_paths.mjs` produce a refusal, and a
preflight that cannot produce one fails the state.
"""

from __future__ import annotations

import json
from pathlib import Path

from factory.harness import vendor_check
from factory.machine import Blocked, State
from factory.sandbox.base import SandboxSpec, Workspace
from factory.sandbox.sbx import create_argv
from factory.steps import Context, advance

__all__ = ["preflight", "run"]

#: The hook's own contract, read from `lib.mjs`: a PreToolUse payload on stdin, exit 2
#: to block, the reason on stderr. Exit 2 is the *only* code that blocks, which is why
#: the canary tests for 2 rather than for "non-zero".
_BLOCKING_EXIT = 2


def run(ctx: Context) -> None:
    spec = build_spec(ctx)

    if ctx.dry_run:
        ctx.would(f"sbx inspect {spec.name}  # create only if absent")
        ctx.would(" ".join(create_argv(spec)))
        advance(ctx, State.SANDBOX_CREATING)
        ctx.would("preflight: toolchain, HARNESS_SKIP_VERIFY, vendored tree, protect_paths canary")
        advance(ctx, State.SANDBOX_READY)
        return

    advance(ctx, State.SANDBOX_CREATING)
    ctx.sandbox.ensure(spec)
    ctx.log("sandbox.ready", sandbox=spec.name, template=spec.template or "(agent default)")

    preflight(ctx, spec)
    advance(ctx, State.SANDBOX_READY)


def build_spec(ctx: Context) -> SandboxSpec:
    """The build sandbox. Every creation-time decision is made here, once.

    The vault is mounted read-write and not `:ro`, because `session_learnings.mjs`
    *writes* to `<vault>/Project Learnings` and `vault_index.mjs` rewrites
    `_VAULT_INDEX.md`; a read-only mount would break both. The residual risk is bounded
    by observation instead — a before/after snapshot with an allowlist (§8.5).

    The static MCP set is empty. That is a decision that cannot be silently widened
    later, because `sbx` fixes it at creation.
    """
    if ctx.project.requires_clone:
        raise Blocked(
            "clone-not-implemented",
            f"{ctx.project.name} needs `sbx create --clone`: its node_modules cannot be "
            "shared with a macOS host, and a bind-mounted `pnpm install` would overwrite "
            "the host's tree (p0-10-gate-timing.md). Phase 2 builds that path.",
        )

    workspaces = [Workspace(ctx.project.path)]
    if ctx.project.vault_mount == "rw":
        workspaces.append(Workspace(ctx.registry.vault.path))
    elif ctx.project.vault_mount == "ro":
        workspaces.append(Workspace(ctx.registry.vault.path, readonly=True))

    return SandboxSpec(
        project=ctx.project.name,
        role="build",
        name=ctx.project.build_sandbox,
        workspaces=tuple(workspaces),
        template=ctx.project.template or None,
        kits=ctx.project.kits,
        static_mcp=ctx.project.static_mcp,
        env=dict(ctx.project.env),
    )


def preflight(ctx: Context, spec: SandboxSpec) -> None:
    """Five assertions, all of them positive. §8.7, §9.3, F8, F9, F20.

    Each one answers a way the system can look green while proving nothing.
    """
    checks: list[tuple[str, bool, str]] = []

    # 1. The toolchain the layer-A hooks need. Every hook is `.mjs`, and `verify.mjs`
    #    returns 0 when a gate could not start — so a missing node does not fail
    #    loudly, it makes the gates disappear.
    probe = ctx.sandbox.exec_sync(
        spec.name,
        ["/bin/sh", "-lc", "command -v node git && node --version"],
        workdir=str(ctx.project.path),
        timeout=120,
    )
    checks.append(("toolchain", probe.ok, probe.stdout.strip() or probe.stderr.strip()))

    # 2. `HARNESS_SKIP_VERIFY` unset in the environment the run will get. F8.
    env_probe = ctx.sandbox.exec_sync(
        spec.name,
        ["/bin/sh", "-lc", 'printf %s "${HARNESS_SKIP_VERIFY-}"'],
        workdir=str(ctx.project.path),
        timeout=60,
    )
    checks.append(
        ("harness-skip-verify-unset", env_probe.stdout.strip() == "", env_probe.stdout.strip())
    )

    # 3. No credential of any kind inside the VM. §8.7. Read from the sandbox rather
    #    than assumed from how it was made: P0-5 found the operator's own sandboxes
    #    carrying uploaded secrets.
    info = ctx.sandbox.inspect(spec.name)
    secrets = info.get("secrets") or []
    checks.append(("no-secrets-in-vm", not secrets, json.dumps(secrets)))

    # 4. The vendored layer-A tree is intact. F9. Run on the host, against the repo
    #    the sandbox has mounted, because `vendor_sync.py` is a host script and reading
    #    files is one of the three things the factory does on the host.
    ok, detail = vendor_check(ctx.project.path, _vendor_sync_path())
    checks.append(("vendored-tree-intact", ok, detail))

    # 5. The canary. §9.3's rule is that enforcement is proved by producing a refusal,
    #    not by observing the absence of a flag.
    canary_ok, canary_detail = _protect_paths_canary(ctx, spec)
    checks.append(("protect-paths-refuses", canary_ok, canary_detail))

    for name, passed, detail in checks:
        ctx.store.record_check(
            ctx.run.id,
            ctx.run.attempt,
            f"preflight:{name}",
            "pass" if passed else "fail",
            detail=detail[:2000],
        )

    failed = [(name, detail) for name, passed, detail in checks if not passed]
    if failed:
        raise Blocked(
            "enforcement-disabled",
            "preflight could not prove the run would be enforced: "
            + "; ".join(f"{name} ({detail[:200]})" for name, detail in failed),
        )
    ctx.log("preflight.green", checks=[name for name, _, _ in checks])


def _vendor_sync_path() -> Path:
    return Path.home() / "harness" / "scripts" / "vendor_sync.py"


def _protect_paths_canary(ctx: Context, spec: SandboxSpec) -> tuple[bool, str]:
    """Feed `protect_paths.mjs` a write to a protected path and require exit 2.

    What this proves and what it does not, stated plainly because the difference is
    the whole hazard: it proves node can spawn the hook, that the hook resolves this
    repository's `harness.config.json`, and that the rule is live. It does **not**
    prove Codex is wired to call it — nothing short of a real `codex exec` does, which
    is a model call, so `factory doctor --deep` offers that and this does not.

    Everything else about the wiring is asserted structurally instead: the worktree
    carries `.codex/hooks.json`, `--dangerously-bypass-hook-trust` is on every
    invocation (a unit test pins it), and the vendored tree just passed its integrity
    check.
    """
    if ctx.harness is None:
        return False, "no harness config loaded"
    protected = ctx.harness.first_protected_glob()
    if protected is None:
        return False, "the repo declares no protected paths, so nothing can be canaried"

    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": protected.glob}})
    hook = ".agents/vendor/harness/hooks/protect_paths.mjs"
    result = ctx.sandbox.exec_sync(
        spec.name,
        ["node", hook],
        workdir=str(ctx.project.path),
        timeout=120,
        stdin=payload,
    )
    if result.returncode == _BLOCKING_EXIT:
        return True, f"{protected.glob}: {result.stderr.strip()[:200]}"
    return False, (
        f"writing {protected.glob!r} exited {result.returncode}, expected {_BLOCKING_EXIT}. "
        f"stdout={result.stdout.strip()[:200]} stderr={result.stderr.strip()[:200]}"
    )
