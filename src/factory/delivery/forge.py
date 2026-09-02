"""Which forge a project delivers to — §13.2.

One function. It exists so `steps/deliver.py`, `cli.py` and `gc.py` name a *capability*
rather than a vendor, and so adding a third forge is a new module plus a line here rather
than an `if` in three call sites that will drift apart.

The adapters are whole modules, selected as modules. `Forge` is a Protocol they satisfy
structurally — neither imports the other, and neither imports this file, so there is no
cycle and no base class anybody has to remember to inherit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from factory.delivery import github, gitlab, sandbox_gitlab
from factory.registry import Project
from factory.sandbox.base import SandboxAdapter

__all__ = [
    "FORGES",
    "Forge",
    "SandboxSite",
    "UnknownForge",
    "for_delivery",
    "for_name",
    "for_project",
    "for_url",
]


class UnknownForge(Exception):
    """`projects.toml` named a forge with no adapter. Raised at delivery, not at load."""


class Forge(Protocol):
    """The six calls the run pipeline makes against a forge.

    Merge is not among them, and neither is approve. That is §24.8, and
    `tests/unit/test_boundaries.py` reads it off the source of every adapter rather than
    trusting this docstring — a new forge module gets the same grep for free, which is the
    point of keeping the surface this small.
    """

    def push(self, worktree: Path, branch: str) -> None: ...

    def find_pr(self, worktree: Path, branch: str) -> str | None: ...

    def create_pr(
        self, worktree: Path, *, base: str, head: str, title: str, body_file: Path
    ) -> str: ...

    def edit_pr(self, worktree: Path, number: int, *, body_file: Path) -> None: ...

    def pr_number(self, url: str) -> int | None: ...

    def pr_state(self, cwd: Path, url: str) -> str | None: ...


#: The registry's `forge` key is validated against these names at load (`registry._project`),
#: so an unknown forge fails when James edits `projects.toml` rather than eight minutes into
#: a run that has already spent model budget.
FORGES: dict[str, Forge] = {"github": github, "gitlab": gitlab}


def for_name(name: str) -> Forge:
    try:
        return FORGES[name]
    except KeyError:
        raise UnknownForge(f"unknown forge {name!r}: the adapters are {sorted(FORGES)}") from None


def for_url(pr_url: str) -> Forge:
    """The adapter for a PR/MR URL, when there is no project row left to ask.

    The narrow exception to `for_project`'s rule, and it is narrow on purpose: this is a
    *read* (`pr_state`) about a URL that already exists, not a decision about where to push.
    GitLab namespaces merge requests under `/-/merge_requests/`, which no GitHub URL
    contains, so the discrimination is exact rather than a guess. Anything else is GitHub,
    which is also the honest default for a URL from before the `forge` key existed.
    """
    return gitlab if "/-/merge_requests/" in pr_url else github


def for_project(project: Project) -> Forge:
    """The adapter for `project`, from its `forge` key.

    Read from the registry rather than sniffed from `project.remote`, deliberately. A
    typo'd or moved remote should fail loudly; inference would make it pick an adapter,
    authenticate with the wrong CLI's keyring credential and push to whatever that resolved
    to. §4.5's rule for the registry — validate, never default — applies to the question
    "which forge am I about to write to" more than to anything else in the file.
    """
    return for_name(project.forge)


@dataclass(frozen=True)
class SandboxSite:
    """Where an in-VM delivery would run: which sandbox, and where in it.

    A parameter object rather than three arguments, because the three are only ever
    meaningful together and `for_delivery` should not be the place someone can pass a
    sandbox name with another sandbox's workdir.
    """

    adapter: SandboxAdapter
    name: str
    #: The absolute path of the worktree, which is the same string on the host and in the
    #: VM for a bind-mounted project. `Project.requires_clone` is refused alongside
    #: `[sandbox_delivery]` at registry load precisely so that identity holds here.
    workdir: str


def for_delivery(project: Project, *, sandbox: SandboxSite) -> Forge:
    """The adapter that will **push and open the merge request** for this project.

    Separate from `for_project` rather than an optional argument on it, and the split is
    the point. `for_project` answers reads — `cli.py` and `gc.py` ask it for `pr_state`
    about a URL that already exists, and those stay host-side whatever a project has
    opted into. This one answers the write, and it is the only caller that can return the
    in-VM adapter, so a call site that wants one has to say so.

    An optional `sandbox=None` on a single function was the obvious alternative and is
    worse: a delivery step that forgot to pass it would silently get the host adapter,
    push over SSH from the host, and look exactly like a working run — a registry saying
    one thing while the machine does another, with nothing failing to say so.
    """
    declared = project.sandbox_delivery
    if declared is None:
        return for_name(project.forge)
    return sandbox_gitlab.SandboxGitlabForge(
        sandbox.adapter,
        sandbox.name,
        base_url=declared.api_url,
        project_path=declared.project_path,
        workdir=sandbox.workdir,
    )
