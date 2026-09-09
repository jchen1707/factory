# Factory learning handoff: isolated acceptance, 2026-09-08

This record covers the host lifecycle seam. It is not evidence that an interactive hook
or a sandbox runtime fired successfully; layer A's own measurements cover those paths.

## Reproduction and cause

Before the repair, `uv run pytest tests/integration/test_learning.py -q` failed with
`completed factory attempt has no observable learning handoff`. The test drives the
existing fake-sandbox pipeline through implementation collection and asserts a capture
outcome beside the retained events. GC's separate `distil_backlog.mjs` invocation scans
interactive host transcripts; it does not hand these retained factory streams to layer A.

Completed implementation, planning and review-axis collection now schedule a detached host
worker. Central owned-launch reconciliation covers diagnosis, test-design, child and
suspension paths; cancellation schedules from archived event copies after terminal proof. Reaping an orphaned or timed-out attempt schedules its retained invocation streams.
No sandbox is started for capture, no capability is added to an agent, and the workflow does
not wait for distillation. The worker invokes the registered host repository's vendored
`session_learnings.mjs --json`, never a candidate worktree's script. The registry vault path
is passed as `OBSIDIAN_VAULT_DIRECTORY`; other operator capture settings are inherited.
Both supported factory adapters are Codex, so the payload declares `runtime: codex`.
Layer A chooses the matching distiller by default; the operator can override it with
`LEARNINGS_DISTILLER`. Backend selection remains entirely in layer A.

The hook receives the registry repository as `cwd`. Layer A derives canonical project
identity from its Git origin, keeping factory registry aliases out of note identities.
The runtime thread ID is preserved as the session ID.

## Evidence and recovery

Beside each collected event stream, `*.learning.json` records the capture outcome and
`*.learning.jsonl` retains complete JSON lines from that collection. The receipt labels
this `retained-events-partial`: legacy exec streams include assistant/tool events;
app-server currently emits assistant text and telemetry, without the complete user/tool
conversation. Neither stream is represented as a full interactive transcript.

The worker serializes concurrent processing with a file lock. An unchanged snapshot with
a finished, non-retryable layer-A result is skipped. A failure or a killed worker can be
retried by recollecting the attempt. A killed worker leaves `started` with `finished: false`
after acquiring its lock. Missing script/transcript/session identity have explicit
`unavailable` outcomes; malformed layer-A output and process failures have explicit
`failed` outcomes. These receipts do not alter the run's state verdict.

The isolated tests run a real Node subprocess with a fixture capture script. They verify
payload/session preservation, duplicate suppression, observable failure followed by
successful recollection, and preserving complete lines after a truncated final record.
Pipeline tests cover completed collection and orphan reaping against fake adapters.
No historical backlog was recovered or rewritten during this repair.

## Limits

A host reboot can kill the detached capture worker; detached execution is not a durable
job queue. Recollection retries it, but there is no independent capture-job poller.
Old attempts that are never recollected remain recoverable gaps. Existing GC behavior is unchanged. Full sandbox transcript
export, native hook execution and model-backed distillation require separate runtime
measurements; fake-adapter pipeline tests do not establish those properties.

## Cross-layer transport measurement

The real `factory.learning_worker` invoked the repaired layer-A CLI twice in a temporary
HOME and vault, with a fixture `claude` binary. The worker's cwd was the factory repair
worktree. The receipt reported `wrote 2026-09-09 factory factoryi.md`, `retryable: false`,
and `retained-events-partial`; the temporary vault held one note and its project index.
This proves transport, canonical worktree identity, writing, indexing and replay suppression
across the actual Python/Node seam. It does not prove a model or native runtime hook fired.

## Factory gates

`node .agents/vendor/harness/hooks/gate_report.mjs --force --json` returned exit 0:

```json
{"ruff check":"pass","ruff format --check":"pass","mypy":"pass","pytest":"pass","verdict":"pass"}
```

Every gate ran with exit 0; pytest took 174.658 seconds. The new Python modules live under
mypy's configured `src` tree. The pytest caveat still applies: it proves factory logic,
not real sandbox, GitHub or Linear behavior. The cross-layer measurement above and the
separate runtime acceptance record distinguish those evidence levels.
