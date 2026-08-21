"""SQLite: runs, transitions, the effects ledger, attempts, checks, costs — §14.3.

`synchronous=FULL` because the cost of a lost transaction here is a **repeated side
effect**, not a lost row: the ledger's whole value is that its `intended` write is
durable before the external call happens.

The ledger is the answer to "on resume, do not repeat side effects" (§16.2). It is
never retried blindly. A row still `intended` after a crash is *reconciled* — the caller
asks the external system whether the marker is there — and only performed when it is
not.
"""

from __future__ import annotations

import os
import socket
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from factory.machine import TERMINAL, State

__all__ = ["Effect", "Run", "Store", "marker", "new_run_id", "owner_token"]

SCHEMA_VERSION = 2


def _runs_ddl(table: str) -> str:
    """The `runs` DDL, parameterised by table name because the 1 -> 2 migration rebuilds
    it under a temporary one and the two shapes must not drift.

    `linear_id` carries no column-level `UNIQUE`; `_LIVE_RUN_INDEX` states the real
    constraint instead.
    """
    return f"""
CREATE TABLE {table} (
  id TEXT PRIMARY KEY, linear_id TEXT NOT NULL,
  project TEXT NOT NULL, team TEXT NOT NULL,
  branch TEXT, base_ref TEXT, worktree TEXT,
  state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT, lease_expires_at INTEGER,
  blocked_reason TEXT, pr_url TEXT,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
"""


#: Named so `INSERT INTO ... SELECT` never relies on column order surviving a rebuild.
_RUNS_COLUMNS = (
    "id, linear_id, project, team, branch, base_ref, worktree, state, attempt, "
    "lease_owner, lease_expires_at, blocked_reason, pr_url, created_at, updated_at"
)

#: §7.2's race protection, narrowed to what it actually guarantees: **one live run per
#: ticket**, not one run per ticket for all time.
#:
#: A column-level `UNIQUE` said both, and the second half broke the rollback contract:
#: `factory cancel` releases the lease and puts the ticket back to `Todo`, but the
#: cancelled row sat in `runs` forever, so `factory run` refused the very rerun
#: `cmd_run` advises. Restricting the index to the live states keeps "a second poller
#: or a second tick cannot create a second run" exactly as §7.2 words it, while letting
#: a terminal run be *succeeded* rather than resurrected — which is what keeps
#: `cancelled` terminal (§5.1) and the next attempt directory at `run/1` (§19).
#:
#: `blocked` is deliberately live: it is a stop, not an end, so a blocked run still
#: holds its ticket until someone cancels it. That is the contract, not an oversight.
#:
#: Derived from `machine.TERMINAL` rather than spelled out, so the index and the state
#: machine cannot disagree — but note that an existing database keeps the index it was
#: built with, so adding a terminal state needs SCHEMA_VERSION 3 and a rebuild.
#: `test_terminal_states_are_pinned_to_the_live_run_index` fails if that is forgotten.
_LIVE_RUN_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS runs_one_live_per_ticket ON runs(linear_id) "
    f"WHERE state NOT IN ({', '.join(chr(39) + str(s) + chr(39) for s in sorted(TERMINAL))})"
)

