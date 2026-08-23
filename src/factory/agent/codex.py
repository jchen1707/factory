"""The Codex adapter — command construction and the JSONL parser.

Every fact in this module was measured, not read out of documentation. `docs/discovery/
codex-events.md` is the record and `tests/fixtures/` holds the transcripts the parser is
pinned to. Where the plan's reading of the v0.147.0 binary disagreed with the real
stream, the measurement wins and the difference is noted at the line it affects.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Sequence
from pathlib import Path

from factory.agent.base import AgentInvocation, Transcript, Usage

__all__ = ["CodexAdapter", "TranscriptError", "parse_events"]

#: `codex exec` blocks for ever on a non-TTY stdin that is not closed: it prints
#: "Reading additional input from stdin..." and waits for EOF, because it appends piped
#: stdin to the prompt as a `<stdin>` block. Reading the prompt *from* stdin closes it
#: at EOF, which is why the prompt is piped rather than passed as an argument — that
#: also keeps a long prompt out of the process table.
PROMPT_FROM_STDIN = "-"


class TranscriptError(Exception):
    """A transcript that cannot be trusted — truncated, or not JSONL at all."""


class CodexAdapter:
    """Builds the invocation and reads back what it produced."""

    def command(self, invocation: AgentInvocation) -> Sequence[str]:
        """The `codex exec` argv, without the shell wrapper.

        Three flags are absent on purpose. No `--ephemeral`: it discards the session
        file the resume path needs. No `--ignore-user-config` and no `--ignore-rules`:
        those are the Codex analogue of Claude Code's `--bare`, which switches off the
        enforcement layer this whole system is built on (§9.3).
        """
        if invocation.resume_session:
            argv = ["codex", "exec", "resume", invocation.resume_session]
        else:
            argv = ["codex", "exec"]
        argv += [
            "-m",
            invocation.model,
            "-c",
            f"model_reasoning_effort={invocation.effort}",
            # Reproduces `codex-vault-setting` exactly, and is passed on **every**
            # invocation including resumes: the in-sandbox name is
            # OBSIDIAN_VAULT_DIRECTORY, the host name is OBSIDIAN_VAULT_DIR, and this
            # is the translator between the two scopes (§9.2).
            "-c",
            f"shell_environment_policy.set.OBSIDIAN_VAULT_DIRECTORY={invocation.vault_directory}",
            "--json",
            "--output-schema",
            str(invocation.schema_path),
            "-o",
            str(invocation.output_path),
        ]
        # `-C` is `codex exec`'s alone. `codex exec resume` is a different clap command
        # and rejects it outright — `error: unexpected argument '-C'`, exit 2 in under a
        # second, which is how BAC-6 attempt 3 died on 2026-08-23, the first resume-by-id
        # ever run against the real binary. Nothing is lost by omitting it: the worktree
        # is already the process's working directory, because `exec_argv` passes
        # `sbx exec -w <worktree>` for every detached run.
        if not invocation.resume_session:
            argv += ["-C", invocation.workdir]
        argv += [
            # Mandatory, not prudent. P0-6 measured a protected-path edit succeeding at
            # exit 0, in silence, without it: at an untrusted path the enforcement layer
            # is not merely inert, it is invisible. Paired with vendor_sync's integrity
            # check in the same step, which is what makes the flag safe rather than
            # reckless.
            "--dangerously-bypass-hook-trust",
            PROMPT_FROM_STDIN,
        ]
        return argv

    def wrapper_script(self, invocation: AgentInvocation) -> str:
        """The detached `/bin/sh -lc` body — §4.2's filesystem protocol.

        `events.jsonl` and stderr are captured to **separate** files. §8.5's own snippet
        folds them together with `2>&1`, but P0-7 measured hook denials arriving as
        plain stderr lines: merged, they would put non-JSON into a JSONL parser and the
        evidence would be lost either way.
        """
        from factory.sandbox.base import detached_shell_script

        argv = " ".join(shlex.quote(part) for part in self.command(invocation))
        body = (
            f"{argv} < {shlex.quote(str(invocation.prompt_path))} "
            f"> {shlex.quote(str(invocation.events_path))} "
            f"2> {shlex.quote(str(invocation.stderr_path))}"
        )
        return detached_shell_script(
            heartbeat_path=invocation.heartbeat_path,
            exit_path=invocation.exit_path,
            body=body,
        )

    def read_transcript(self, events: Path, stderr: Path) -> Transcript:
        text = events.read_text(encoding="utf-8", errors="replace") if events.exists() else ""
        transcript = parse_events(text)
        if stderr.exists():
            denials = _hook_denials(stderr.read_text(encoding="utf-8", errors="replace"))
            transcript = Transcript(
                session_id=transcript.session_id,
                usage=transcript.usage,
                failed=transcript.failed,
                failure=transcript.failure,
                error_items=transcript.error_items,
                hook_denials=denials,
                commands=transcript.commands,
                files_touched=transcript.files_touched,
            )
        return transcript


def _hook_denials(stderr: str) -> tuple[str, ...]:
    """Lines where a hook refused a tool call.

    The refusal never reaches the JSON stream. It is written by
    `codex_core::tools::router` to stderr, so a factory that captured only the stream
    would show a clean run for a turn that was blocked.
    """
    return tuple(
        line.strip()
        for line in stderr.splitlines()
        if "blocked by" in line.lower() or "Command blocked" in line
    )


def _usage_from(payload: dict[str, object]) -> Usage:
    raw = payload.get("usage")
    if not isinstance(raw, dict):
        return Usage()
    return Usage(
        input_tokens=int(raw.get("input_tokens", 0) or 0),
        cached_input_tokens=int(raw.get("cached_input_tokens", 0) or 0),
        cache_write_input_tokens=int(raw.get("cache_write_input_tokens", 0) or 0),
        output_tokens=int(raw.get("output_tokens", 0) or 0),
        reasoning_output_tokens=int(raw.get("reasoning_output_tokens", 0) or 0),
    )


def _failure_message(payload: dict[str, object]) -> str:
    """`turn.failed`'s message is a JSON document encoded **as a string**.

    So it needs a second `json.loads` to reach `status` and `error.type`. A parser that
    reported the outer string would print an escaped blob where the reason should be.
    """
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            try:
                inner = json.loads(message)
            except json.JSONDecodeError:
                return message
            return json.dumps(inner, separators=(",", ":"))
        return json.dumps(error, separators=(",", ":"))
    if isinstance(error, str):
        return error
    return "turn.failed with no error payload"


def parse_events(text: str) -> Transcript:
    """Read a `codex exec --json` stream.

    A truncated line raises rather than returning a partial result: a transcript the
    parser had to guess at is not evidence, and the state machine advances on evidence.
    """
    session_id: str | None = None
    usage = Usage()
    failed = False
    failure: str | None = None
    error_items: list[str] = []
    commands: list[str] = []
    files: list[str] = []

    lines = [line for line in text.splitlines() if line.strip()]
    for number, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            if number == len(lines):
                raise TranscriptError(
                    f"events.jsonl ends with an unparseable line ({exc}); the run was "
                    "truncated and the transcript cannot be trusted"
                ) from exc
            raise TranscriptError(f"events.jsonl line {number} is not JSON: {exc}") from exc
        if not isinstance(event, dict):
            raise TranscriptError(f"events.jsonl line {number} is not an object")

        kind = event.get("type")
        if kind == "thread.started":
            # The only field on this event. `model_context_window` is **not** here,
            # despite the binary's symbol table: the context denominator comes from
            # models.toml instead (§18.5, corrected by P0-7).
            session_id = event.get("thread_id")
        elif kind == "turn.completed":
            usage = usage + _usage_from(event)
        elif kind == "turn.failed":
            failed = True
            failure = _failure_message(event)
        elif kind == "error":
            failed = True
            failure = failure or str(event.get("message", "error event"))
        elif kind in {"item.started", "item.completed", "item.updated"}:
            item = event.get("item") or {}
            item_type = item.get("item_type") or item.get("type")
            if item_type == "error" and kind == "item.completed":
                error_items.append(str(item.get("message", "")))
            elif item_type == "command_execution" and kind == "item.completed":
                commands.append(str(item.get("command", "")))
            elif item_type == "file_change":
                files += [
                    str(change.get("path", ""))
                    for change in item.get("changes", [])
                    if isinstance(change, dict)
                ]

    return Transcript(
        session_id=session_id,
        usage=usage,
        failed=failed,
        failure=failure,
        error_items=tuple(error_items),
        commands=tuple(commands),
        files_touched=tuple(dict.fromkeys(files)),
    )
