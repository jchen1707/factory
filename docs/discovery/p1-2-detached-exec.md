# P1-2 — `sbx exec -d` does not detach, and the sandbox does not stay up

Measured 2026-08-21 on `sbx` v0.38.0, after the preflight went green for the first time
and the first implement attempt reached `codex exec` inside the VM. Two assumptions the
factory was built on are wrong. §4.2 depends on both.

## 1. `-d` does not detach the call

`sbx exec --help` says "Run a command in the background". P0 verified the flag was
accepted; nobody timed it.

```console
$ time sbx exec -d factory-build-python-harness-2 /bin/sh -lc 'sleep 240'
sbx exec -d ... 'sleep 240'  0.14s user 0.10s system 0% cpu 4:01.72 total
```

**4 m 01 s for a 240 s sleep.** The call returns when the command finishes, not when it
starts. `SbxAdapter.exec_detached` gave it a 120 s timeout, so the first real implement
attempt died on `TimeoutExpired` with `events.jsonl` at 139 KB and the heartbeat 19 s
old — the agent was working normally and the host gave up on it.

## 2. The sandbox stops on its own, and that kills the work

The obvious reading of (1) is that the in-VM process is tethered to the host CLI process,
and the obvious fix is to detach *inside* the VM. Both are wrong.

```console
$ time sbx exec -d factory-build-python-harness-2 /bin/sh -lc \
    'setsid nohup sh -c "sleep 60; date -u +%s > /tmp/probe2" >/dev/null 2>&1 </dev/null & exit 0'
0.260 total                       # returns immediately, as hoped

$ # 82 seconds later, with nothing else touching the sandbox:
$ sbx exec factory-build-python-harness-2 /bin/sh -lc 'cat /tmp/probe2 || echo ABSENT'
Sandbox factory-build-python-harness-2 started successfully
ABSENT
```

`setsid nohup` does make the call return in 0.26 s. It does **not** keep the work alive.
The give-away is the line the second command printed: *started successfully* — the
sandbox had **stopped** in the interval. A stopping VM takes every process with it,
whatever its session or parent.

The same mechanism explains the agent's death in (1). Killing the timed-out `sbx exec`
ended the sandbox's last session; the sandbox stopped; `codex` went with it. Confirmed
after the fact — zero `codex` processes, a heartbeat frozen at 318 s, no `exit` file.

## What follows

**An open `sbx exec` session is what keeps the sandbox running.** So `exec_detached`
starts the process with `Popen`, waits for the wrapper's first heartbeat rather than for
the call, and then holds the handle for the run's lifetime. Never wait on it; never drop
it. Dropping it is not a leak, it is a kill.

**§4.2's "the host process can die at any moment and the next tick learns what happened
by looking at three files" does not hold on this version, and no code in `sandbox/` can
make it hold.** The filesystem protocol is still right — `heartbeat`, `exit` and the
atomic `mv` all work, and `poll()` reads them correctly. What is gone is the durability
guarantee underneath it. This matters most for **Phase 4**, whose daemon, `resume` and
`recovery.py` all assume a run survives the process that started it.

Restoring it needs one of:

- a way to keep the sandbox up without holding a session — an idle/keep-alive setting, if
  one exists; nothing in `sbx --help` advertises one, and this was not searched
  exhaustively;
- a supervisor *inside* the VM that the sandbox's own lifecycle keeps alive;
- or accepting that the factory host process is part of the run's TCB, and saying so in
  the plan rather than leaving §4.2 claiming otherwise.

That is a design decision, not a bug fix, and it is James's.

## Not measured

Whether the sandbox stops on session end specifically, or on an idle timer that the
session merely resets. The distinction matters for a keep-alive strategy: the first would
need any open session, the second only periodic traffic.
