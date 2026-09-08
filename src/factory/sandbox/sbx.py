"""The `sbx` implementation of the sandbox contract — §8.5.

Only commands verified against `sbx` v0.38.0 appear here. Two absences are deliberate:
`--branch` does not exist on `create` or `run` in this version (the factory makes its
own worktree), and `sbx template save` is forbidden outright because it captures the
entire filesystem including secrets.

`sbx reset`, `sbx logout` and any operation on a sandbox this adapter did not create
are refused by `policy.assert_factory_sandbox`, called at the adapter rather than at
the caller. A guard the caller can forget to call is not a guard.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from factory.agent import runtime_identity
from factory.policy import assert_factory_sandbox, assert_no_skip_verify, capability_secrets
from factory.sandbox.base import (
    Completed,
    RunHandle,
    RunResult,
    RunStatus,
    SandboxSpec,
)

__all__ = ["SbxAdapter", "SbxError", "create_argv", "exec_argv", "holder_pid", "pid_alive"]

#: How long after the last heartbeat a run with no `exit` file is presumed orphaned.
#: The wrapper beats every 20 s, so three missed beats plus slack.
ORPHAN_AFTER_SECONDS = 90

#: Where `sbx exec`'s own stderr lands. Not `stderr.log` — that one is the agent's, and
#: mixing the transport's complaints into the agent's output is how evidence gets lost.
SBX_EXEC_STDERR = "sbx-exec.stderr"

#: The pid of the `sbx exec` process holding the sandbox's session open. Written to the
#: attempt directory because the process that has to ask "is this run still alive?" is
#: usually **not** the process that started it — that is the whole point of §4.2, and
#: `_detached` only knows about runs this object started.
SBX_EXEC_PID = "sbx-exec.pid"

#: The port `sbx`'s in-sandbox git daemon listens on. Its *host* port is reassigned on
#: every start, so it is looked up rather than remembered; this is the stable half.
GIT_DAEMON_PORT = 9418


def _certification_configuration(info: dict[str, Any], *, clone: bool) -> dict[str, Any]:
    """Keep exposure identity while allowing sbx's internal clone Git forwarding to move.

    Docker reassigns this loopback host port whenever the same clone VM starts. It
    is a transport locator, like the URL resolved by git_daemon_url, not a sandbox
    capability. Preserve every endpoint, interface, protocol and other port mapping.
    """
    volatile = {"state", "sessions", "uptime", "daemon_uptime"}
    configuration = {k: v for k, v in info.items() if k not in volatile}
    ports = configuration.get("ports")
    if clone and isinstance(ports, list):
        normalized = []
        for port in ports:
            match = (
                re.fullmatch(rf"127\.0\.0\.1:([0-9]{{1,5}})->{GIT_DAEMON_PORT}/tcp", port)
                if isinstance(port, str)
                else None
            )
            normalized.append(
                f"127.0.0.1:<dynamic-clone-git>->{GIT_DAEMON_PORT}/tcp"
                if match and 1 <= int(match[1]) <= 65535
                else port
            )
        configuration["ports"] = normalized
    return configuration


#: The prefix `sbx secret set-custom` gives its substitution placeholders. Not a
#: credential: the real value stays on the host and the proxy swaps this string into the
#: outbound request. Named for the prefix rather than for what it stands in for, because
#: ruff S105 reads any constant whose name says "secret" or "token" as a hardcoded one.
_PLACEHOLDER_PREFIX = "sbx-cs-"

#: How long `exec_detached` waits for the wrapper's first heartbeat before calling the
#: start a failure. Generous because it covers `sbx exec` starting a stopped sandbox,
#: and cheap because the common case returns as soon as the file appears.
START_TIMEOUT_SECONDS = 120


class SbxError(Exception):
    """An `sbx` command that failed, with its output attached."""


def assert_primary_workspace_writable(spec: SandboxSpec) -> None:
    """`sbx create` refuses a read-only primary workspace, so refuse it here instead.

    Measured: creating the reviewer sandbox with the worktree `:ro` first returned
    `ERROR: primary workspace must be read/write (remove ':ro' or ':readonly')`, and
    `sbx create codex --help` says `:ro` applies to the *additional* workspaces. A
    read-only review sandbox is still exactly buildable — the writable scratch goes
    first and the code under review comes in beside it — so this is an ordering rule,
    not a lost guarantee. It lives here because `create_argv` is the one place a
    creation-time decision is spelled, and a spec that cannot be created should say so
    on the host rather than 40 seconds into a `sbx create`.
    """
    if spec.workspaces and spec.workspaces[0].readonly:
        raise SbxError(
            f"{spec.name}: the primary workspace {spec.workspaces[0].path} is `:ro`, and "
            "`sbx create` requires it to be read/write. Put a writable workspace first and "
            "mount the read-only one after it."
        )


def create_argv(spec: SandboxSpec) -> list[str]:
    """The exact `sbx create` command line for a spec. Golden-tested.

    Order matters only for readability; correctness is that every creation-time
    decision — template, kits, static MCP, deny rules, workspaces — appears here and
    nowhere else, because none of them can be changed afterwards.
    """
    assert_factory_sandbox(spec.name)
    assert_primary_workspace_writable(spec)
    argv = ["sbx", "create", "codex", "--name", spec.name]
    if spec.template:
        argv += ["-t", spec.template]
    for kit in spec.kits:
        argv += ["--kit", kit]
    if spec.static_mcp:
        argv += ["--static-mcp", ",".join(spec.static_mcp)]
    for host in spec.deny_network:
        argv += ["--deny-network", host]
    if spec.memory:
        argv += ["-m", spec.memory]
    if spec.cpus:
        argv += ["--cpus", str(spec.cpus)]
    if spec.clone:
        argv.append("--clone")
    if not spec.share_skills:
        argv.append("--no-share-skills")
    argv += [workspace.as_argument() for workspace in spec.workspaces]
    return argv


def exec_argv(
    name: str,
    argv: Sequence[str],
    *,
    workdir: str | None = None,
    env: Mapping[str, str] | None = None,
    detach: bool = False,
) -> list[str]:
    """The exact `sbx exec` command line. Golden-tested."""
    assert_factory_sandbox(name)
    if env:
        assert_no_skip_verify(env)
    out = ["sbx", "exec"]
    if detach:
        out.append("-d")
    if workdir:
        out += ["-w", workdir]
    for key, value in sorted((env or {}).items()):
        out += ["-e", f"{key}={value}"]
    out += [name, *argv]
    return out


def holder_pid(attempt_dir: Path) -> int | None:
    """The pid of the `sbx exec` process holding this attempt's session open."""
    try:
        return int((attempt_dir / SBX_EXEC_PID).read_text().strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid: int) -> bool:
    """Whether a host process is still running.

    A pid is a weak identifier — the number can be reused once its process is reaped —
    so this is only ever used to declare a run **dead**, never alive on its own. A
    false "still running" costs one more poll; every other signal (`exit`, the
    heartbeat, the sandbox's own state) is checked alongside it. `PermissionError`
    means the pid exists and belongs to somebody else, which is still a live pid.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class SbxAdapter:
    """Create-or-attach, detached execution, and polling by filesystem."""

    #: Live `sbx exec -d` processes started by *this* object, by sandbox. Held open on
    #: purpose: the session this process opened is what keeps the sandbox up, so
    #: dropping the handle would kill the run it started. It is not the source of truth
    #: for liveness — `SBX_EXEC_PID` is, because it outlives this object.
    _detached: dict[str, subprocess.Popen[str]]

    def __init__(self, *, timeout: int = 900) -> None:
        self.timeout = timeout
        self._detached = {}

    # -- plumbing -----------------------------------------------------------------

    def _run(
        self, argv: Sequence[str], *, timeout: int | None = None, stdin: str | None = None
    ) -> Completed:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout or self.timeout,
            input=stdin,
        )
        return Completed(tuple(argv), proc.returncode, proc.stdout, proc.stderr)

    # -- lifecycle ----------------------------------------------------------------

    def generation(self, name: str) -> str:
        """Fresh creation UUID from `sbx ls`, never the reusable sandbox name.

        `inspect` omits this field on v0.38.0. Missing or ambiguous listing data
        cannot authorize reuse of certification evidence.
        """
        assert_factory_sandbox(name)
        result = self._run(["sbx", "ls", "--json"], timeout=60)
        try:
            if not result.ok:
                raise ValueError
            rows = json.loads(result.stdout)["sandboxes"]
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError
            matches = [row for row in rows if row.get("name") == name]
            if len(matches) != 1:
                raise ValueError
            value = matches[0]["id"]
            if not isinstance(value, str) or str(UUID(value)) != value:
                raise ValueError
            return value
        except (ValueError, KeyError, TypeError):
            raise SbxError(f"sandbox generation unavailable: {name}") from None

    def observe_runtime(
        self,
        name: str,
        *,
        binary: str,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Fresh native binary/image/generation evidence, without a model turn.

        This is not a certificate or the full spec/mount/hook preflight. The
        certifier must supply the same explicit binary and environment at launch.
        Capability credentials refuse even metadata execution on this path.
        """
        assert_factory_sandbox(name)
        if not Path(binary).is_absolute():
            raise SbxError("runtime binary path must be absolute")
        generation = self.generation(name)
        info = self.inspect(name)
        image = info.get("image_digest")
        if not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise SbxError(f"sandbox image identity unavailable: {name}")
        if not isinstance(info.get("secrets"), list) or capability_secrets(info["secrets"]):
            raise SbxError(f"sandbox capability preflight failed: {name}")
        result = self.exec_sync(
            name,
            # Candidate cwd/PYTHONPATH/site imports must not replace probe modules.
            [
                "/usr/bin/python3",
                "-I",
                "-S",
                "-c",
                Path(runtime_identity.__file__).read_text(),
                binary,
            ],
            workdir=workdir,
            env=env,
            timeout=60,
        )
        try:
            observed = json.loads(result.stdout)
            if (
                not result.ok
                or not isinstance(observed, dict)
                or set(observed) != {"runtime_path", "runtime_version", "runtime_sha256"}
            ):
                raise ValueError
            if not all(isinstance(value, str) for value in observed.values()):
                raise ValueError
            if (
                not Path(observed["runtime_path"]).is_absolute()
                or not re.fullmatch(r"[0-9a-f]{64}", observed["runtime_sha256"])
                or not re.fullmatch(r"codex-cli [0-9][A-Za-z0-9.+-]*", observed["runtime_version"])
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise SbxError(f"native runtime identity unavailable: {name}") from None
        current = self.inspect(name)
        if (
            self.generation(name) != generation
            or current.get("image_digest") != image
            or current.get("secrets") != info["secrets"]
        ):
            raise SbxError(f"sandbox changed during runtime observation: {name}")
        return {"generation": generation, "image_digest": image, **observed}

    def observe_certification(
        self,
        spec: SandboxSpec,
        *,
        binary: str,
        workdir: str,
        env: Mapping[str, str],
        hook_files: Mapping[str, str],
        credential_names: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Fresh complete observation; metadata checks never create or modify a sandbox."""
        from factory.certification_fingerprint import digest
        from factory.sandbox import certification_observer

        before = self.observe_runtime(spec.name, binary=binary, workdir=workdir, env=env)
        info = self.inspect(spec.name)
        if info.get("kits") != list(spec.kits) or (
            spec.template and info.get("image") != spec.template
        ):
            raise SbxError("sandbox template or kits differ from requested specification")
        request = {
            "binary": binary,
            "workspaces": [{"path": str(w.path), "readonly": w.readonly} for w in spec.workspaces],
            "clone": spec.clone,
            "env_names": sorted(env),
            "env_sha256": digest(dict(env)),
            "hook_files": dict(hook_files),
            "credential_names": list(credential_names),
        }
        result = self.exec_sync(
            spec.name,
            [
                "/usr/bin/python3",
                "-I",
                "-S",
                "-c",
                Path(certification_observer.__file__).read_text(),
                json.dumps(request),
            ],
            workdir=workdir,
            env=env,
            timeout=60,
        )
        try:
            actual = json.loads(result.stdout)
            if (
                not result.ok
                or not isinstance(actual, dict)
                or set(actual)
                != {
                    "mounts",
                    "environment_sha256",
                    "configurations",
                    "hooks",
                    "credential_names",
                    "launcher_sha256",
                    "code_host_sha256",
                    "native_mount",
                }
            ):
                raise ValueError("sandbox observation unavailable")
        except (ValueError, TypeError) as exc:
            raise SbxError("sandbox certification observation failed") from exc
        # Stable inspect properties only: session counts, uptime and state are liveness,
        # not identity. Include all remaining properties so unknown configuration drifts.
        configuration = _certification_configuration(info, clone=spec.clone)
        current = _certification_configuration(self.inspect(spec.name), clone=spec.clone)
        after = self.observe_runtime(spec.name, binary=binary, workdir=workdir, env=env)
        if before != after or current != configuration:
            changed = sorted(
                k
                for k in set(configuration) | set(current)
                if configuration.get(k) != current.get(k)
            )
            changed += sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
            raise SbxError(
                "sandbox changed during certification observation: " + ", ".join(changed)
            )
        return {**after, "actual": {**actual, "configuration": configuration}}

    def exists(self, name: str) -> bool:
        """`sbx inspect` is non-zero when the sandbox is absent.

        Undocumented in `sbx --help` and present in the binary, which P0-5 confirmed
        by running it: it returns exactly the fields `inspect()` needs.
        """
        return self._run(["sbx", "inspect", name], timeout=60).ok

    def inspect(self, name: str) -> dict[str, Any]:
        result = self._run(["sbx", "inspect", name, "--json"], timeout=60)
        if not result.ok:
            raise SbxError(f"sbx inspect {name} failed: {result.stderr.strip()}")
        parsed: dict[str, Any] = json.loads(result.stdout)
        return parsed

    def ensure(self, spec: SandboxSpec) -> None:
        """Create only when absent; never re-create to change a spec.

        `csbx` passes its template and static MCP set on the first run only, and this
        does the same — but a spec that no longer matches an existing sandbox is an
        error here rather than a shrug, because those three fields are fixed at
        creation and pretending otherwise would be the silent mismatch §9.1 warns of.
        """
        assert_factory_sandbox(spec.name)
        if self.exists(spec.name):
            self._assert_spec_matches(spec)
            return
        result = self._run(create_argv(spec))
        if not result.ok:
            raise SbxError(f"sbx create for {spec.name} failed:\n{result.stdout}\n{result.stderr}")

    def _assert_spec_matches(self, spec: SandboxSpec) -> None:
        info = self.inspect(spec.name)
        workspace = str(info.get("workspace", ""))
        wanted = [str(w.path) for w in spec.workspaces]
        if wanted and not any(w in workspace for w in wanted):
            raise SbxError(
                f"sandbox {spec.name} exists with workspace {workspace!r}, which does "
                f"not include {wanted}. The workspace set is fixed at creation: use a "
                "new sandbox name rather than expecting this one to change."
            )
        # §8.7: no capability-granting credential inside a factory sandbox. Read from
        # the sandbox rather than assumed from how it was made — a sandbox created
        # while a secret was still global keeps it, because `sbx` fixes the secret set
        # at creation. Recreating is the only fix, which is what the message says.
        offending = capability_secrets(
            info.get("secrets") or [], declared=spec.allowed_custom_secrets
        )
        if offending:
            raise SbxError(
                f"sandbox {spec.name} has injected secrets {offending}; factory "
                "sandboxes carry none (§8.7). Remove it and let the factory recreate "
                "it: the secret set is fixed at creation and cannot be narrowed later."
            )

    def git_daemon_url(self, name: str) -> str | None:
        """Where a `--clone` sandbox serves the agent's commits, or None if it is not up.

        `sbx create --clone` prints this URL once and registers it on the host as a
        `sandbox-<name>` remote — and that remote is a trap. Measured 2026-08-22: the port
        is reassigned by Docker on every start, the remote is withdrawn when the sandbox
        stops, and starting it again does **not** restore the remote. A run that fetches by
        remote name at the `reviewing` entry is therefore fetching from a name that is
        either absent or pointing at a port nobody is listening on.

        `sbx ls --json` reports the mapping structurally, which is the only durable source:
        the entry whose `sandbox_port` is the git-daemon port carries the live host port.
        `sbx inspect --json` does not report it at all.
        """
        assert_factory_sandbox(name)
        result = self._run(["sbx", "ls", "--json"], timeout=60)
        if not result.ok:
            return None
        try:
            listing = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        for sandbox in listing.get("sandboxes", []):
            if sandbox.get("name") != name:
                continue
            for port in sandbox.get("ports") or []:
                if int(port.get("sandbox_port", 0)) != GIT_DAEMON_PORT:
                    continue
                host = port.get("host_ip") or "127.0.0.1"
                return f"git://{host}:{int(port['host_port'])}"
        return None

    def custom_secret_placeholder(self, name: str, env: str) -> str | None:
        """The `sbx-cs-…` placeholder for `env` in `name`'s scope, or None if there is none.

        The one host-side read the in-VM delivery path needs. The placeholder is not a
        secret — off the proxy, or aimed at any host but the one its rule names, it is a
        meaningless string — so it is fetched here and handed to the sandbox, rather than
        relying on `set-custom --env` to export it. That flag is EXPERIMENTAL and measured
        2026-08-31 it never reaches an `sbx exec` session: absent before *and* after a
        stop/restart, while `sbx inspect` went on listing the secret. A delivery path
        depending on it would fail at the last step of a run that had already been paid for.

        Parsed out of the table `sbx secret ls` prints, because there is no `--json` on
        `secret ls` (v0.38.0: `unknown flag`). Scoped with `--sandbox` deliberately — the
        same command with no scope prints global entries too, and a global custom secret
        is a rule that fires for `codex-*` sandboxes as well, which is not a thing this
        path should be able to reach for by accident.

        Matched on the placeholder's own `sbx-cs-` prefix rather than on a column index:
        the output is whitespace-aligned with values that contain no spaces, so a column
        count is the fragile way to read it and the prefix is the stable one.
        """
        assert_factory_sandbox(name)
        result = self._run(["sbx", "secret", "ls", "--sandbox", name], timeout=60)
        if not result.ok:
            return None
        for line in result.stdout.splitlines():
            fields = line.split()
            if name not in fields or env not in fields:
                continue
            for field_value in fields:
                if field_value.startswith(_PLACEHOLDER_PREFIX):
                    return field_value
        return None

    def stop(self, name: str) -> None:
        assert_factory_sandbox(name)
        result = self._run(["sbx", "stop", name], timeout=120)
        if not result.ok:
            raise SbxError(f"sbx stop {name} failed (exit {result.returncode})")

    def remove(self, name: str) -> None:
        assert_factory_sandbox(name)
        if self.inspect(name).get("state") != "stopped":
            raise SbxError(f"sbx remove {name} refused: sandbox must be stopped")
        result = self._run(["sbx", "rm", "--force", name], timeout=120)
        if not result.ok:
            raise SbxError(f"sbx remove {name} failed (exit {result.returncode})")

    # -- execution ----------------------------------------------------------------

    def exec_sync(
        self,
        name: str,
        argv: Sequence[str],
        *,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int | None = None,
        stdin: str | None = None,
    ) -> Completed:
        return self._run(
            exec_argv(name, argv, workdir=workdir, env=env), timeout=timeout, stdin=stdin
        )

    def exec_detached(
        self,
        handle: RunHandle,
        script: str,
        env: Mapping[str, str],
        *,
        stdout_path: Path | None = None,
    ) -> None:
        """The single most important command in the factory (§4.2).

        Long work runs inside the sandbox and reports through the filesystem: the caller
        learns what happened by looking at three files. The script writes its exit code
        to a temp file and `mv`s it, so `exit` appearing is atomic.

        §4.2 also says the host process may die at any moment. **On `sbx` v0.38.0 it may
        not**, and that is a property of the sandbox rather than of this code — see the
        comment below. Nothing here can restore it; `docs/discovery/p1-2-detached-exec.md`
        records what would.
        """
        assert_factory_sandbox(handle.sandbox)
        assert_no_skip_verify(env)
        argv = exec_argv(
            handle.sandbox,
            ["/bin/sh", "-lc", script],
            workdir=handle.workdir,
            env=env,
            detach=True,
        )
        # Three measured facts about `sbx exec -d` on v0.38.0, none of them what the
        # factory was built to assume. P0 verified the flag was *accepted*, not what it
        # did.
        #
        # 1. `-d` does not detach the call. `sbx exec -d <sandbox> /bin/sh -lc
        #    'sleep 240'` returned after 4 m 01 s — exactly the command's duration. The
        #    first implement attempt to clear the preflight therefore died on
        #    `TimeoutExpired` at 120 s, with `events.jsonl` at 139 KB and the heartbeat
        #    19 s old.
        # 2. **The sandbox stops when its last session ends, and a stopping VM kills
        #    everything inside it.** That, not process parentage, is why the agent died
        #    when the timed-out `sbx exec` was killed. A `setsid nohup` wrapper *inside*
        #    the VM was measured against this and made no difference — the probe
        #    returned in 0.26 s and its work was gone 82 s later, along with the sandbox.
        # 3. The rule is **sessions, not idleness**, and it has a 30-second grace period.
        #    sandboxd logs it: `session disconnected, deferring auto-stop … delay:
        #    30000000000` → `auto-stop grace period expired, stopping runtime` →
        #    `auto-stopped runtime after last session disconnected`. No traffic keeps a
        #    sessionless sandbox up and no flag changes the timer, so the only keep-alive
        #    that exists is an open session.
        #
        # `start_new_session=True` is what turns that into §4.2's guarantee rather than
        # away from it. The session belongs to this `sbx exec` **process**, which is an
        # ordinary host process and not this one: made a session leader it is reparented
        # to pid 1, and it survives both this process exiting and a SIGINT or SIGHUP
        # delivered to this process's group — a Ctrl-C in the terminal that started the
        # factory. Measured on 2026-08-21: parent `os._exit(0)` immediately after spawn,
        # and 115 s later — long past the 30 s grace — the sandbox was still `running`
        # and the in-VM loop's output file was one second old. Kill this process's
        # holder, however, and the sandbox stops 30 s later with the run inside it, which
        # is what `poll` reads `SBX_EXEC_PID` to notice.
        #
        # So the host *process* is out of the run's TCB and the *machine* is still in it:
        # a reboot, a logout, `sbx stop` or Docker Desktop quitting takes the run with
        # it. §4.2 says so in those words.
        #
        # stderr goes to a file rather than a pipe nobody drains: this process outlives
        # the call by design, and a full 64 KB pipe buffer would block the very exec it
        # is meant to be diagnosing.
        handle.attempt_dir.mkdir(parents=True, exist_ok=True)
        stderr_path = handle.attempt_dir / SBX_EXEC_STDERR
        from contextlib import ExitStack

        with ExitStack() as files:
            stderr_file = files.enter_context(stderr_path.open("w", encoding="utf-8"))
            stdout_file = (
                files.enter_context(stdout_path.open("x", encoding="utf-8"))
                if stdout_path is not None
                else subprocess.DEVNULL
            )
            process = subprocess.Popen(
                list(argv),
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                start_new_session=True,
            )
        self._detached[handle.sandbox] = process
        (handle.attempt_dir / SBX_EXEC_PID).write_text(f"{process.pid}\n", encoding="utf-8")

        # "Started" is the heartbeat appearing, not the call returning — the wrapper
        # writes its first beat before `codex exec` is reached. A process that has
        # already exited without one never started, and the file says why.
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        heartbeat = handle.attempt_dir / "heartbeat"
        while time.monotonic() < deadline:
            if heartbeat.exists():
                return
            if process.poll() is not None:
                detail = stderr_path.read_text(encoding="utf-8", errors="replace").strip()
                raise SbxError(
                    f"detached exec in {handle.sandbox} exited {process.returncode} "
                    f"before writing a heartbeat:\n{detail}"
                )
            time.sleep(0.5)
        raise SbxError(
            f"detached exec in {handle.sandbox} wrote no heartbeat within "
            f"{START_TIMEOUT_SECONDS}s; the wrapper never reached its first beat"
        )

    def kill_agent(self, name: str, proc: str = "codex") -> None:
        """Stop a hung run so the attempt gets a real terminal record.

        A timeout is never silently a success: the wrapper's `exit` file still lands
        after the kill, which is what turns "we gave up" into a recorded exit code.

        **The match is on the process name, never the command line.** This read
        `pkill -f "codex exec"` until 2026-08-22, and that pattern selects three
        processes inside the sandbox, not one — measured on
        `factory-build-python-harness-2`:

            305 /bin/sh -lc set -u ( while :; do date -u +%s > …/heartbeat; …
            309 /bin/sh -lc set -u ( while :; do date -u +%s > …/heartbeat; …
            310 …/bodyproc …

        The wrapper's command line *contains* the agent's argv, so `-f` kills the
        wrapper and its heartbeat subshell along with the agent — and the wrapper is
        the process that writes `exit`, after the body returns. The promise in the
        paragraph above was therefore false in every case it was made: BAC-6 run
        `3f03240cd3bc4bd0` was suspended from `implementing`, waited out
        `KILL_GRACE_SECONDS`, and recorded `exit_code = NULL`.

        `-x codex` selects the agent alone (`command()` puts `codex` at argv[0]); the
        wrapper, named `sh`, survives to write `143`. Measured both ways on the same
        sandbox with the real envelope.

        ``proc`` is the in-VM process name to kill, because not every step runs
        ``codex``. The ``verifying`` step runs ``node gate_report.mjs`` (Phase 5
        defect 3): the gate report is a node process, so ``pkill -x codex`` matched
        nothing, the hung report was never signalled, no ``exit`` landed, and the run
        went ``resumable`` with no terminal record. The caller names the process; the
        default stays ``codex`` for implement/review/reap/recovery.
        """
        assert_factory_sandbox(name)
        self._run(["sbx", "exec", name, "pkill", "-x", proc], timeout=60)

    def kill_group(self, name: str, pgid: int) -> None:
        """Signal one run's body by its in-VM pid. The concurrency-safe kill.

        `kill_agent` above matches on a process *name*, and the build sandbox is named
        once per project (`steps/sandbox.py`), so `pkill -x codex` in a VM holding two
        concurrent runs signals both. The second run's wrapper then writes `exit 143` and
        the factory reads it as that run's own timeout — a wrong terminal record for a
        run that was doing nothing wrong, and nothing in the artifacts says otherwise.

        A process group cannot make that mistake. The wrapper publishes its pgid
        (`detached_shell_script`) and removes the file once the body is reaped, so a
        readable pgid always names a live group belonging to this attempt. It is a group
        rather than a pid because a body may be compound — `steps/review.py` runs one
        codex block per axis — and because a group also reaches whatever the body spawned.

        `-TERM` is what `pkill` sent by default, kept so the wrapper's exit-code path is
        unchanged: the body dies, the wrapper survives, `wait` returns 143 and `exit`
        lands. The negative pid is the POSIX spelling for "the whole group".
        """
        assert_factory_sandbox(name)
        self._run(["sbx", "exec", name, "kill", "-TERM", f"-{pgid}"], timeout=60)

    # -- observation --------------------------------------------------------------

    def poll(self, handle: RunHandle) -> RunStatus:
        """Filesystem first, sandbox state second.

        `exit` present is terminal regardless of what the VM is doing. A run is an
        orphan when its session holder is gone or its sandbox has stopped — the
        heartbeat is a progress signal, not a death signal. A stale heartbeat with a
        *live* holder and a running sandbox is a paused run: the host slept and the
        sandbox suspended with it, so the heartbeat subshell stopped advancing while
        nothing died. Reaping that as an orphan (Phase 5 defect 1) lost healthy runs
        on every wake. The §5.1 state timeout recovers a run that is alive but making
        no progress; `poll` recovers one whose holder or sandbox is gone.
        """
        attempt_dir = handle.attempt_dir
        if (attempt_dir / handle.exit_name).exists():
            return RunStatus.EXITED
        # The session holder is gone, so the sandbox has either stopped already or will
        # in under 30 s, and nothing inside it survives that. Saying so now rather than
        # after `ORPHAN_AFTER_SECONDS` of stale heartbeat is the difference between the
        # next tick recovering the run and it waiting out a minute and a half for a
        # verdict that is already decided.
        holder = holder_pid(attempt_dir)
        holder_alive = holder is not None and pid_alive(holder)
        if holder is not None and not holder_alive:
            return RunStatus.ORPHANED
        beat = attempt_dir / "heartbeat"
        try:
            age = time.time() - int(beat.read_text().strip())
        except (OSError, ValueError):
            age = None
        if age is not None and age < ORPHAN_AFTER_SECONDS:
            return RunStatus.RUNNING
        if not self.exists(handle.sandbox):
            return RunStatus.ORPHANED
        state = str(self.inspect(handle.sandbox).get("state", ""))
        if state != "running":
            return RunStatus.ORPHANED
        # The sandbox is running. A stale heartbeat here with a live holder is a paused
        # run, not an orphan — the host slept and both the holder and the sandbox resume
        # on wake. Only a run with no recorded holder falls back to the heartbeat: that
        # is the pre-holder-pid path, where a stale beat is the only death signal.
        if holder_alive:
            return RunStatus.RUNNING
        return RunStatus.RUNNING if age is None else RunStatus.ORPHANED

    def collect(self, handle: RunHandle) -> RunResult:
        attempt_dir = handle.attempt_dir
        raw = (attempt_dir / "exit").read_text().strip()
        return RunResult(
            exit_code=int(raw),
            events_path=attempt_dir / "events.jsonl",
            stderr_path=attempt_dir / "stderr.log",
            last_message_path=attempt_dir / "last-message.json",
        )


def sbx_available() -> tuple[bool, str]:
    """For `factory doctor`. Reports the daemon version rather than just presence."""
    try:
        proc = subprocess.run(
            ["sbx", "ls"], capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"sbx unusable: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()
    return True, proc.stdout.strip()


def list_sandboxes() -> list[str]:
    proc = subprocess.run(["sbx", "ls"], capture_output=True, text=True, check=False, timeout=60)
    names: list[str] = []
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split()
        if parts:
            names.append(parts[0])
    return names


def worktree_inside(workspace: Path, worktree: Path) -> bool:
    """A worktree must sit inside a mounted workspace, or the VM cannot see it."""
    try:
        worktree.relative_to(workspace)
    except ValueError:
        return False
    return True
