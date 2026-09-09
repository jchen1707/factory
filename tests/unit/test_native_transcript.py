"""Exercise actual indexed rollout export with isolated SQLite and runtime files."""

import json
import sqlite3
from pathlib import Path

import pytest

from factory.sandbox.transcript_observer import export

SESSION = "01a08462-9f0c-7e80-bdc4-78a787da8ca6"


@pytest.fixture
def rollout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    path = tmp_path / "sessions" / "measured-session.jsonl"
    path.parent.mkdir()
    path.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": SESSION}})
        + "\n"
        + json.dumps({"type": "response_item", "payload": {"role": "user", "text": "lesson"}})
        + "\n"
    )
    with sqlite3.connect(tmp_path / "state_5.sqlite") as connection:
        connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT)")
        connection.execute("INSERT INTO threads VALUES (?, ?)", (SESSION, str(path)))
    return path


def test_exact_session_export_survives_interrupted_last_record(rollout: Path) -> None:
    complete = rollout.read_text()
    rollout.write_text(complete + '{"partial":')
    assert export(SESSION) == {"session_id": SESSION, "transcript": complete + '{"partial":'}
    with pytest.raises(ValueError, match="session path unavailable"):
        export("01a08462-9f0c-7e80-bdc4-78a787da8ca7")


@pytest.mark.parametrize("kind", ["symlink", "parent", "wrong-session", "interior"])
def test_index_cannot_export_unrelated_or_malformed_content(rollout: Path, kind: str) -> None:
    home = rollout.parent.parent
    if kind == "symlink":
        elsewhere = home / "private.jsonl"
        rollout.rename(elsewhere)
        rollout.symlink_to(elsewhere)
    elif kind == "parent":
        with sqlite3.connect(home / "state_5.sqlite") as connection:
            connection.execute(
                "UPDATE threads SET rollout_path=?", (str(home / "sessions/../private"),)
            )
    elif kind == "wrong-session":
        rollout.write_text('{"type":"session_meta","payload":{"id":"another"}}\n')
    else:
        rollout.write_text(rollout.read_text() + "invalid\n{}\n")
    with pytest.raises(ValueError, match=r"symlink|noncanonical|metadata mismatch|interior"):
        export(SESSION)
