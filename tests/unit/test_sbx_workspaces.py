"""`ensure` attaches to an existing sandbox only when it carries every workspace the spec needs.

`sbx inspect --json` reports the primary workspace alone. `sbx ls --json` lists all of them in
`Workspace.as_argument`'s spelling. Measured on sbx v0.38.0 (2026-10-07):
`sbx create --clone claude <repo> <rw> <ro>:ro` listed `[<repo>, <rw>, <ro>:ro]`, so a `--clone`
primary is listed bare and only a read-only mount carries `:ro`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from factory.sandbox.base import Completed, SandboxSpec, Workspace
from factory.sandbox.sbx import SbxAdapter, SbxError

NAME = "factory-build-demo"
PROJECT = "/Users/operator/code/demo"
AUTHORITY = "/Users/operator/factory/state/authority/demo"
VAULT = "/Users/operator/Documents/Vault"


def _adapter(listing: object, *, ok: bool = True) -> tuple[SbxAdapter, list[tuple[str, ...]]]:
    adapter = SbxAdapter()
    calls: list[tuple[str, ...]] = []

    def run(
        argv: Sequence[str],
        *,
        timeout: int | None = None,
        stdin: str | None = None,
        env: object = None,
    ) -> Completed:
        calls.append(tuple(argv))
        if list(argv) == ["sbx", "inspect", NAME]:
            return Completed(tuple(argv), 0, "", "")
        if list(argv) == ["sbx", "inspect", NAME, "--json"]:
            return Completed(tuple(argv), 0, json.dumps({"workspace": PROJECT}), "")
        assert list(argv) == ["sbx", "ls", "--json"], f"unexpected call {argv}"
        return Completed(tuple(argv), 0 if ok else 1, json.dumps(listing), "")

    adapter._run = run  # type: ignore[method-assign]
    return adapter, calls


def _listing(workspaces: object) -> dict[str, object]:
    return {
        "sandboxes": [
            {
                "agent": "claude",
                "id": "3f2c1a8e-0d7b-4c51-9a61-5b0e2f7d9c44",
                "name": NAME,
                "status": "stopped",
                "workspaces": workspaces,
            }
        ]
    }


def _spec(*workspaces: Workspace) -> SandboxSpec:
    return SandboxSpec(project="demo", role="build", name=NAME, workspaces=workspaces)


BUILD = (Workspace(Path(PROJECT)), Workspace(Path(AUTHORITY), readonly=True))


@pytest.mark.parametrize(
    "workspaces",
    [
        [PROJECT, VAULT],
        [PROJECT, AUTHORITY],
        [PROJECT, AUTHORITY + "-other:ro"],
        [PROJECT, AUTHORITY + "/run/1:ro"],
        [PROJECT, "/mnt" + AUTHORITY + ":ro"],
    ],
    ids=["absent", "writable", "sibling", "nested", "prefixed"],
)
def test_missing_required_authority_mount_refuses_before_execution(workspaces: list[str]) -> None:
    adapter, calls = _adapter(_listing(workspaces))
    with pytest.raises(SbxError, match="workspace") as caught:
        adapter.ensure(_spec(*BUILD))
    assert f"{AUTHORITY}:ro" in str(caught.value)
    assert all(call[1] in {"inspect", "ls"} for call in calls)


def test_a_read_only_vault_is_refused_when_the_spec_wants_it_writable() -> None:
    adapter, _ = _adapter(_listing([PROJECT, f"{AUTHORITY}:ro", f"{VAULT}:ro"]))
    with pytest.raises(SbxError, match="workspace"):
        adapter.ensure(_spec(*BUILD, Workspace(Path(VAULT))))


def test_a_sandbox_with_every_required_workspace_is_accepted() -> None:
    adapter, _ = _adapter(_listing([PROJECT, f"{AUTHORITY}:ro", VAULT, "/extra:ro"]))
    adapter.ensure(_spec(*BUILD, Workspace(Path(VAULT))))


@pytest.mark.parametrize(
    ("listing", "ok"),
    [
        (_listing([PROJECT, f"{AUTHORITY}:ro"]), False),
        ({"sandboxes": []}, True),
        (_listing(f"{PROJECT} {AUTHORITY}:ro"), True),
        (_listing([PROJECT, None]), True),
    ],
    ids=["ls-failed", "sandbox-unlisted", "not-a-list", "not-strings"],
)
def test_an_unreadable_workspace_listing_refuses(listing: object, ok: bool) -> None:
    adapter, _ = _adapter(listing, ok=ok)
    with pytest.raises(SbxError, match="workspace"):
        adapter.ensure(_spec(*BUILD))
