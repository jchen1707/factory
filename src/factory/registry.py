"""`config/projects.toml` — §10. The single place a stack fact lives in layer D."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factory.machine import Blocked

__all__ = [
    "Defaults",
    "GarbageCollection",
    "Project",
    "Registry",
    "RegistryError",
    "SandboxDelivery",
    "VaultConfig",
    "load_registry",
]


class RegistryError(Exception):
    """A configuration error the daemon must refuse to start on."""


#: The forge names `delivery/forge.py` has an adapter for. Spelled here rather than
#: imported from it because `forge` imports `Project` from this module, and a validation
#: constant is not worth a cycle. `tests/unit/test_registry.py` asserts the two agree, so
#: the duplication cannot drift.
_FORGES = ("github", "gitlab")


@dataclass(frozen=True)
class SandboxDelivery:
    """A project that pushes and opens its merge request **from inside the VM** (§13.2,
    deliberately reversed for this project and no other).

    Declaring this sub-table is the whole opt-in. Everything downstream reads it rather
    than a flag in `src/`: `steps/deliver.py` picks `delivery/sandbox_gitlab.py` over the
    host adapter, `policy.capability_secrets` admits `placeholder_env` inside the VM, and the
    preflight asserts the two things that must be true before any model spend. Deleting
    the sub-table restores the full-strength boundary with **no code revert** — that
    property is the reason the opt-in lives here and not in a constant, and it is worth
    more than the four lines it costs.

    `placeholder_env` is the *env name* of an `sbx secret set-custom` entry, and what the VM
    receives under it is a `sbx-cs-…` **placeholder**, never the `glpat-…`. The proxy
    substitutes the real token into the outbound request headers on the way to
    `api_url`, so the agent gains a capability bounded to one host and one sandbox
    scope, not a credential. `delivery/sandbox_gitlab.py` argues this at length; the
    argument is the only reason this sub-table is defensible at all.
    """

    #: The instance root, e.g. `https://172.18.194.183`. `/api/v4` is appended.
    api_url: str
    #: `group/subgroup/project` as the API addresses it. From the registry rather than
    #: parsed out of `remote`, so a repository that has moved fails loudly.
    project_path: str
    #: The `sbx secret set-custom` env name whose placeholder authenticates the VM.
    #:
    #: No CA path sits beside it, and that absence is load-bearing: measured 2026-09-01,
    #: the VM is offered `sbx`'s proxy certificate rather than the instance's, and the
    #: image already trusts that CA. A `ca_file` key here would pin the wrong certificate
    #: and read like diligence while doing it.
    placeholder_env: str


@dataclass(frozen=True)
class VaultConfig:
    path: Path
    write_allowlist: tuple[str, ...]
    snapshot_exclude: tuple[str, ...]


@dataclass(frozen=True)
class Planning:
    auto: bool
    acceptance_criteria_over: int
    description_chars_over: int


@dataclass(frozen=True)
class RedPhase:
    inconclusive: str
    inconclusive_alarm_pct: int


@dataclass(frozen=True)
class GarbageCollection:
    """§16.5's four ages. Every one is a *floor* on how long the factory keeps
    something, never a promise to delete it: a pushed branch, an open PR and a sandbox
    the factory did not create are all untouchable at any age."""

    worktree_days: int = 7
    sandbox_idle_hours: int = 12
    #: Deliberately far above `sandbox_idle_hours`. Stopping a sandbox costs a restart;
    #: removing one costs rebuilding the image cache, which is expensive enough that the
    #: note this plan came from calls it out by name.
    sandbox_rm_days: int = 14
    artifact_days: int = 60


@dataclass(frozen=True)
class Defaults:
    worktree_subdir: str
    max_attempts: int
    max_total_attempts: int
    disk_min_free_gb: int
    concurrency_per_project: int
    planning: Planning
    redphase: RedPhase
    gc: GarbageCollection
    timeouts_seconds: Mapping[str, int]
    #: §8.7 — per-sandbox egress denials applied to **every** factory sandbox at
    #: creation. `sbx` fixes these at `create`, so this is a creation-time decision
    #: like the template and the static MCP set.
    deny_network: tuple[str, ...] = ()
    #: Phase 2 — when true, the implement step spawns a host-side tailer that writes an
    #: `events.timings.jsonl` sidecar (one `observed_at` per `events.jsonl` line), which the
    #: console's run-timeline reads to defend a per-call `duration_s`. Off by default: the
    #: writer is a boundary change (the console is otherwise read-only), so it is James's
    #: call to arm per project in `[defaults]` of `projects.toml`.
    timings: bool = False


@dataclass(frozen=True)
class Project:
    name: str
    team: str
    path: Path
    remote: str
    base_branch: str
    stack: str
    template: str
    kits: tuple[str, ...]
    static_mcp: tuple[str, ...]
    build_sandbox: str
    review_sandbox: str
    vault_mount: str
    network_allow: tuple[str, ...]
    #: Which forge this project delivers to — `github` or `gitlab` (`delivery/forge.py`).
    #: Named rather than sniffed from `remote`: a typo'd or moved remote would otherwise
    #: pick an adapter, authenticate with the wrong CLI's keyring credential, and push to
    #: whatever that resolved to. Validated at load, like everything else here.
    forge: str = "github"
    env: Mapping[str, str] = field(default_factory=dict)
    requires_clone: bool = False
    #: Credential names that `sbx` puts in the sandbox environment, that this project
    #: has looked at and decided to run with anyway. Every name here is recorded as a
    #: `warn` on every run rather than passing silently; a name *not* here blocks. The
    #: list is per project because the judgement is per project — see
    #: `policy.capability_env_names`.
    acknowledged_env_credentials: tuple[str, ...] = ()
    #: §15.2's sensitive-path Tier-2 trigger: a diff touching one of these globs runs the
    #: full nine-axis fan-out however small it is. It lives here rather than in
    #: `src/factory/` because it is a stack fact, and §3.2 gives layer D none of those —
    #: a table of one repository's directory names inside the control plane is the same
    #: violation a gate command there would be. The registry is where this file's own
    #: header says a stack fact belongs.
    #:
    #: Empty is a real answer and the default: a project that has not decided which of its
    #: directories carry extra risk gets the size and protected-path triggers alone. What
    #: is *not* an answer is a glob that matches nothing, which is what the moved-from
    #: table held for `frontend` — an inert trigger reads exactly like a trigger that
    #: never fired, and nothing tells the two apart.
    sensitive_paths: tuple[str, ...] = ()
    #: §11.3's writer limit, for this project alone. `None` — the default and the answer
    #: for every project that has not thought about it — means `[defaults]` decides, so
    #: raising the floor for everything is still one edit in one place.
    #:
    #: It is per project because the constraint is per project: the limit exists because
    #: concurrent writers share one `.git`, and how many a repository can carry is a fact
    #: about *that* repository — its worktree layout, its test suite's tolerance for
    #: parallel runs, its sandbox's memory. A global number is either too low for the
    #: repository that could take four or too high for the one that cannot take two.
    #: Resolve it with `Registry.concurrency_for`, never by reading this attribute.
    concurrency_per_project: int | None = None
    #: Set only by a `[projects.<name>.sandbox_delivery]` sub-table. `None` — the default
    #: and the answer for every other project — means delivery is host-side, which is
    #: what §13.2 says and what `delivery/github.py` and `delivery/gitlab.py` do.
    sandbox_delivery: SandboxDelivery | None = None

    @property
    def base_ref(self) -> str:
        """Always the remote ref. Never the local branch, and never `main` for these
        two repos, where `main` is generated by CI (§2.2)."""
        return f"origin/{self.base_branch}"

    def worktree_path(self, subdir: str, identifier: str) -> Path:
        return self.path / subdir / identifier


@dataclass(frozen=True)
class Registry:
    vault: VaultConfig
    defaults: Defaults
    projects: Mapping[str, Project]

    def concurrency_for(self, project: Project) -> int:
        """§11.3's writer limit for one project: its own override, else `[defaults]`.

        The single place the fallback is spelled. Both enforcement sites — the poller's
        admission check and `steps/claim.py`'s refusal — go through here, because a limit
        that two call sites resolve differently is a limit the poller admits work against
        and the claim then blocks, once per tick, forever.
        """
        return (
            project.concurrency_per_project
            if project.concurrency_per_project is not None
            else self.defaults.concurrency_per_project
        )

    def resolve(self, identifier: str) -> Project:
        """§10.2 steps 1 to 3. Zero matches is "not eligible"; two is a config error.

        The distinction matters: an unknown team key is a ticket the factory was never
        meant to see, and a duplicated one is a registry that would silently send two
        teams' work to one repository.
        """
        team = identifier.split("-", 1)[0]
        matches = [p for p in self.projects.values() if p.team == team]
        if len(matches) > 1:
            raise RegistryError(
                f"team {team!r} is claimed by {len(matches)} projects: "
                f"{sorted(p.name for p in matches)}"
            )
        if not matches:
            raise Blocked("unknown-team", f"no project in the registry claims team {team!r}")
        return matches[0]


def _defaults(raw: Mapping[str, Any]) -> Defaults:
    planning_raw = dict(raw.get("planning", {}))
    redphase_raw = dict(raw.get("redphase", {}))
    gc_raw = dict(raw.get("gc", {}))
    inconclusive = str(redphase_raw.get("inconclusive", "report"))
    if inconclusive not in {"report", "escalate", "block"}:
        raise RegistryError(
            f"redphase.inconclusive must be report|escalate|block, got {inconclusive!r}"
        )
    return Defaults(
        worktree_subdir=str(raw.get("worktree_subdir", ".factory/worktrees")),
        max_attempts=int(raw.get("max_attempts", 3)),
        max_total_attempts=int(raw.get("max_total_attempts", 5)),
        disk_min_free_gb=int(raw.get("disk_min_free_gb", 20)),
        concurrency_per_project=int(raw.get("concurrency_per_project", 1)),
        planning=Planning(
            auto=bool(planning_raw.get("auto", False)),
            acceptance_criteria_over=int(planning_raw.get("acceptance_criteria_over", 12)),
            description_chars_over=int(planning_raw.get("description_chars_over", 8000)),
        ),
        redphase=RedPhase(
            inconclusive=inconclusive,
            inconclusive_alarm_pct=int(redphase_raw.get("inconclusive_alarm_pct", 30)),
        ),
        gc=GarbageCollection(
            worktree_days=int(gc_raw.get("worktree_days", 7)),
            sandbox_idle_hours=int(gc_raw.get("sandbox_idle_hours", 12)),
            sandbox_rm_days=int(gc_raw.get("sandbox_rm_days", 14)),
            artifact_days=int(gc_raw.get("artifact_days", 60)),
        ),
        timeouts_seconds={str(k): int(v) for k, v in dict(raw.get("timeouts_seconds", {})).items()},
        deny_network=tuple(str(h) for h in raw.get("deny_network", ())),
        timings=bool(raw.get("timings", False)),
    )


def _sandbox_delivery(name: str, raw: Mapping[str, Any], *, forge: str) -> SandboxDelivery | None:
    """Parse `[projects.<name>.sandbox_delivery]`, or `None` when it is absent.

    Absent is the answer for every project but one, and it is the strong answer: §13.2
    holds and delivery is host-side. Everything here is validated rather than defaulted,
    because a half-specified opt-in would be discovered at the *end* of a run, after the
    model spend, by a push that had nowhere to go.

    Two of the three refusals are about the boundary rather than about typos:

    - **`forge` must be `gitlab`.** `sandbox_gitlab.py` is the only in-VM adapter there is.
      A `github` project with this sub-table would silently deliver host-side anyway, which
      is a registry that says one thing and does another.
    - **`requires_clone` must be false.** A clone sandbox's repository lives at the
      project's path *inside* the VM, and the branch the run built is in the clone, not in
      the host worktree this adapter would push from. That is a real design question, not a
      line of code, so it fails here rather than pushing an empty branch.
    """
    body = raw.get("sandbox_delivery")
    if body is None:
        return None
    if not isinstance(body, Mapping):
        raise RegistryError(f"project {name!r}: [sandbox_delivery] must be a table")
    if forge != "gitlab":
        raise RegistryError(
            f"project {name!r} declares [sandbox_delivery] with forge {forge!r}. In-VM "
            "delivery exists only for gitlab (`delivery/sandbox_gitlab.py`); a github "
            "project carrying this table would deliver host-side and say otherwise."
        )
    if bool(raw.get("requires_clone", False)):
        raise RegistryError(
            f"project {name!r} declares [sandbox_delivery] and requires_clone. The branch a "
            "clone run builds lives in the VM's private clone, not in the worktree this "
            "adapter pushes from, so the combination would push nothing and report success."
        )
    missing = [key for key in ("api_url", "project_path", "placeholder_env") if key not in body]
    if missing:
        raise RegistryError(f"project {name!r}: [sandbox_delivery] is missing {missing}")
    return SandboxDelivery(
        api_url=str(body["api_url"]).rstrip("/"),
        project_path=str(body["project_path"]).strip("/"),
        placeholder_env=str(body["placeholder_env"]),
    )


def _project(name: str, raw: Mapping[str, Any]) -> Project:
    required = ("team", "path", "remote", "base_branch", "stack", "build_sandbox")
    missing = [key for key in required if key not in raw]
    if missing:
        raise RegistryError(f"project {name!r} is missing {missing}")
    if raw["base_branch"] == "main" and name in {"python-harness", "frontend-harness"}:
        raise RegistryError(
            f"project {name!r} names `main` as its base branch, but `main` there is "
            "generated by generate-main.yml and is never a merge target (§2.2)"
        )
    forge = str(raw.get("forge", "github"))
    if forge not in _FORGES:
        raise RegistryError(
            f"project {name!r} names forge {forge!r}; the adapters are {sorted(_FORGES)}. "
            "A run must not discover this after it has spent model budget."
        )
    sandbox_delivery = _sandbox_delivery(name, raw, forge=forge)
    concurrency = raw.get("concurrency_per_project")
    if concurrency is not None:
        concurrency = int(concurrency)
        if concurrency < 1:
            raise RegistryError(
                f"project {name!r} sets concurrency_per_project={concurrency}; the limit is a "
                "count of writers and zero would make the project unclaimable rather than "
                "paused. Remove the project to stop claiming for it."
            )
    return Project(
        name=name,
        team=str(raw["team"]),
        path=Path(str(raw["path"])),
        remote=str(raw["remote"]),
        forge=forge,
        base_branch=str(raw["base_branch"]),
        stack=str(raw["stack"]),
        template=str(raw.get("template", "")),
        kits=tuple(str(k) for k in raw.get("kits", ())),
        static_mcp=tuple(str(m) for m in raw.get("static_mcp", ())),
        build_sandbox=str(raw["build_sandbox"]),
        review_sandbox=str(raw.get("review_sandbox", "")),
        vault_mount=str(raw.get("vault_mount", "rw")),
        network_allow=tuple(str(h) for h in raw.get("network_allow", ())),
        env={str(k): str(v) for k, v in dict(raw.get("env", {})).items()},
        requires_clone=bool(raw.get("requires_clone", False)),
        acknowledged_env_credentials=tuple(
            str(n) for n in raw.get("acknowledged_env_credentials", ())
        ),
        sensitive_paths=tuple(str(g) for g in raw.get("sensitive_paths", ())),
        concurrency_per_project=concurrency,
        sandbox_delivery=sandbox_delivery,
    )


def load_registry(path: Path) -> Registry:
    """Parse and validate `projects.toml`. Raises rather than defaulting.

    Validation happens at load because §4.5's reasoning applies to the registry too: a
    control plane running on a half-understood configuration looks identical to one
    running correctly, right up until it writes to the wrong repository.
    """
    raw = tomllib.loads(path.read_text(encoding="utf-8"))

    vault_raw = raw.get("vault", {})
    if "path" not in vault_raw:
        raise RegistryError("projects.toml has no [vault] path")
    vault = VaultConfig(
        path=Path(str(vault_raw["path"])),
        write_allowlist=tuple(
            str(g)
            for g in vault_raw.get("write_allowlist", ("Project Learnings/**", "_VAULT_INDEX.md"))
        ),
        snapshot_exclude=tuple(
            str(d) for d in vault_raw.get("snapshot_exclude", (".obsidian", ".git", ".trash"))
        ),
    )

    projects = {name: _project(name, body) for name, body in raw.get("projects", {}).items()}
    if not projects:
        raise RegistryError("projects.toml declares no projects")

    seen: dict[str, str] = {}
    for project in projects.values():
        if project.team in seen:
            raise RegistryError(
                f"team {project.team!r} is claimed by both {seen[project.team]!r} "
                f"and {project.name!r}"
            )
        seen[project.team] = project.name

    return Registry(vault=vault, defaults=_defaults(raw.get("defaults", {})), projects=projects)
