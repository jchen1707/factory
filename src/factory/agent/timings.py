"""The Phase 2 timings sidecar writer — a host-side observer that stamps each line of an
attempt's `events.jsonl` with an `observed_at` wall clock as it appears, writing one
`{"observed_at": <epoch>}` row per line to `events.timings.jsonl` beside it. The console's
`read_tool_calls` pairs a call's `item.completed` with its `item.started` (by item `id`) to
defend a per-call `duration_s` it could not show in Phase 1.

Why a host tailer and not an in-sandbox stampler. The codex wrapper redirects codex's
stdout straight to `events.jsonl`, and `detached_shell_script`'s `code=$?` *is* the exit
protocol `poll`/`reap` rely on (§4.2). Piping codex through a shell stampler would break
that capture, and the `awk`/`/bin/sh` dialect differs between the macOS host and the Linux
sandbox the wrapper runs in. A host-side Python tailer reads the same file — the attempt
directory's path is identical on host and VM (RunHandle, §8.5), which is already what makes
the console's live tail work — without touching the wrapper, stamps with `time.time()`
(sub-second), and stops the moment the attempt's `exit` file appears.

Opt-in and bounded. The step spawns the tailer only when
`registry.defaults.timings` is set, so it is James's call (the plan reserves the writer as
a boundary change) and the test suite — which does not set it — never spawns a real
process. A tailer whose `exit` never appears (a host crash mid-run) stops itself after
`idle_timeout` seconds with no new line, so an orphan cannot run forever. The sidecar is
archived and swept with the attempt directory by the existing `gc` step (`artifacts.archive`
is a `copytree`; the worktree removal takes the working copy), so no second cleanup path.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

__all__ = ["spawn", "stamp_timings"]

#: A poll that finds no new complete line sleeps this long before looking again. Short
#: enough that calls seconds apart land in different polls (so their `observed_at` differs),
#: long enough that a 30-minute implement is not a busy loop.
_POLL_INTERVAL = 0.1

#: A stuck or orphaned run: stop after this many seconds with no new line, even if `exit`
#: has not appeared. A host reboot under a live run leaves the tailer with no `exit` to
#: observe; this is the backstop that keeps an orphan from running until the heat death.
_IDLE_TIMEOUT = 6 * 3600.0


def _timings_path(events_path: Path) -> Path:
    """`events.jsonl` → `events.timings.jsonl` (sibling). The console's `read_tool_calls`
    derives the same name, so the writer and the reader agree by convention, not by config."""
    return events_path.with_name(events_path.name.removesuffix(".jsonl") + ".timings.jsonl")


def stamp_timings(
    events_path: Path,
    exit_path: Path,
    *,
    poll_interval: float = _POLL_INTERVAL,
    idle_timeout: float = _IDLE_TIMEOUT,
) -> None:
    """Tail `events_path` line by line, writing one `{"observed_at": <epoch>}` row per line
    to its sibling `<stem>.timings.jsonl`. Returns when `exit_path` appears (the run
    finished — after draining any final complete line) or after `idle_timeout` seconds with
    no new complete line.

    Tracks a byte offset and only stamps *complete* lines (those ending in a newline), so a
    half-flushed trailing line — the normal state of a file a live agent is writing — is
    not stamped until it completes. Lines that arrive in the same poll share an
    `observed_at` to within the poll gap; calls are typically seconds apart, so each lands
    in its own poll and the per-call duration the console derives is honest.
    """
    timings = _timings_path(events_path)
    offset = 0
    last_progress = time.time()
    with timings.open("w", encoding="utf-8") as out:
        while True:
            if events_path.exists():
                with events_path.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                if chunk and b"\n" in chunk:
                    text = chunk.decode("utf-8", errors="replace")
                    complete, _partial = text.rsplit("\n", 1)
                    for _line in complete.split("\n"):
                        out.write(json.dumps({"observed_at": time.time()}) + "\n")
                    out.flush()
                    offset += len(complete.encode("utf-8")) + 1  # +1 for the split newline
                    last_progress = time.time()
            if exit_path.exists():
                break
            if time.time() - last_progress > idle_timeout:
                break
            time.sleep(poll_interval)


def spawn(events_path: Path, exit_path: Path) -> None:
    """Start the tailer as a detached host process that survives the tick that launched it.

    `start_new_session=True` is the same durability seam `exec_detached` uses (§4.2): pid 1
    adopts the holder, so the tailer outlives the factory process that started the run and
    keeps stamping until the attempt's `exit` appears. Stdio is detached so it never writes
    to the factory's own streams.
    """
    subprocess.Popen(  # noqa: S603 - argv is factory-controlled: sys.executable + path args
        [sys.executable, "-m", "factory.agent.timings", str(events_path), str(exit_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def _main() -> None:
    if len(sys.argv) != 3:
        sys.exit("usage: python -m factory.agent.timings <events.jsonl> <exit>")
    stamp_timings(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":  # pragma: no cover - exercised only as a detached child
    _main()
