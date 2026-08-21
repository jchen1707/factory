# P1-2 — `sbx exec -d` does not detach, and the sandbox does not stay up

Measured 2026-08-21 on `sbx` v0.38.0, after the preflight went green for the first time
and the first implement attempt reached `codex exec` inside the VM. Two assumptions the
factory was built on are wrong. §4.2 depends on both.

**Resolved the same day.** The rule turned out to be *sessions*, not idleness, and a
session is held by an ordinary host process — so §4.2's guarantee is recoverable rather
than lost. Section 3 below is that measurement and the fix. Read it before Phase 4.

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

The first is closed and the third was the wrong framing. What follows is why.

## 3. The rule is sessions, with a 30-second grace — and that gives the guarantee back

The question left open above — session end, or an idle timer a session merely resets —
is answered by sandboxd's own log, which names the mechanism outright:

```console
$ grep auto-stop "~/Library/Application Support/com.docker.sandboxes/sandboxes/sandboxd/daemon.log"
01:16:56 "session disconnected, deferring auto-stop"  runtime=… delay=30000000000 gen=1
01:17:26 "auto-stop grace period expired, stopping runtime"  runtime=…
01:17:31 "auto-stopped runtime after last session disconnected"  runtime=…
```

**Session end, with a 30.0 s grace timer**, and the daemon says so in three lines. It is
not idleness: an in-VM process generates no session and does not defer the stop, which is
why §2's `setsid nohup` probe died. A *new* session cancels the pending stop — the daemon
logs `auto-stop complete, new session waiting` — so periodic traffic would work as a
keep-alive, but only as a race against a 30 s timer, and only while something is there to
send it.

There is no knob. `sbx create`, `sbx exec` and `sbx daemon` advertise no idle or
keep-alive flag on v0.38.0, and the binary carries no `SBX_*` environment variable that
changes the timer. That closes the first of the three options above.

**But the session is held by a host *process*, and that process does not have to be the
factory.** `sbx exec` is an ordinary CLI process; `start_new_session=True` makes it a
session leader, reparented to pid 1 and outside the caller's process group. Measured:

```console
$ python3 -c '…Popen(["sbx","exec",NAME,…], start_new_session=True); os._exit(0)'
$ # the spawning process is gone; 115 s later, long past the 30 s grace:
$ sbx ls | grep NAME
factory-build-python-harness-2   codex   running   …
$ sbx exec NAME /bin/sh -lc 'cat /tmp/probe3; date -u +%s'
1787289407
1787289408                       # the in-VM loop is one second stale — still working
$ kill -9 <holder>               # and now the other half of the mechanism:
01:16:56 session disconnected, deferring auto-stop … delay=30000000000
01:17:26 auto-stop grace period expired, stopping runtime
01:17:31 auto-stopped runtime after last session disconnected
```

So the factory host process is **out** of the run's TCB, and the machine is still in it.
That is the honest shape of §4.2 and it is what the plan now says. The fix is one keyword
argument in `exec_detached` plus the pid it leaves behind:

- `start_new_session=True` on the `Popen`, so the holder survives this process exiting
  *and* a Ctrl-C at the terminal that started the factory — a SIGINT to the process group
  would otherwise take it down, which is the failure mode the plain `Popen` still had.
- `sbx-exec.pid` in the attempt directory, because the process that asks "is this run
  still alive?" is a *later* `factory tick` that never had the handle. `poll` reads it and
  calls a run orphaned the moment its holder is gone, rather than waiting out 90 s of
  stale heartbeat for a verdict the 30 s timer has already decided.

**What Phase 4 still has to handle**, and what its daemon, `resume` and `recovery.py`
should now be written against:

| When | What happens |
| --- | --- |
| The host process dies | The run continues. The next tick reads `heartbeat`, `exit` and `events.jsonl` — §4.2 as written. |
| The holder dies | The sandbox stops 30 s later and the run dies with it. `poll` reports `ORPHANED`; the attempt has no `exit` file and needs a retry. |
| The machine reboots, the user logs out, Docker Desktop quits, or `sbx stop` runs | Same as the holder dying, and nothing at any layer can prevent it. |

A single reaped-pid caveat: a pid can be reused, so `pid_alive` is only ever used to
declare a run **dead**, never alive on its own.
