"""Exercise the host handoff with a real local Node process, never a model or vault."""

import json
from pathlib import Path

import pytest

from factory.learning_worker import run


@pytest.mark.parametrize("truncated", [False, True])
def test_handoff_preserves_session_and_deduplicates(tmp_path: Path, truncated: bool) -> None:
    script = tmp_path / "capture.mjs"
    script.write_text("""import fs from 'node:fs';
const p=JSON.parse(fs.readFileSync(0,'utf8'));
const transcript=fs.readFileSync(p.transcript_path,'utf8');
if(p.runtime!=='codex'||'project' in p||p.session_id!=='stable-session'||!transcript.includes('lesson')) process.exit(1);
fs.appendFileSync('calls','called\\n');
console.log(JSON.stringify({target:null,outcome:'wrote fixture',retryable:false}));
""")
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "stable-session"}),
                json.dumps(
                    {"type": "item.completed", "item": {"type": "agent_message", "text": "lesson"}}
                ),
            ]
        )
        + ('\n{"partial":' if truncated else "\n")
    )
    for _ in range(2):
        run(script, events, tmp_path, tmp_path / "isolated-vault")
    result = json.loads(events.with_suffix(".learning.json").read_text())
    assert result["outcome"] == "wrote fixture"
    assert result["evidence"] == "retained-events-partial"
    assert (tmp_path / "calls").read_text() == "called\n"
    assert "partial" not in events.with_suffix(".learning.jsonl").read_text()


def test_failed_worker_is_observable_and_recoverable(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"thread.started","thread_id":"session"}\n')
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


def test_native_transcript_replaces_partial_snapshot_for_same_session(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"thread.started","thread_id":"session"}\n')
    native = events.with_suffix(".native.jsonl")
    native.write_text(
        '{"type":"session_meta","payload":{"id":"session"}}\n'
        '{"type":"response_item","payload":{"type":"message","role":"user",'
        '"content":[{"type":"input_text","text":"lesson only in native user turn"}]}}\n'
    )
    script = tmp_path / "capture.mjs"
    script.write_text("""import fs from 'node:fs';
const p=JSON.parse(fs.readFileSync(0,'utf8'));
const transcript=fs.readFileSync(p.transcript_path,'utf8');
if(p.evidence!=='retained-native-transcript'||!transcript.includes('native user turn')) process.exit(1);
console.log(JSON.stringify({outcome:'wrote native fixture',retryable:false}));
""")
    run(script, events, tmp_path, tmp_path / "vault")
    receipt = json.loads(events.with_suffix(".learning.json").read_text())
    assert receipt["outcome"] == "wrote native fixture"
    assert receipt["evidence"] == "retained-native-transcript"


def test_unrelated_native_transcript_cannot_replace_partial_evidence(tmp_path: Path) -> None:
    from factory.learning import evidence

    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"thread.started","thread_id":"session"}\n')
    events.with_suffix(".native.jsonl").write_text(
        '{"type":"session_meta","payload":{"id":"different-session"}}\n'
    )
    text, _, session = evidence(events)
    assert session == "session"
    assert "different-session" not in text


def test_quarantined_native_is_never_replayed(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"thread.started","thread_id":"session"}\n')
    events.with_suffix(".native.jsonl").write_text(
        '{"type":"session_meta","payload":{"id":"session"}}\n'
        '{"type":"response_item","payload":{"text":"CUSTOM_AUTH=fixture-sensitive-value"}}\n'
    )
    events.with_suffix(".native.json").write_text(
        '{"outcome":"quarantined:secret","kind":"assignment to CUSTOM_AUTH"}'
    )
    script = tmp_path / "capture.mjs"
    script.write_text("throw new Error('must not execute')")
    run(script, events, tmp_path, tmp_path / "vault")
    result = json.loads(events.with_suffix(".learning.json").read_text())
    assert result["outcome"] == "quarantined:secret"
    assert not events.with_suffix(".learning.jsonl").exists()


def test_truncated_native_is_explicit_prefix(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"thread.started","thread_id":"session"}\n')
    events.with_suffix(".native.jsonl").write_text(
        '{"type":"session_meta","payload":{"id":"session"}}\n{"partial":'
    )
    script = tmp_path / "capture.mjs"
    script.write_text("""import fs from 'node:fs';
const p=JSON.parse(fs.readFileSync(0,'utf8'));
if(p.evidence!=='retained-native-prefix'||fs.readFileSync(p.transcript_path,'utf8').includes('partial')) process.exit(1);
console.log(JSON.stringify({outcome:'prefix retained',retryable:false}));
""")
    run(script, events, tmp_path, tmp_path / "vault")
    result = json.loads(events.with_suffix(".learning.json").read_text())
    assert result["outcome"] == "prefix retained"
    assert result["evidence"] == "retained-native-prefix"


def test_learning_atomic_outputs_replace_symlinks_without_writing_targets(tmp_path: Path) -> None:
    import os

    from factory.learning import _write, _write_text

    victim = tmp_path / "host-file"
    victim.write_text("preserve me")
    for name in ("events.native.jsonl", "events.learning.jsonl", "events.learning.json"):
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


def test_complete_native_refreshes_prefix_receipt_even_when_snapshot_is_unchanged(
    tmp_path: Path,
) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"type":"thread.started","thread_id":"session"}\n')
    native = events.with_suffix(".native.jsonl")
    complete = '{"type":"session_meta","payload":{"id":"session"}}\n'
    native.write_text(complete + '{"partial":')
    script = tmp_path / "capture.mjs"
    script.write_text('console.log(JSON.stringify({outcome:"fixture",retryable:false}))')
    run(script, events, tmp_path, tmp_path / "vault")
    native.write_text(complete)
    run(script, events, tmp_path, tmp_path / "vault")
    result = json.loads(events.with_suffix(".learning.json").read_text())
    assert result["evidence"] == "retained-native-transcript"
