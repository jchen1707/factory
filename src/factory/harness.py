"""The bridge to layer A and layer B — §12.

The rule this module exists to keep: **the factory calls the harness; it never
re-implements it.** Nothing here names a gate command. It reads `harness.config.json`
and hands back what the file declares, so a gate added in layer B is a gate the factory
runs without a change here.

`uv` and `pnpm` appear once, in `cross_check_stack`, as expectations to *test* the
config against. That is §10.2 step 6, and it is the guard that stops a stack fact
leaking into layer D as a command.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from factory.machine import Blocked

__all__ = [
    "Gate",
    "HarnessConfig",
    "ProtectedPath",
    "cross_check_stack",
    "load_harness_config",
    "vendor_check",
]

#: What each stack's gates must invoke, per §10.2 step 6. Not a command the factory
#: builds — a value it compares against.
EXPECTED_RUNNER = {"python": "uv", "frontend": "pnpm"}


@dataclass(frozen=True)
class Gate:
    name: str
    kind: str
    run: tuple[str, ...]
    when: str | None = None
    caveat: str | None = None


@dataclass(frozen=True)
class ProtectedPath:
    glob: str
    why: str
    scope: str = "write"


@dataclass(frozen=True)
class HarnessConfig:
    root: Path
    name: str
    team: str | None
    gates: tuple[Gate, ...]
    protected: tuple[ProtectedPath, ...]
    gated_paths: tuple[str, ...]
    gated_files: tuple[str, ...]
    secret_vars: tuple[str, ...]
    apps: tuple[str, ...]
    tests: tuple[str, ...]
    review_agent_dir: str
    review_checklist_dir: str

    @property
    def is_monorepo(self) -> bool:
        return bool(self.apps)

    def gate_of_kind(self, kind: str) -> Gate | None:
        """The first gate of a kind, or None. Used by the red-phase replay to find the
        repository's own `kind: test` gate rather than composing one."""
        return next((g for g in self.gates if g.kind == kind), None)

    def first_protected_glob(self) -> ProtectedPath | None:
        """A protected path with no wildcard, for the preflight canary (§9.3).

        A literal path is wanted because the canary feeds one to `protect_paths.mjs`
        and needs the hook to recognise it without the caller inventing a filename that
        happens to match a glob.
        """
        literal = [p for p in self.protected if "*" not in p.glob and p.scope == "write"]
        return literal[0] if literal else (self.protected[0] if self.protected else None)


def load_harness_config(root: Path) -> HarnessConfig:
    """Read `<root>/harness.config.json`.

    A config with neither `gates` nor `apps` is refused: it declares no Definition of
    Done and names no app that would, so anything run against it would be verified by
    nothing while looking verified.
    """
    path = root / "harness.config.json"
    if not path.exists():
        raise Blocked("no-harness-config", str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))

    gates = tuple(
        Gate(
            name=g["name"],
            kind=g["kind"],
            run=tuple(g["run"]),
            when=g.get("when"),
            caveat=g.get("caveat"),
        )
        for g in raw.get("gates", [])
    )
    apps = tuple(raw.get("apps", ()))
    if not gates and not apps:
        raise Blocked(
            "harness-config-declares-nothing",
            f"{path} has neither `gates` nor `apps`; nothing would be verified",
        )

    hooks = raw.get("hooks", {})
    review = raw.get("review", {})
    return HarnessConfig(
        root=root,
        name=raw.get("name", root.name),
        team=raw.get("tracker", {}).get("team"),
        gates=gates,
        protected=tuple(
            ProtectedPath(glob=p["glob"], why=p["why"], scope=p.get("scope", "write"))
            for p in hooks.get("protected", [])
        ),
        gated_paths=tuple(hooks.get("gatedPaths", ())),
        gated_files=tuple(hooks.get("gatedFiles", ())),
        secret_vars=tuple(hooks.get("secretVars", ())),
        apps=apps,
        # Layer A gains this key in Phase 2 (§15.3). Absent means the red-phase replay
        # reports `unavailable`, never `pass` — which is why the default is empty
        # rather than a guess at where the tests are.
        tests=tuple(raw.get("tests", ())),
        review_agent_dir=review.get("agentDir", ".agents/agents"),
        review_checklist_dir=review.get("checklistDir", "docs/agents/subagents"),
    )


def cross_check_stack(config: HarnessConfig, stack: str) -> None:
    """§10.2 step 6 — the guard that stops the factory hard-coding a gate name.

    A python project whose gates do not run `uv`, or a frontend one whose gates do not
    run `pnpm`, is either mis-registered or has changed its toolchain. Both are things
    a human should look at before an unattended run starts writing.
    """
    expected = EXPECTED_RUNNER.get(stack)
    if expected is None or config.is_monorepo:
        # A monorepo root declares `apps` and no gates of its own; the dispatch in
        # layer A resolves them per app, so there is nothing here to cross-check.
        return
    wrong = [g.name for g in config.gates if g.run and g.run[0] != expected]
    if wrong:
        raise Blocked(
            "stack-mismatch",
            f"project stack is {stack!r} so every gate should invoke {expected!r}; "
            f"these do not: {wrong}",
        )


def vendor_check(target: Path, vendor_sync: Path) -> tuple[bool, str]:
    """Run layer A's own freshness check against a checkout. F9.

    Integrity only: `--harness` is deliberately omitted, because a worktree is checked
    against the `MANIFEST.json` it carries. Whether that pin is current with `harness`
    is a different question and a different failure, and conflating them would make a
    hand-edited vendored file indistinguishable from an out-of-date one.
    """
    if not vendor_sync.exists():
        return False, f"vendor_sync.py not found at {vendor_sync}"
    proc = subprocess.run(
        ["python3", str(vendor_sync), "check", "--target", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def gates_summary(gates: Sequence[Gate]) -> str:
    """One line per gate, for a dry run and for `factory doctor`."""
    return "\n".join(
        f"  {g.kind:<12} {g.name:<24} {' '.join(g.run)}"
        + (f"\n{'':<40}when: {g.when}" if g.when else "")
        for g in gates
    )
