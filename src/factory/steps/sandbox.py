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
from factory.policy import capability_env_names, capability_secrets
from factory.sandbox.base import SandboxSpec, Workspace
from factory.steps import Context, advance

__all__ = ["preflight", "run"]

#: The hook's own contract, read from `lib.mjs`: a PreToolUse payload on stdin, exit 2
#: to block, the reason on stderr. Exit 2 is the *only* code that blocks, which is why
#: the canary tests for 2 rather than for "non-zero".
_BLOCKING_EXIT = 2


def run(ctx: Context) -> None:
    spec = build_spec(ctx)

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
    workspaces = [Workspace(ctx.project.path)]
    if ctx.project.requires_clone:
        # `--clone` gives the VM a private in-container clone at the project's own path,
        # so nothing the agent writes under it reaches the host — including `.factory/`,
        # which §4.2 needs both sides to read. This additional `rw` workspace is the
        # repair: mounted at its identical path, its writes visible on the host at once.
        # It is per *project* and not per run, because §9.1 fixes the workspace set at
        # creation and this sandbox is named once per project (`_review_scratch` records
        # what the per-run version costs). See `steps/clone.py` for the whole shape.
        ctx.clone_mount.mkdir(parents=True, exist_ok=True)
        workspaces.append(Workspace(ctx.clone_mount))
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
        deny_network=ctx.registry.defaults.deny_network,
        # Creation-time env, so per-run values are deliberately *left out* rather than
        # resolved: §9.1 fixes this at `sbx create` and the sandbox is named once per
        # project, so there is no single run to resolve them against. Every step passes
        # the resolved set per exec (`Context.env`), which is what the agent actually
        # sees; baking a literal `{run}` in here would put an unexpanded token on a real
        # path inside the VM and it would fail somewhere far from this line.
        env={k: v for k, v in ctx.project.env.items() if "{run}" not in v},
        clone=ctx.project.requires_clone,
        allowed_custom_secrets=(
            (ctx.project.sandbox_delivery.placeholder_env,) if ctx.project.sandbox_delivery else ()
        ),
    )


def _capability_env(ctx: Context, spec: SandboxSpec) -> list[str]:
    """Which of the repository's `secretVars` are set, and non-empty, inside the VM.

    Names only. The values never leave the sandbox and never reach a log — the question
    is whether a credential is *there*, and printing one to answer that would be the
    leak the check exists to prevent. An empty variable is not a credential, so the test
    is `-n`, not existence.

    Nothing to check is a pass: a repository that declares no `secretVars` has told us
    which names matter, and the answer was none.
    """
    names = list(ctx.harness.secret_vars) if ctx.harness else []
    if not names:
        return []
    listed = " ".join(names)
    # `rf`: the backslash is shell syntax (`\$$v` is "expand $v, once"), not Python's.
    script = rf'for v in {listed}; do eval "value=\$$v"; [ -n "$value" ] && echo "$v"; done'
    completed = ctx.sandbox.exec_sync(spec.name, ["/bin/sh", "-lc", script], timeout=120)
    return [line.strip() for line in completed.stdout.splitlines() if line.strip() in names]


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

    # 3. No credential inside the VM that grants the agent a capability. §8.7. Read
    #    from the sandbox rather than assumed from how it was made — P0-5 assumed a
    #    factory-created sandbox inherits nothing and was wrong on both names it
    #    listed. `capability_secrets` argues the one exclusion; the deny rules below
    #    are what make that exclusion safe rather than merely convenient.
    info = ctx.sandbox.inspect(spec.name)
    secrets = info.get("secrets") or []
    offending = capability_secrets(secrets, declared=spec.allowed_custom_secrets)
    checks.append(("no-secrets-in-vm", not offending, json.dumps(secrets)))

    # 3d. The other half of that admission. A project that has opted into in-VM delivery
    #     has told the preflight to stop objecting to one name — so the preflight has to
    #     prove the thing that name is *for* is actually there. Without this, a missing or
    #     renamed custom secret is discovered by the push, at the end of a run that has
    #     already spent its whole model budget, and the failure reads like a network fault.
    if ctx.project.sandbox_delivery is not None:
        declared = ctx.project.sandbox_delivery.placeholder_env
        placeholder = ctx.sandbox.custom_secret_placeholder(spec.name, declared)
        checks.append(
            (
                "sandbox-delivery-provisioned",
                placeholder is not None,
                f"{declared} -> {placeholder or 'no custom secret scoped to this sandbox'}",
            )
        )

    # 3b. The other channel. `sbx inspect` reports proxy-managed secrets and cannot see
    #     an environment variable, so a credential injected into the VM's environment
    #     passes check 3 in silence — measured on 2026-08-22, when all three live
    #     sandboxes carried a 40-character `gho_` value while `inspect` showed only the
    #     gateway. Which names count comes from the repository's own `secretVars`.
    env_present = _capability_env(ctx, spec)
    blocking, known = capability_env_names(env_present, ctx.project.acknowledged_env_credentials)
    checks.append(("no-capability-env", not blocking, ", ".join(blocking or known) or "none set"))
    if known:
        # Acknowledged, not forgiven: recorded on every run so it stays visible in the
        # ledger rather than becoming a thing nobody looks at again.
        ctx.store.record_check(
            ctx.run.id,
            ctx.run.attempt,
            "preflight:acknowledged-env-credential",
            "warn",
            detail=", ".join(known),
        )

    # 3c. For a clone project: the clone is actually in effect. `ensure` attaches to an
    #     existing sandbox by name and `_assert_spec_matches` compares the workspace set —
    #     which is *identical* either way, because the clone sits at the project's own
    #     path. So a sandbox created before this project needed `--clone` would be
    #     attached to in silence, the agent would work on the bind-mounted host tree, and
    #     the first sign of it would be a `pnpm install` overwriting the host's
    #     node_modules. Asserted the way §9.3 asks: by producing the observable.
    if ctx.project.requires_clone:
        isolated, detail = _clone_isolation(ctx, spec)
        checks.append(("clone-isolates-the-workspace", isolated, detail))

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


def _clone_isolation(ctx: Context, spec: SandboxSpec) -> tuple[bool, str]:
    """Prove the VM is on a private clone and not on the host's directory.

    The host writes a marker into the project's own `.factory/` and asks the VM whether it
    can see it. A bind mount shares writes immediately, so *visible* means the clone is
    not in effect; a clone carries only the tracked content it was made from, so *absent*
    is the proof. The direction matters: writing from the host keeps the whole test on
    ground the factory already owns, and cleanup is a local `unlink` that cannot fail
    halfway inside a VM.
    """
    marker = ctx.project.path / ".factory" / f"clone-canary-{ctx.run.id}"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("If the sandbox can read this, it is not on a clone.\n", encoding="utf-8")
    try:
        seen = ctx.sandbox.exec_sync(
            spec.name,
            ["/bin/sh", "-c", f'[ -e "{marker}" ] && echo visible || echo absent'],
            timeout=120,
        )
    finally:
        marker.unlink(missing_ok=True)

    if seen.stdout.strip() == "absent":
        return True, f"{marker.name} written on the host is not visible in {spec.name}"
    return False, (
        f"{spec.name} can see {marker}, so it is bind-mounted on the host checkout rather "
        f"than running on a clone. {ctx.project.name} requires a clone: a `pnpm install` in "
        "there would overwrite the host's node_modules (p0-10). The sandbox almost "
        "certainly predates `requires_clone` — `sbx rm` it and let the factory recreate it, "
        "because the workspace set is fixed at creation."
    )
