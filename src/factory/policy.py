"""The rules that stop the factory — §5.3, §6, §8.5, §17.4.

Four unrelated-looking guards live together because they answer one question: what is
the factory not allowed to do on its own? Human-reserved transitions, the host-execution
deny list, the sandbox namespace, and the vault write allowlist are all the same kind of
rule, and putting them in one file means there is one place to read before relaxing any
of them.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from factory.machine import State, requires_human_rule

__all__ = [
    "HOST_EXECUTION_DENY",
    "VaultChange",
    "assert_factory_sandbox",
    "assert_no_skip_verify",
    "diff_vault",
    "host_execution_verdict",
    "requires_human",
    "sandbox_is_factory_owned",
    "snapshot_vault",
    "vault_writes_outside_allowlist",
]


def requires_human(source: State, target: State) -> str | None:
    """The single place any human-reserved transition can be relaxed (§5.3).

    Returns the rule name that reserves the hop, or None when the factory may take it
    itself. Callers write the verdict to the audit log either way.
    """
    return requires_human_rule(source, target)


# --------------------------------------------------------------------------------
# §17.4 — the host-execution guard
# --------------------------------------------------------------------------------

#: A diff touching any of these can make the *host* execute agent-authored code the
#: next time James commits, builds or opens the repo. The list is deliberately short
#: and deliberately blunt: every entry is a file whose whole purpose is to run
#: something.
HOST_EXECUTION_DENY: tuple[str, ...] = (
    ".husky/**",
    ".github/**",
    ".pre-commit-config.yaml",
    "Makefile",
    "*.mk",
    "**/*.mk",
    "package.json",
    "**/package.json",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".codex/**",
    ".agents/vendor/**",
    "harness.config.json",
    "**/harness.config.json",
    ".gitattributes",
)

#: A hit here is not "needs a human", it is "enforcement failed". `protect_paths.mjs`
#: should already have refused the write, so the write landing means the hook did not
#: fire — which is precisely the invisible failure P0-6 measured.
HOST_EXECUTION_BLOCK: tuple[str, ...] = (".agents/vendor/**",)


def _matches(path: str, patterns: Iterable[str]) -> bool:
    # `removeprefix`, not `lstrip`: lstrip takes a character set, so it would turn
    # ".husky/pre-commit" into "husky/pre-commit" and quietly unprotect every dotfile
    # on the deny list. Layer A's own `relativePath` carries the same warning.
    normalised = path.replace("\\", "/")
    while normalised.startswith("./"):
        normalised = normalised.removeprefix("./")
    for pattern in patterns:
        if fnmatch.fnmatchcase(normalised, pattern):
            return True
        # `dir/**` should also match `dir/file`, which fnmatch's `**` does not do on
        # its own because it has no directory-crossing semantics.
        if pattern.endswith("/**") and normalised.startswith(pattern[:-2]):
            return True
    return False


def host_execution_verdict(changed_paths: Sequence[str]) -> tuple[str, list[str]]:
    """`("clear"|"awaiting_human"|"blocked", the paths that decided it)`.

    Called before **any** host-side command runs against the worktree. `blocked` wins
    over `awaiting_human` when both fire, because a vendored-tree edit means the guard
    layer itself is broken and a human reading the diff is not the right next step.
    """
    blocking = [p for p in changed_paths if _matches(p, HOST_EXECUTION_BLOCK)]
    if blocking:
        return "blocked", sorted(blocking)
    escalating = [p for p in changed_paths if _matches(p, HOST_EXECUTION_DENY)]
    if escalating:
        return "awaiting_human", sorted(escalating)
    return "clear", []


# --------------------------------------------------------------------------------
# §8.5 — the sandbox namespace
# --------------------------------------------------------------------------------

_FACTORY_SANDBOX_PREFIXES = ("factory-build-", "factory-review-")


def sandbox_is_factory_owned(name: str) -> bool:
    """True only for a name the factory created. `codex-*` is James's `csbx` session."""
    return name.startswith(_FACTORY_SANDBOX_PREFIXES)


def assert_factory_sandbox(name: str) -> None:
    """Refuse to touch a sandbox the factory does not own.

    Asserted at the adapter rather than at the caller: an unattended writer inside a
    live human session is the failure this prevents, and a guard the caller can forget
    to call is not a guard.
    """
    if not sandbox_is_factory_owned(name):
        raise PermissionError(
            f"refusing to operate on sandbox {name!r}: the factory owns only "
            f"{' and '.join(p + '*' for p in _FACTORY_SANDBOX_PREFIXES)}. "
            "A codex-* sandbox is James's interactive csbx session."
        )


def assert_no_skip_verify(env: Mapping[str, str]) -> None:
    """`HARNESS_SKIP_VERIFY=1` disables layer A's Stop gate. F8.

    Checked positively on the environment the run will actually get, not on the
    ambient one, because the leak the plan describes is a value passed in.
    """
    if env.get("HARNESS_SKIP_VERIFY"):
        raise PermissionError(
            "HARNESS_SKIP_VERIFY is set in the run environment; the Stop gate would "
            "not fire and a green run would prove nothing (enforcement-disabled)"
        )


# --------------------------------------------------------------------------------
# §8.5 — the vault write allowlist
# --------------------------------------------------------------------------------


@dataclass(frozen=True)
class VaultChange:
    path: str
    kind: str  # "added" | "modified" | "deleted"


def snapshot_vault(
    root: Path, *, exclude: Sequence[str] = (".obsidian", ".git", ".trash")
) -> dict[str, tuple[int, int, str]]:
    """`path -> (mtime, size, sha256)` for every file under `root`.

    Measured at 22 ms over 162 files / 15.7 MB (p0-13), so this runs twice per attempt
    with no caching and no incremental scheme. `.obsidian` is excluded for correctness
    rather than cost: it records UI state that changes when a pane is opened, and
    attributing that to the run would be a false positive every time.
    """
    out: dict[str, tuple[int, int, str]] = {}
    if not root.is_dir():
        return out
    excluded = set(exclude)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in excluded]
        for name in filenames:
            full = Path(dirpath) / name
            try:
                stat = full.stat()
                digest = hashlib.sha256(full.read_bytes()).hexdigest()
            except OSError:
                continue
            out[str(full.relative_to(root))] = (int(stat.st_mtime), stat.st_size, digest)
    return out


def diff_vault(
    before: Mapping[str, tuple[int, int, str]], after: Mapping[str, tuple[int, int, str]]
) -> list[VaultChange]:
    """What changed between two snapshots, by content rather than by mtime."""
    changes: list[VaultChange] = []
    for path, meta in after.items():
        if path not in before:
            changes.append(VaultChange(path, "added"))
        elif before[path][2] != meta[2]:
            changes.append(VaultChange(path, "modified"))
    changes.extend(VaultChange(path, "deleted") for path in before if path not in after)
    return sorted(changes, key=lambda c: (c.kind, c.path))


def vault_writes_outside_allowlist(
    changes: Sequence[VaultChange], allowlist: Sequence[str]
) -> list[VaultChange]:
    """Every change the run was not entitled to make.

    A deletion or truncation *inside* the allowed set counts too: the distiller only
    ever adds or rewrites its own dated note, so a delete there is not the hook's work
    either.
    """
    offending = [c for c in changes if not _matches(c.path, allowlist)]
    offending += [c for c in changes if _matches(c.path, allowlist) and c.kind == "deleted"]
    return sorted(set(offending), key=lambda c: (c.kind, c.path))
