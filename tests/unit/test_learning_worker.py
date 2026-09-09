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
