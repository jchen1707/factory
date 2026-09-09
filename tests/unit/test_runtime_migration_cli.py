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
    assert "Schema 6 -> 7" in capsys.readouterr().out


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
    assert "Schema 5 -> 7" in preview
    assert "runtime_certifications" in preview
    assert "agent_leases" in preview
    assert "delegation_requests" in preview
    assert path.read_bytes() == before