_SCHEMA = (
    _runs_ddl("runs")
    + """
CREATE TABLE transitions (
  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
  from_state TEXT, to_state TEXT NOT NULL, actor TEXT NOT NULL,
  rule TEXT, detail TEXT, at INTEGER NOT NULL
);
CREATE TABLE effects (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL, attempt INTEGER NOT NULL, step TEXT NOT NULL,
  system TEXT NOT NULL,
  key TEXT NOT NULL,
  status TEXT NOT NULL,
  external_id TEXT, at INTEGER NOT NULL,
  UNIQUE (run_id, attempt, step, system, key)
);
CREATE TABLE attempts (
  run_id TEXT NOT NULL, attempt INTEGER NOT NULL, state TEXT NOT NULL,
  sandbox TEXT, session_id TEXT, started_at INTEGER, ended_at INTEGER,
  exit_code INTEGER, outcome TEXT, artifact_dir TEXT,
  PRIMARY KEY (run_id, attempt, state)
);
CREATE TABLE checks (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id), attempt INTEGER NOT NULL,
  check_name TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT, detail TEXT, artifact TEXT, at INTEGER NOT NULL
);
CREATE INDEX checks_by_run ON checks (run_id, check_name);
CREATE TABLE costs (
  run_id TEXT NOT NULL, attempt INTEGER NOT NULL, step TEXT NOT NULL,
  model TEXT, input_tokens INTEGER, output_tokens INTEGER,
  cached_tokens INTEGER, usd REAL, at INTEGER NOT NULL
);
"""
)


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def marker(run_id: str, attempt: int, step: str) -> str:
    """`factory:<run>:<attempt>:<step>` — §16.2.

    Embedded as an HTML comment in Linear and GitHub bodies: invisible to a reader and
    exact for a search, which is what makes reconciliation a lookup rather than a guess.
    """
    return f"factory:{run_id}:{attempt}:{step}"


def owner_token() -> str:
    """Who holds a lease. Hostname and pid, so a stale holder is identifiable."""
    return f"{socket.gethostname()}:{os.getpid()}"


