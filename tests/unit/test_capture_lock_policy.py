"""Capture locks are transient evidence, not deleted learning notes."""

import pytest

from factory.policy import (
    VaultChange,
    diff_vault,
    disallowed_vault_writes,
    unattributable_vault_changes,
    vault_writes_outside_allowlist,
)

LOCK = "Project Learnings/._capture-" + "a" * 64 + ".lock/owner.json"
ALLOW = ["Project Learnings/**", "_VAULT_INDEX.md"]


def test_capture_lock_cleanup_is_retained_without_blocking() -> None:
    changes = diff_vault({LOCK: (1, 1, "digest")}, {})
    assert changes == [VaultChange(LOCK, "deleted")]
    assert disallowed_vault_writes(changes, ALLOW) == []
    assert unattributable_vault_changes(changes, ALLOW) == changes
    assert vault_writes_outside_allowlist(changes, ALLOW) == changes


@pytest.mark.parametrize(
    "path",
    [
        "Project Learnings/note.md",
        "Project Learnings/_INDEX.md",
        "_VAULT_INDEX.md",
        LOCK.replace("owner.json", "note.md"),
        LOCK.replace("owner.json", "nested/owner.json"),
        LOCK.replace("a" * 64, "a" * 63),
        LOCK.replace("a" * 64, "A" * 64),
        LOCK.replace("a" * 64, "g" * 64),
        LOCK.replace("Project Learnings/", "Project Learnings/nested/"),
        LOCK + ".bak",
    ],
)
def test_lock_exception_does_not_hide_note_or_lookalike_deletions(path: str) -> None:
    changes = [VaultChange(path, "deleted")]
    assert disallowed_vault_writes(changes, ALLOW) == changes
    assert unattributable_vault_changes(changes, ALLOW) == []


def test_lock_and_note_deleted_together_still_block_note() -> None:
    lock = VaultChange(LOCK, "deleted")
    note = VaultChange("Project Learnings/note.md", "deleted")
    assert disallowed_vault_writes([lock, note], ALLOW) == [note]
    assert unattributable_vault_changes([lock, note], ALLOW) == [lock]


def test_lock_outside_allowed_set_remains_unattributable() -> None:
    changes = [VaultChange(LOCK, "deleted")]
    assert disallowed_vault_writes(changes, []) == []
    assert unattributable_vault_changes(changes, []) == changes
