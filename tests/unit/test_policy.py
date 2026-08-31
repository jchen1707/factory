"""§21.1 — the host-execution guard, the sandbox namespace, and the vault allowlist."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.policy import (
    HOST_EXECUTION_DENY,
    VaultChange,
    assert_factory_sandbox,
    assert_no_skip_verify,
    capability_env_names,
    capability_secrets,
    diff_vault,
    host_execution_verdict,
    sandbox_is_factory_owned,
    snapshot_vault,
    vault_writes_outside_allowlist,
)


@pytest.mark.parametrize(
    "path",
    [
        ".husky/pre-commit",
        ".github/workflows/ci.yml",
        ".pre-commit-config.yaml",
        "Makefile",
        "build.mk",
        "package.json",
        ".claude/settings.json",
        ".codex/hooks.json",
        "harness.config.json",
        ".gitattributes",
        # The GitLab pipeline definitions, at the root and in a monorepo package.
        ".gitlab-ci.yml",
        "packages/api/.gitlab-ci.yml",
        ".gitlab/ci/build.yml",
    ],
)
def test_every_deny_entry_escalates(path: str) -> None:
    verdict, hits = host_execution_verdict([path])
    assert verdict == "awaiting_human"
    assert hits == [path]


def test_vendored_tree_blocks_rather_than_escalating() -> None:
    # protect_paths.mjs should already have refused this write, so a hit means the
    # enforcement layer failed — which is not a thing a human reads a diff about.
    # §22 F11 — a diff touching a host-executed path routes to `awaiting_human`
    # BEFORE any push; §22 F9 covers the vendored tree specifically.
    verdict, hits = host_execution_verdict([".agents/vendor/harness/hooks/lib.mjs"])
    assert verdict == "blocked"
    assert hits == [".agents/vendor/harness/hooks/lib.mjs"]


def test_block_wins_over_escalate() -> None:
    verdict, _ = host_execution_verdict([".github/workflows/ci.yml", ".agents/vendor/x.mjs"])
    assert verdict == "blocked"


def test_an_ordinary_diff_is_clear() -> None:
    verdict, hits = host_execution_verdict(["src/app/main.py", "tests/test_main.py", "README.md"])
    assert verdict == "clear"
    assert hits == []


def test_the_deny_list_is_not_empty() -> None:
    assert len(HOST_EXECUTION_DENY) >= 10


@pytest.mark.parametrize(
    ("name", "owned"),
    [
        ("factory-build-python-harness", True),
        ("factory-review-python-harness", True),
        ("codex-python-harness", False),
        ("codex-factory", False),
        ("factory-probe-py", False),
        ("", False),
    ],
)
def test_sandbox_namespace(name: str, owned: bool) -> None:
    assert sandbox_is_factory_owned(name) is owned
    if owned:
        assert_factory_sandbox(name)
    else:
        with pytest.raises(PermissionError):
            assert_factory_sandbox(name)


def test_harness_skip_verify_is_refused() -> None:
    assert_no_skip_verify({"PATH": "/usr/bin"})
    # §22 F8 — `HARNESS_SKIP_VERIFY=1` leaking into the run env disables layer A's
    # Stop gate, so the preflight refuses the run rather than reporting a silent green.
    with pytest.raises(PermissionError, match="HARNESS_SKIP_VERIFY"):
        assert_no_skip_verify({"HARNESS_SKIP_VERIFY": "1"})


def test_vault_snapshot_and_diff(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "Project Learnings").mkdir(parents=True)
    (vault / ".obsidian").mkdir()
    (vault / "Project Learnings" / "2026-08-20.md").write_text("one")
    (vault / "_VAULT_INDEX.md").write_text("index")
    (vault / "Upskilling").mkdir()
    (vault / "Upskilling" / "notes.md").write_text("mine")
    (vault / ".obsidian" / "workspace.json").write_text("{}")

    before = snapshot_vault(vault)
    assert ".obsidian/workspace.json" not in before  # UI state is not a run's doing

    (vault / "Project Learnings" / "2026-08-21.md").write_text("allowed")
    (vault / "Upskilling" / "notes.md").write_text("rewritten by the agent")
    after = snapshot_vault(vault)

    changes = diff_vault(before, after)
    assert VaultChange("Project Learnings/2026-08-21.md", "added") in changes
    assert VaultChange("Upskilling/notes.md", "modified") in changes

    offending = vault_writes_outside_allowlist(changes, ["Project Learnings/**", "_VAULT_INDEX.md"])
    assert [c.path for c in offending] == ["Upskilling/notes.md"]


def test_a_deletion_inside_the_allowlist_is_still_reported(tmp_path: Path) -> None:
    # The distiller only ever adds or rewrites its own dated note, so a delete there is
    # not the hook's work either.
    vault = tmp_path / "vault"
    (vault / "Project Learnings").mkdir(parents=True)
    note = vault / "Project Learnings" / "old.md"
    note.write_text("x")
    before = snapshot_vault(vault)
    note.unlink()
    offending = vault_writes_outside_allowlist(
        diff_vault(before, snapshot_vault(vault)), ["Project Learnings/**"]
    )
    assert [c.kind for c in offending] == ["deleted"]


# --------------------------------------------------------------------------------
# §8.7 — which injected secrets are a capability
# --------------------------------------------------------------------------------

#: Verbatim from `sbx inspect factory-build-python-harness --json` on 2026-08-21, taken
#: seconds after the factory created the sandbox itself. P0-5 predicted an empty array.
MEASURED_SECRETS = [
    {"name": "github", "source": "uploaded"},
    {"name": "mcpgateway", "source": "uploaded"},
]


def test_a_service_secret_is_a_capability_even_beside_the_gateway() -> None:
    assert capability_secrets(MEASURED_SECRETS) == ["github"]


def test_the_gateway_credential_alone_is_not_a_capability() -> None:
    # It cannot be removed per-sandbox, so treating it as a violation makes the check
    # unsatisfiable rather than strict. `Defaults.deny_network` is what compensates.
    assert capability_secrets([{"name": "mcpgateway", "source": "uploaded"}]) == []


def test_an_empty_secret_set_is_clean() -> None:
    assert capability_secrets([]) == []


def test_every_other_service_secret_is_reported_sorted() -> None:
    secrets = [{"name": "openai"}, {"name": "mcpgateway"}, {"name": "anthropic"}]
    assert capability_secrets(secrets) == ["anthropic", "openai"]


def test_malformed_entries_are_ignored_rather_than_crashing_the_preflight() -> None:
    # `sbx` is not ours and its JSON shape is not a contract we control. A preflight
    # that raises on an unexpected entry fails the run for the wrong reason.
    assert capability_secrets([None, "github", {}, {"name": ""}, {"name": "gh"}]) == ["gh"]


# --------------------------------------------------------------------------------
# §8.7 — the second channel: credentials in the VM's environment
# --------------------------------------------------------------------------------

#: Measured 2026-08-22 inside `factory-review-python-harness`, asking the repository's own
#: `secretVars` which of them were set. `sbx inspect` on the same sandbox reported only the
#: gateway credential, which is the whole point: one channel cannot see the other.
MEASURED_ENV_CREDENTIALS = ["GH_TOKEN"]


def test_an_unacknowledged_env_credential_blocks() -> None:
    blocking, known = capability_env_names(MEASURED_ENV_CREDENTIALS, acknowledged=())
    assert blocking == ["GH_TOKEN"]
    assert known == []


def test_an_acknowledged_env_credential_is_reported_rather_than_blocking() -> None:
    blocking, known = capability_env_names(MEASURED_ENV_CREDENTIALS, acknowledged=("GH_TOKEN",))
    assert blocking == []
    assert known == ["GH_TOKEN"]  # recorded as a warning on every run, never silent


def test_acknowledging_one_name_does_not_acknowledge_another() -> None:
    # The failure this guards: a project that has looked at the forge token and decided to
    # live with it has said nothing about a tracker key turning up beside it later.
    blocking, known = capability_env_names(
        ["GH_TOKEN", "LINEAR_API_KEY"], acknowledged=("GH_TOKEN",)
    )
    assert blocking == ["LINEAR_API_KEY"]
    assert known == ["GH_TOKEN"]


def test_nothing_set_in_the_environment_is_clean() -> None:
    assert capability_env_names([], acknowledged=("GH_TOKEN",)) == ([], [])