@dataclass(frozen=True)
class Run:
    id: str
    linear_id: str
    project: str
    team: str
    branch: str | None
    base_ref: str | None
    worktree: str | None
    state: State
    attempt: int
    lease_owner: str | None
    lease_expires_at: int | None
    blocked_reason: str | None
    pr_url: str | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Run:
        return cls(
            id=row["id"],
            linear_id=row["linear_id"],
            project=row["project"],
            team=row["team"],
            branch=row["branch"],
            base_ref=row["base_ref"],
            worktree=row["worktree"],
            state=State(row["state"]),
            attempt=row["attempt"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            blocked_reason=row["blocked_reason"],
            pr_url=row["pr_url"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class Effect:
    run_id: str
    attempt: int
    step: str
    system: str
    key: str
    status: str
    external_id: str | None
    at: int


class Store:
    """The database. One process, one connection, WAL."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    # -- migrations ---------------------------------------------------------------

    def _migrate(self) -> None:
        current = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if current >= SCHEMA_VERSION:
            return
        if current == 0:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(_LIVE_RUN_INDEX)
        elif current == 1:
            self._upgrade_1_to_2()
        self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _upgrade_1_to_2(self) -> None:
        """Replace the column-level `UNIQUE` on `runs.linear_id` with `_LIVE_RUN_INDEX`.

        SQLite cannot drop a column constraint in place, so this is the documented
        twelve-step table rebuild (https://sqlite.org/lang_altertable.html#otheralter),
        reduced to what applies here. Foreign keys go **off** around the swap: the
        ledger tables reference `runs(id)`, and dropping the old table with enforcement
        on would take the audit history with it — the one thing a migration here must
        never do. `foreign_key_check` afterwards proves it did not.
        """
        self._conn.execute("PRAGMA foreign_keys=OFF")
        try:
            with self.transaction():
                self._conn.execute(_runs_ddl("runs_migrating"))
                self._conn.execute(
                    f"INSERT INTO runs_migrating ({_RUNS_COLUMNS}) "  # noqa: S608 - names above
                    f"SELECT {_RUNS_COLUMNS} FROM runs"
                )
                self._conn.execute("DROP TABLE runs")
                self._conn.execute("ALTER TABLE runs_migrating RENAME TO runs")
                self._conn.execute(_LIVE_RUN_INDEX)
            orphans = self._conn.execute("PRAGMA foreign_key_check").fetchall()
            if orphans:
                raise RuntimeError(
                    f"migration 1 -> 2 left {len(orphans)} orphaned ledger rows; "
                    "the database was not modified beyond this point and the backup "
                    "SQLite keeps in the WAL is the recovery path"
                )
        finally:
            self._conn.execute("PRAGMA foreign_keys=ON")

    def integrity_ok(self) -> tuple[bool, str]:
        result = self._conn.execute("PRAGMA integrity_check").fetchone()[0]
        return result == "ok", result

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    # -- runs ---------------------------------------------------------------------

    def insert_run(
        self, *, linear_id: str, project: str, team: str, state: State = State.APPROVED
    ) -> Run:
        """`INSERT OR IGNORE` against `_LIVE_RUN_INDEX` — §7.2 phase one.

        Two ticks racing the same ticket cannot create two runs: the index makes the
        second insert a no-op, so the loser reads the existing row and finds it leased,
        which is where F12 ends.

        A ticket whose previous run reached a terminal state gets a **new** row rather
        than the old one back, because the index only covers the live states. That is
        what makes `factory cancel` followed by `factory run` work, and it keeps the
        ledger for the abandoned run intact and separately addressable by its own id.
        """
        now = int(time.time())
        run_id = new_run_id()
        inserted = self._conn.execute(
            "INSERT OR IGNORE INTO runs "
            "(id, linear_id, project, team, state, attempt, created_at, updated_at) "
            "VALUES (?,?,?,?,?,0,?,?)",
            (run_id, linear_id, project, team, str(state), now, now),
        ).rowcount
        # Ask for the row by the id just written rather than by ticket: a terminal run
        # and this fresh one can share a `created_at` second, and picking the wrong one
        # here would hand the caller a run it must not touch.
        got = self.run_by_id(run_id) if inserted else self.run_by_ticket(linear_id)
        assert got is not None  # noqa: S101 - the INSERT above guarantees a row
        return got

    def run_by_ticket(self, linear_id: str) -> Run | None:
        """The ticket's current run — the live one if there is one, else the most recent.

        A ticket can now own several rows over its life (see `insert_run`), and every
        caller wants the newest: `cancel` rolls back what just happened, `status`
        reports where the ticket stands. `rowid` breaks a same-second tie in insert
        order, which is the order they happened in.
        """
        row = self._conn.execute(
            "SELECT * FROM runs WHERE linear_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (linear_id,),
        ).fetchone()
        return Run.from_row(row) if row else None

    def run_by_id(self, run_id: str) -> Run | None:
        row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return Run.from_row(row) if row else None

    def all_runs(self) -> list[Run]:
        rows = self._conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        return [Run.from_row(r) for r in rows]

    def active_runs_for_project(self, project: str) -> list[Run]:
        """Runs holding a writer for a project — the §11.3 concurrency check."""
        busy = (State.IMPLEMENTING, State.VERIFYING, State.REVIEWING, State.PLANNING)
        rows = self._conn.execute(
            f"SELECT * FROM runs WHERE project = ? AND state IN "  # noqa: S608 - placeholders below
            f"({','.join('?' * len(busy))})",
            (project, *[str(s) for s in busy]),
        ).fetchall()
        return [Run.from_row(r) for r in rows]

    def update_run(self, run_id: str, **fields: object) -> None:
        if not fields:
            return
        allowed = {
            "branch",
            "base_ref",
            "worktree",
            "attempt",
            "blocked_reason",
            "pr_url",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"update_run does not write {sorted(unknown)}")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(
            f"UPDATE runs SET {assignments}, updated_at = ? WHERE id = ?",  # noqa: S608 - keys checked above
            (*fields.values(), int(time.time()), run_id),
        )

    # -- leases -------------------------------------------------------------------

    def acquire_lease(self, run_id: str, *, ttl_seconds: int, owner: str | None = None) -> bool:
        """§7.2 phase two. The row count tells the caller whether it won.

        A lease is stealable only once it has expired. F13's rule — a second process
        must not steal a lease whose heartbeat is fresh — is this predicate plus the
        holder renewing on every step.
        """
        now = int(time.time())
        token = owner or owner_token()
        cursor = self._conn.execute(
            "UPDATE runs SET lease_owner = ?, lease_expires_at = ?, updated_at = ? "
            "WHERE id = ? AND (lease_owner IS NULL OR lease_owner = ? OR lease_expires_at < ?)",
            (token, now + ttl_seconds, now, run_id, token, now),
        )
        return cursor.rowcount == 1

    def renew_lease(self, run_id: str, *, ttl_seconds: int, owner: str | None = None) -> bool:
        now = int(time.time())
        token = owner or owner_token()
        cursor = self._conn.execute(
            "UPDATE runs SET lease_expires_at = ?, updated_at = ? WHERE id = ? AND lease_owner = ?",
            (now + ttl_seconds, now, run_id, token),
        )
        return cursor.rowcount == 1

    def release_lease(self, run_id: str) -> None:
        self._conn.execute(
            "UPDATE runs SET lease_owner = NULL, lease_expires_at = NULL, updated_at = ? "
            "WHERE id = ?",
            (int(time.time()), run_id),
        )

    def holds_lease(self, run_id: str, *, owner: str | None = None) -> bool:
        token = owner or owner_token()
        row = self._conn.execute(
            "SELECT lease_owner, lease_expires_at FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None or row["lease_owner"] != token:
            return False
        return bool(row["lease_expires_at"] and row["lease_expires_at"] > int(time.time()))

    # -- transitions --------------------------------------------------------------

    def record_transition(
        self,
        run_id: str,
        *,
        from_state: State | None,
        to_state: State,
        actor: str,
        rule: str | None = None,
        detail: str | None = None,
    ) -> None:
        now = int(time.time())
        self._conn.execute(
            "INSERT INTO transitions (run_id, from_state, to_state, actor, rule, detail, at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                run_id,
                str(from_state) if from_state else None,
                str(to_state),
                actor,
                rule,
                detail,
                now,
            ),
        )
        self._conn.execute(
            "UPDATE runs SET state = ?, updated_at = ? WHERE id = ?", (str(to_state), now, run_id)
        )

    def transitions(self, run_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM transitions WHERE run_id = ? ORDER BY at, id", (run_id,)
            ).fetchall()
        )

    # -- effects ------------------------------------------------------------------

    def intend_effect(self, run_id: str, attempt: int, step: str, system: str, key: str) -> Effect:
        """Step 1 of §16.2, committed before the external call happens."""
        self._conn.execute(
            "INSERT OR IGNORE INTO effects "
            "(run_id, attempt, step, system, key, status, at) VALUES (?,?,?,?,?,'intended',?)",
            (run_id, attempt, step, system, key, int(time.time())),
        )
        found = self.find_effect(run_id, attempt, step, system, key)
        assert found is not None  # noqa: S101 - the INSERT above guarantees a row
        return found

    def confirm_effect(
        self, run_id: str, attempt: int, step: str, system: str, key: str, external_id: str | None
    ) -> None:
        self._conn.execute(
            "UPDATE effects SET status = 'confirmed', external_id = ?, at = ? "
            "WHERE run_id = ? AND attempt = ? AND step = ? AND system = ? AND key = ?",
            (external_id, int(time.time()), run_id, attempt, step, system, key),
        )

    def find_effect(
        self, run_id: str, attempt: int, step: str, system: str, key: str
    ) -> Effect | None:
        row = self._conn.execute(
            "SELECT * FROM effects WHERE run_id = ? AND attempt = ? AND step = ? "
            "AND system = ? AND key = ?",
            (run_id, attempt, step, system, key),
        ).fetchone()
        if row is None:
            return None
        return Effect(
            run_id=row["run_id"],
            attempt=row["attempt"],
            step=row["step"],
            system=row["system"],
            key=row["key"],
            status=row["status"],
            external_id=row["external_id"],
            at=row["at"],
        )

    def effects(self, run_id: str) -> list[Effect]:
        rows = self._conn.execute(
            "SELECT * FROM effects WHERE run_id = ? ORDER BY at, id", (run_id,)
        ).fetchall()
        return [
            Effect(
                run_id=r["run_id"],
                attempt=r["attempt"],
                step=r["step"],
                system=r["system"],
                key=r["key"],
                status=r["status"],
                external_id=r["external_id"],
                at=r["at"],
            )
            for r in rows
        ]

    # -- attempts, checks, costs --------------------------------------------------

    def start_attempt(
        self, run_id: str, attempt: int, state: State, *, sandbox: str, artifact_dir: str
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO attempts "
            "(run_id, attempt, state, sandbox, started_at, artifact_dir) VALUES (?,?,?,?,?,?)",
            (run_id, attempt, str(state), sandbox, int(time.time()), artifact_dir),
        )
        self._conn.execute(
            "UPDATE runs SET attempt = ?, updated_at = ? WHERE id = ?",
            (attempt, int(time.time()), run_id),
        )

    def finish_attempt(
        self,
        run_id: str,
        attempt: int,
        state: State,
        *,
        exit_code: int | None,
        outcome: str,
        session_id: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE attempts SET ended_at = ?, exit_code = ?, outcome = ?, "
            "session_id = COALESCE(?, session_id) "
            "WHERE run_id = ? AND attempt = ? AND state = ?",
            (int(time.time()), exit_code, outcome, session_id, run_id, attempt, str(state)),
        )

    def set_session_id(self, run_id: str, attempt: int, state: State, session_id: str) -> None:
        """Stored before the run is considered started, so a crash can resume by id.

        Never `codex exec resume --last`: on a machine running several tickets that
        picks "the most recent recorded session", which is a coin flip (§16.3).
        """
        self._conn.execute(
            "UPDATE attempts SET session_id = ? WHERE run_id = ? AND attempt = ? AND state = ?",
            (session_id, run_id, attempt, str(state)),
        )

    def session_id(self, run_id: str, attempt: int, state: State) -> str | None:
        row = self._conn.execute(
            "SELECT session_id FROM attempts WHERE run_id = ? AND attempt = ? AND state = ?",
            (run_id, attempt, str(state)),
        ).fetchone()
        return row["session_id"] if row else None

    def record_check(
        self,
        run_id: str,
        attempt: int,
        check_name: str,
        status: str,
        *,
        reason: str | None = None,
        detail: str | None = None,
        artifact: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO checks (run_id, attempt, check_name, status, reason, detail, artifact, at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (run_id, attempt, check_name, status, reason, detail, artifact, int(time.time())),
        )

    def checks(self, run_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM checks WHERE run_id = ? ORDER BY at, id", (run_id,)
            ).fetchall()
        )

    def record_cost(
        self,
        run_id: str,
        attempt: int,
        step: str,
        *,
        model: str | None,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        usd: float | None,
    ) -> None:
        """`usd` is None when no price row covers the model.

        Never 0. A zero reads as a free run rather than an unpriced one, and the
        difference decides whether the budget ceiling means anything.
        """
        self._conn.execute(
            "INSERT INTO costs (run_id, attempt, step, model, input_tokens, output_tokens, "
            "cached_tokens, usd, at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                attempt,
                step,
                model,
                input_tokens,
                output_tokens,
                cached_tokens,
                usd,
                int(time.time()),
            ),
        )

    def costs(self, run_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM costs WHERE run_id = ? ORDER BY at, rowid", (run_id,)
            ).fetchall()
        )

    def spend(self, run_id: str) -> tuple[int, int, float | None]:
        """`(input_tokens, output_tokens, usd or None)` for the whole run."""
        rows = self.costs(run_id)
        tokens_in = sum(r["input_tokens"] or 0 for r in rows)
        tokens_out = sum(r["output_tokens"] or 0 for r in rows)
        priced = [r["usd"] for r in rows if r["usd"] is not None]
        return tokens_in, tokens_out, (sum(priced) if priced else None)
