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
from typing import Any

from factory.machine import State, requires_human_rule

__all__ = [
    "GATEWAY_CREDENTIAL",
    "HOST_EXECUTION_DENY",
    "VaultChange",
    "assert_factory_sandbox",
    "assert_no_skip_verify",
    "capability_env_names",
    "capability_secrets",
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
    # The GitLab half of `.github/**`, added with the `gitlab` forge adapter. Without
    # these, a `forge = "gitlab"` project got a *weaker* guard than a GitHub one for no
    # stated reason: `.gitlab-ci.yml` is the pipeline definition, `.gitlab/` holds the
    # included templates and the CODEOWNERS/issue-template machinery, and a runner that
    # picks either up executes agent-authored YAML on infrastructure the project owns.
    # `**/.gitlab-ci.yml` because a monorepo's per-package pipelines are not at the root.
    ".gitlab-ci.yml",
    "**/.gitlab-ci.yml",
    ".gitlab/**",
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
# §8.7 — what counts as a credential inside the VM
# --------------------------------------------------------------------------------

#: The MCP gateway's own credential. `sbx` uploads it into every sandbox whenever any
#: MCP server is registered on the host, and offers no per-sandbox opt-out — so an
#: assertion that the `secrets` array is *empty* can never pass while `sbx mcp ls`
#: lists anything, and removing the registration would break the interactive
#: `--static-mcp linear` flow that §13.1 preserves on purpose.
#:
#: It is not a service credential. It authenticates the sandbox to the gateway, and the
#: gateway serves only the **static MCP set**, which `build_spec` fixes at `()` at
#: creation and which `sbx` cannot widen afterwards. §13.1 grounds the safety in
#: exactly that — *"with no Linear MCP in the static set, there is nothing to leak and
#: nothing to misuse"* — not in this token's absence.
#:
#: Measured 2026-08-21: `factory-build-python-harness`, created seconds earlier by the
#: factory itself, carried `github` and `mcpgateway`. P0-5 had guessed that a
#: factory-created sandbox inherits neither; it inherits both. `github` was the real
#: violation and was re-scoped out of global. This one cannot be, so it is named here
#: and `Defaults.deny_network` is the compensating control.
#:
#: Named for the thing rather than spelled `..._SECRET`: this is the secret's
#: *name*, and ruff's S105 reads any constant whose name contains "secret" as a
#: hardcoded credential value.
GATEWAY_CREDENTIAL = "mcpgateway"


def capability_secrets(secrets: Iterable[Any], *, declared: Iterable[str] = ()) -> list[str]:
    """The injected secrets that hand the VM a capability it must not have (§8.7).

    `sbx` secrets are **proxy-managed**: the token never lands on the sandbox
    filesystem, and the proxy authenticates the agent's egress on its way out. So the
    hazard is not a readable file — it is that the agent can act as James without ever
    holding a credential, which is what §13.2 ("GitHub writes: host only") forbids. Only
    `sbx inspect` sees a proxy-managed secret, which is why the preflight reads it from
    the sandbox.

    This is one of two channels, not the only one. The plan's claim that an in-VM env
    scan "finds nothing and looks green" is measured false — see `capability_env_names`,
    which reads the other.

    Everything is a capability except `GATEWAY_CREDENTIAL`, whose exclusion is argued in
    full at its definition, and the names in `declared`. Returned sorted so a failure
    message is stable.

    `declared` is the project's own `[sandbox_delivery] placeholder_env` and nothing else. It
    is threaded from the registry rather than added to a constant here, so that **deleting
    the sub-table restores the full-strength guard with no code revert** — the property
    James asked about when he approved the reversal, and the only version of the reversal
    that is reversible.

    Admitted only when the entry's `source` is `custom`. That is not decoration: a custom
    secret is a proxy *substitution rule*, so what the VM can hold under that name is a
    `sbx-cs-…` placeholder bounded to one host and one sandbox scope. A **service** secret
    of the same name would be a real credential wearing the declaration's clothes, and it
    still blocks. Measured shape, 2026-08-31: `{"name": ..., "source": "custom"}`.
    """
    admitted = set(declared)
    names = [
        str(entry.get("name"))
        for entry in secrets
        if isinstance(entry, Mapping)
        and entry.get("name")
        and not (str(entry.get("name")) in admitted and entry.get("source") == "custom")
    ]
    return sorted(name for name in names if name != GATEWAY_CREDENTIAL)


def capability_env_names(
    present: Iterable[str], acknowledged: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Split the credential names found *in the VM environment* into `(blocking, known)`.

    The second channel, and the one §8.7 argued did not exist. The plan says an env scan
    "finds nothing and looks green" because `sbx` secrets are proxy-managed — measured
    false on 2026-08-22: `factory-build-python-harness-2`, `factory-review-python-harness`
    and a freshly created clone sandbox each carried a 40-character `gho_` value in the
    environment, while `sbx inspect` reported only the gateway credential. One channel
    cannot see the other, so the preflight reads both.

    Which names count is not the factory's opinion: they are the target repository's own
    `harness.config.json` `hooks.secretVars`, the list layer B already keeps for its own
    hooks. A name the project has explicitly acknowledged comes back as `known` and is
    recorded as a warning on every run instead of passing in silence; anything else
    blocks. Acknowledgement is a judgement about a specific name in a specific project,
    which is why it lives in the registry rather than here.

    No `declared` parameter, unlike `capability_secrets`, and the asymmetry is deliberate.
    This function only ever sees names the *repository* declared in `secretVars`; a
    `[sandbox_delivery] placeholder_env` is not one of those, so admitting it here would be a
    branch nothing can reach. Measured besides: `set-custom --env` never puts its value in
    an `sbx exec` session's environment at all. If that ever changes, the name arrives
    here as an unrecognised one and blocks — which is the right way round.
    """
    known = set(acknowledged)
    names = sorted(set(present))
    return [n for n in names if n not in known], [n for n in names if n in known]


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
