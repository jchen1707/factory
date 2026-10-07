"""Migration application cannot exceed the DDL the operator was shown."""

import argparse
import sqlite3
from pathlib import Path

import pytest

from factory.configuration_cli import migrate
from factory.machine import Blocked


def test_preview_does_not_create_a_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "unused.db"
    assert migrate(argparse.Namespace(database=path, apply=False)) == 0
    assert not path.exists()
    assert "Schema 9 -> 10" in capsys.readouterr().out


def test_apply_refuses_unreviewed_older_migrations(tmp_path: Path) -> None:
    path = tmp_path / "older.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=3")
    connection.close()
    before = path.read_bytes()
    with pytest.raises(Blocked, match="migration-review-required"):
        migrate(argparse.Namespace(database=path, apply=True))
    assert path.read_bytes() == before


def test_preview_from_schema_five_includes_the_actual_six_migration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "schema-five.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=5")
    connection.close()
    before = path.read_bytes()
    assert migrate(argparse.Namespace(database=path, apply=False)) == 0
    preview = capsys.readouterr().out
    assert "Schema 5 -> 10" in preview
    assert "runtime_certifications" in preview
    assert "agent_leases" in preview
    assert "DROP TABLE delegation_requests" in preview
    assert "DROP TABLE runtime_certifications" in preview
    assert "ALTER TABLE agent_leases DROP COLUMN parent_id" in preview
    assert "UPDATE attempts SET session_id = NULL" in preview
    assert path.read_bytes() == before


def test_preview_names_the_live_work_that_apply_would_refuse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.unit.test_store import _database_at

    path = tmp_path / "factory.db"
    _database_at(path, 8)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute(
        "INSERT INTO invocations (id, run_id, attempt, role, metadata, started_at, updated_at) "
        "VALUES ('probe', 'run', 1, 'certification', '{}', 0, 0)"
    )
    connection.execute(
        "INSERT INTO agent_leases VALUES ('probe', 'run', 'python-harness', 'active', NULL)"
    )
    connection.close()
    before = path.read_bytes()

    assert migrate(argparse.Namespace(database=path, apply=False)) == 0
    preview = capsys.readouterr().out
    assert "--apply refuses until no work is live: " in preview
    assert "child, certification or app-server invocations probe" in preview
    assert "agent invocations launched before the Claude cutover probe" in preview
    with pytest.raises(Blocked, match="migration-live-work"):
        migrate(argparse.Namespace(database=path, apply=True))
    assert path.read_bytes() == before
