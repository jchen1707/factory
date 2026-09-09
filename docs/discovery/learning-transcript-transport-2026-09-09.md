# Native transcript transport audit — September 9, 2026

Status: implementation gap confirmed by code inspection; sandbox transport remains
unmeasured. This supplements the [completion handoff](../handoff-ui-learning-docs.md)
and preserves the limits in the [earlier runtime measurements](learning-repair-2026-09-08.md).
No sandbox was created, accessed, stopped or removed for this audit. No model ran and no
credentials or private session contents were inspected.

## What the controller currently retains

- `agent/codex.py` preserves the runtime session (deliberately omits `--ephemeral`) but
  redirects only the public JSON event stream and stderr into the attempt directory.
- `agent/app_server_worker.py` extracts only `thread.id` from the start/resume response.
  Its reduced stream does not preserve the complete native conversation.
- `sandbox/sbx.py:collect` reads the terminal file and returns existing host artifact
  paths. It performs no native session discovery or export.
- `learning.py:evidence` retains parseable JSON object lines and discovers the session
  from `thread.started`. `learning_worker.py` accurately calls that evidence
  `retained-events-partial`; neither path reads a native rollout.
- Cancellation reconciles owned agents, archives the attempt directory, schedules
  archived event files, releases worktree/clone state and then stops the sandbox.
  GC likewise archives existing attempt files before removing a worktree. Neither
  archive operation discovers files in the sandbox runtime home.

Thus retaining every event line is insufficient to prove native transcript retention.
A sandbox-local rollout surviving a process exit is also insufficient to prove that
it survives sandbox removal or is reachable by the registered host learning hook.

## Installed interface measurement

The installed host binary reports `codex-cli 0.153.4`. This command generated local
protocol metadata without starting a server or model:

```sh
codex app-server generate-json-schema --out /tmp/factory-transcript-schema-20260909
```

Observed in the generated schema:

| Schema field | Exact contract |
| --- | --- |
| `ThreadStartResponse.definitions.Thread.properties.path` | Nullable string; description: `[UNSTABLE] Path to the thread on disk.` |
| `ThreadStartResponse.definitions.Thread.properties.ephemeral` | Boolean describing whether the thread should not be materialized on disk |
| `ThreadStartParams.properties.ephemeral` | Nullable boolean |
| `ThreadReadParams.properties.includeTurns` | Boolean; full-history hydration is deprecated for paginated threads in favor of `thread/turns/list` and `thread/items/list` |

These are installed **host** interface facts, not a certificate for any sandbox version.
The nullable unstable path must not become an unconditional requirement that prevents
ordinary execution. A `thread/read` call alone is not a full-retention solution: it
requires a live server, may need pagination, and cannot run after that server receives
SIGKILL. A shutdown-only copy has the same interruption boundary.

## Concrete remaining measurements before changing transport

Once a clean owned disposable sandbox is available, measure both installed runtime
paths with a temporary vault and fixture project:

1. Observe the native rollout path and file availability after thread creation, clean
   completion and SIGKILL. Establish a bounded, session-specific discovery method for
   legacy exec, and whether app-server start/resume supplies a usable path.
2. Measure an argv-based export through the sandbox adapter into the existing host
   attempt artifact directory for both bind-mounted and clone projects. Check complete
   user/tool content against the source; do not infer equivalence from file existence.
3. Measure export before cancellation/teardown and after recoverable interruption.
   Abrupt sandbox removal before any export must remain an explicit unavailable outcome
   unless continuous retention is actually implemented and tested.
4. Feed only the retained data to the registered host repository's layer-A hook; prove
   one note and both indexes, replay, and later recall after the sandbox is removed.

Factory owns discovery/export ordering and retained evidence. Shared transcript parsing,
learning distillation, indexing and recall remain in harness. Any retained rollout must
use the existing artifact secret-scan boundary before archiving and must never cause
candidate-provided scripts to execute on the host. Preserve partial-event fallback and
explicit absence outcomes when full evidence is unavailable.

No production code was changed in this audit. A guessed rollout filename, an unbounded
scan of every runtime session, or a worker-only finally block would not close the
required clean/interrupted/clone/removed-sandbox matrix. Changes to the certified
app-server worker also require fresh runtime certification, so that worker remains
unchanged pending these measurements.

Validation: generated the installed schema and checked the fields above; inspected the
listed lifecycle paths; local document links and `git diff --check` passed. Factory code
gates were not rerun for this documentation-only audit.
