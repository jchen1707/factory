"""Collection retains transient cleanup without rewriting a live run or vault."""

import json

import pytest

from factory import policy
from factory.machine import Blocked
from factory.steps import Context
from factory.steps.implement import _check_vault


@pytest.mark.parametrize("with_note", [False, True])
def test_capture_cleanup_retains_evidence_and_note_deletions_block(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, with_note: bool
) -> None:
    lock = "Project Learnings/._capture-" + "a" * 64 + ".lock/owner.json"
    before = {lock: (1, 1, "digest")}
    if with_note:
        before["Project Learnings/note.md"] = (1, 1, "note-digest")
    monkeypatch.setattr(policy, "snapshot_vault", lambda *args, **kwargs: {})
    if with_note:
        with pytest.raises(Blocked, match="vault-write-outside-allowlist"):
            _check_vault(ctx, 1, before)
    else:
        _check_vault(ctx, 1, before)
    row = ctx.store._conn.execute(
        "SELECT status, reason, detail FROM checks WHERE run_id=? AND check_name='vault_snapshot'",
        (ctx.run.id,),
    ).fetchone()
    assert row is not None
    assert row["status"] == ("fail" if with_note else "warn")
    assert {"path": lock, "kind": "deleted"} in json.loads(row["detail"])
    if not with_note:
        assert "transient-lock" in row["reason"]
    assert (ctx.state_dir / "vault" / "before-1.json").exists()
    assert (ctx.state_dir / "vault" / "after-1.json").exists()
