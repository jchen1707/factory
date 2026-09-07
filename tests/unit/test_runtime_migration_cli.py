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
    assert "Schema 4 -> 5" in capsys.readouterr().out


def test_apply_refuses_unreviewed_older_migrations(tmp_path: Path) -> None:
    path = tmp_path / "older.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=3")
    connection.close()
    before = path.read_bytes()
    with pytest.raises(Blocked, match="migration-review-required"):
        migrate(argparse.Namespace(database=path, apply=True))
    assert path.read_bytes() == before
