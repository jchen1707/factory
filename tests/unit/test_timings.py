"""Phase 2 — the timings sidecar writer.

The tailer is the one part of the producer that is unit-testable in isolation: it reads a
growing `events.jsonl` and writes one `{"observed_at": <epoch>}` row per line to its
sibling `events.timings.jsonl`, stopping when the attempt's `exit` appears. The console's
`read_tool_calls` pairs these with `item.started`/`item.completed` (by item `id`) to defend
a per-call `duration_s`; that pairing is covered in `test_console.py`. Here we hold the
writer to its own contract: one row per complete line, non-decreasing timestamps, and a
clean stop at `exit`.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from factory.agent.timings import _timings_path, stamp_timings


def _append(events: Path, line: str, *, gap: float = 0.06) -> None:
    with events.open("a", encoding="utf-8") as handle:
        handle.write(line)
    time.sleep(gap)  # > the tailer's poll interval, so the line gets its own observed_at


def test_stamp_timings_records_one_row_per_line_and_stops_at_exit(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    exit_file = tmp_path / "exit"
    done = threading.Event()

    def run() -> None:
        stamp_timings(events, exit_file, poll_interval=0.02, idle_timeout=10)
        done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    time.sleep(0.04)  # let the tailer start before the file exists

    _append(events, json.dumps({"type": "thread.started", "thread_id": "01a0"}) + "\n")
    _append(
        events,
        json.dumps({"type": "item.started", "item": {"id": "i1", "type": "command_execution"}})
        + "\n",
    )
    _append(
        events,
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "i1", "type": "command_execution", "command": "ls", "exit_code": 0},
            }
        )
        + "\n",
    )
    exit_file.write_text("0", encoding="utf-8")

    assert done.wait(timeout=5)
    rows = [
        json.loads(line)
        for line in _timings_path(events).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 3  # one row per complete line
    ts = [row["observed_at"] for row in rows]
    assert ts == sorted(ts)  # non-decreasing
    assert ts[2] > ts[0]  # lines written apart get distinct observed_at


def test_stamp_timings_does_not_stamp_a_partial_trailing_line(tmp_path: Path) -> None:
    # The normal state of a file a live agent is writing: a final line without a newline.
    # It must not be stamped until it completes, or its `observed_at` would precede the
    # moment codex actually finished writing it.
    events = tmp_path / "events.jsonl"
    exit_file = tmp_path / "exit"
    done = threading.Event()

    def run() -> None:
        stamp_timings(events, exit_file, poll_interval=0.02, idle_timeout=10)
        done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    time.sleep(0.04)
    _append(events, json.dumps({"type": "thread.started", "thread_id": "01a0"}) + "\n")
    # a partial line (no newline) — must not be stamped yet
    with events.open("a", encoding="utf-8") as partial:
        partial.write('{"type":"item.comp')
    time.sleep(0.08)  # several polls pass; the partial is still incomplete
    assert _timings_path(events).read_text(encoding="utf-8").count("\n") == 1
    # complete it
    with events.open("a", encoding="utf-8") as handle:
        handle.write('leted","item":{"id":"i1","type":"agent_message","text":"done"}}\n')
    exit_file.write_text("0", encoding="utf-8")
    assert done.wait(timeout=5)
    rows = [
        json.loads(line)
        for line in _timings_path(events).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 2  # the thread.started line + the now-completed item.completed
