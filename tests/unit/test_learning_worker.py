"""Exercise the host handoff with a real local Node process, never a model or vault."""

import json
from pathlib import Path

import pytest

from factory.learning import evidence
from factory.learning_worker import run
from tests.support import claude_stream

SESSION = claude_stream.SESSION


def _stream(path: Path, lines: list[str], *, tail: str = "") -> None:
    path.write_text("".join(f"{line}\n" for line in lines) + tail)


@pytest.mark.parametrize("truncated", [False, True])
def test_handoff_sends_the_raw_claude_stream_and_deduplicates(
    tmp_path: Path, truncated: bool
) -> None:
    script = tmp_path / "capture.mjs"
    script.write_text(
        """import fs from 'node:fs';
const p=JSON.parse(fs.readFileSync(0,'utf8'));
const transcript=fs.readFileSync(p.transcript_path,'utf8');
const expected = process.env.EXPECTED_EVIDENCE;
if(p.runtime!=='claude'||'project' in p||p.session_id!=='SESSION_PLACEHOLDER'||p.evidence!==expected||!transcript.includes('lesson')) process.exit(1);
fs.appendFileSync('calls','called\\n');
console.log(JSON.stringify({target:null,outcome:'wrote fixture',retryable:false}));
""".replace("SESSION_PLACEHOLDER", SESSION)
    )
    events = tmp_path / "events.jsonl"
    lines = claude_stream.success(None, text="lesson").lines
    # A killed writer leaves a torn tail; a stream with no result event is partial.
    _stream(events, lines[:-1] if truncated else lines, tail='{"partial":' if truncated else "")
    import os

    os.environ["EXPECTED_EVIDENCE"] = "retained-events-partial" if truncated else "retained-events"
    try:
        for _ in range(2):
            run(script, events, tmp_path, tmp_path / "isolated-vault")
    finally:
        del os.environ["EXPECTED_EVIDENCE"]
    result = json.loads(events.with_suffix(".learning.json").read_text())
    assert result["outcome"] == "wrote fixture"
    assert result["evidence"] == ("retained-events-partial" if truncated else "retained-events")
    assert result["session_id"] == SESSION
    assert (tmp_path / "calls").read_text() == "called\n"
    assert "partial" not in events.with_suffix(".learning.jsonl").read_text()


def test_the_session_comes_from_the_stream_not_from_a_guess(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    _stream(events, claude_stream.success(session=SESSION).lines)
    text, _, session, ended = evidence(events)
    assert session == SESSION
    assert ended is True
    assert text.count("\n") == len(claude_stream.success().lines)


def test_a_stream_without_a_session_is_not_handed_off(tmp_path: Path) -> None:
    # `session_in_use` is refused before the CLI writes anything: no init, no session.
    events = tmp_path / "events.jsonl"
    events.write_text("")
    script = tmp_path / "capture.mjs"
    script.write_text("throw new Error('must not execute')")
    run(script, events, tmp_path, tmp_path / "vault")
    result = json.loads(events.with_suffix(".learning.json").read_text())
    assert result["outcome"] == "unavailable:session-id"
    assert result["finished"] is True


def test_failed_worker_is_observable_and_recoverable(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    _stream(events, claude_stream.success().lines)
    script = tmp_path / "capture.mjs"
    script.write_text("process.exit(1)")
    run(script, events, tmp_path, tmp_path / "vault")
    receipt = events.with_suffix(".learning.json")
    assert json.loads(receipt.read_text())["outcome"] == "failed:JSONDecodeError"
    script.write_text(
        'console.log(JSON.stringify({outcome:"no learnings: fixture",retryable:false}))'
    )
    run(script, events, tmp_path, tmp_path / "vault")
    assert json.loads(receipt.read_text())["outcome"] == "no learnings: fixture"


def test_a_secret_in_the_stream_is_quarantined_before_any_node_runs(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    leaked = claude_stream.success(text="LINEAR_API_KEY=lin_api_fixturesensitivevalue000").lines
    _stream(events, leaked)
    script = tmp_path / "capture.mjs"
    script.write_text("throw new Error('must not execute')")
    run(script, events, tmp_path, tmp_path / "vault")
    result = json.loads(events.with_suffix(".learning.json").read_text())
    assert result["outcome"] == "quarantined:secret"
    assert not events.with_suffix(".learning.jsonl").exists()


def test_learning_atomic_outputs_replace_symlinks_without_writing_targets(tmp_path: Path) -> None:
    import os

    from factory.learning import _write, _write_text

    victim = tmp_path / "host-file"
    victim.write_text("preserve me")
    for name in ("events.learning.jsonl", "events.learning.json"):
        destination = tmp_path / name
        destination.symlink_to(victim)
        predictable = tmp_path / f"{name}.{os.getpid()}.tmp"
        predictable.symlink_to(victim)
        if name.endswith(".json"):
            _write(destination, {"outcome": "test"})
        else:
            _write_text(destination, "retained evidence")
        assert not destination.is_symlink()
        assert victim.read_text() == "preserve me"
