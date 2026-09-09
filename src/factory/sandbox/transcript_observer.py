"""Controller-supplied stdlib probe, run inside the VM with isolated Python imports.

Codex 0.149.1/0.153.4's measured state_5.sqlite threads index supplies an exact rollout path.
Unknown/absent indices remain explicitly unavailable; never scan other conversations.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
from pathlib import Path
from uuid import UUID

LIMIT = 64 * 1024 * 1024


def export(session: str) -> dict[str, str]:
    if str(UUID(session)) != session:
        raise ValueError("invalid session")
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    if home.is_symlink() or home != home.resolve():
        raise ValueError("noncanonical runtime home")
    if not home.is_absolute():
        raise ValueError("relative runtime home")
    database = home / "state_5.sqlite"
    if database.is_symlink() or not database.is_file():
        raise ValueError("runtime index unavailable")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2) as connection:
        rows = connection.execute(
            "SELECT rollout_path FROM threads WHERE id=?", (session,)
        ).fetchall()
    if len(rows) != 1 or not isinstance(rows[0][0], str):
        raise ValueError("session path unavailable")
    path = Path(rows[0][0])
    path.relative_to(home / "sessions")
    if ".." in path.parts:
        raise ValueError("noncanonical session path")
    current = path
    while current != home:
        if current.is_symlink():
            raise ValueError("symlink in session path")
        current = current.parent
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("nonregular transcript")
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("transcript exceeds limit")
    # Interrupted writers may leave an incomplete final record. Validate complete
    # records but retain original bytes, so the host can label a truncated prefix.
    lines = []
    records = raw.decode("utf-8").splitlines()
    for index, line in enumerate(records):
        try:
            value = json.loads(line)
        except ValueError:
            if index != len(records) - 1:
                raise ValueError("invalid interior transcript record") from None
            break
        if not isinstance(value, dict):
            raise ValueError("invalid transcript record")
        lines.append(line)
    if not lines:
        raise ValueError("empty transcript")
    first = json.loads(lines[0])
    if first.get("type") != "session_meta" or first.get("payload", {}).get("id") != session:
        raise ValueError("session metadata mismatch")
    return {"session_id": session, "transcript": raw.decode("utf-8")}


if __name__ == "__main__":
    try:
        print(json.dumps(export(sys.argv[1])))
    except (OSError, ValueError, IndexError, AttributeError, sqlite3.Error):
        print(json.dumps({"outcome": "unavailable:native-transcript"}))
        raise SystemExit(1) from None
