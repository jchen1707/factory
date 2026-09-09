# Learning runtime measurement — 2026-09-08

Measured on the host at approximately 03:13–03:16 UTC on 2026-09-09 (23:13–23:16 EDT on September 8) using installed `codex-cli 0.153.4`, `Claude Code 2.1.259`, and the factory worktree's actual `src/factory/agent/app_server_worker.py`.

These are isolated host measurements, not sandbox certification. They establish hook execution and transcript availability on the installed host versions. No factory sandbox was created or accessed, no credential was copied into a sandbox, no trust-store configuration was changed, and no real vault/backlog was processed. Temporary repositories registered two fixture hooks that append their input JSON and whether `transcript_path` exists to a temporary log. Each probe used a temporary vault; `CLAUDE_LEARNINGS_OFF=1` prevented incidental capture. Authentication remained in its existing host location. Runtime-owned fixture transcripts remain in the normal host session directories.

| Path | Actual result | SessionStart | SessionEnd | Transcript |
| --- | --- | --- | --- | --- |
| Codex `exec --json` | Real model completion, exit 0 | Fired | Fired, reason `other` | Existed at both callbacks and after process exit |
| Factory app-server worker | Real model completion, exit 0 | Fired | Fired, reason `other` | Existed at both callbacks and after process exit |
| Claude `-p --output-format json` | Exit 1; `Failed to authenticate: OAuth session expired and could not be refreshed` | Fired | Fired, reason `other` | Absent at start, present at end and after exit |
| Codex exec killed after start callback | SIGKILL process group, exit -9 | Fired | Did not fire | Rollout survived; no normal completion claim |
| Factory app-server killed after start callback | SIGKILL process group, exit -9 | Fired | Did not fire | Rollout survived; no normal completion claim |

Codex probe command: `codex exec --ignore-user-config --ignore-rules --skip-git-repo-check --dangerously-bypass-hook-trust --enable hooks -c 'hooks=<temporary fixture handlers>' -C <temporary repo> --sandbox read-only --json '<fixed text-only probe>'`.

Claude probe command: `claude -p --settings <temporary repo>/.claude/settings.json --setting-sources project --tools '' --model sonnet --output-format json '<fixed text-only probe>'`. No authentication repair or credential rotation was attempted.

App-server command: `python3 src/factory/agent/app_server_worker.py <temporary request.json>`. Request used the temporary directory and vault, a one-field output schema, read-only mode, and `gpt-6-astra`/`low`. The repository adapter performed its own hook discovery, inline fallback and invocation-local hook trust. This measures the real adapter lifecycle rather than a mocked transport.

An explicit call to the repaired layer-A `session_learnings.mjs --json`, with the actual successful Codex session's end payload and a temporary vault, returned exit 0 and `{"target":"","outcome":"failed: claude exited 1"}`. Thus transcript availability does not establish capture success: the actual downstream Claude call currently fails. The direct Claude probe identifies expired OAuth as an authentication blocker, while the capture outcome itself records only exit 1. No fake model was used to claim successful real distillation.

The fixture script did not conduct a human interactive terminal conversation. Consequently interactive Codex/Claude UI session shutdown, sandbox transcript export and sandbox runtime versions remain unmeasured by this exercise. A successful known-lesson note and later recall require separate evidence; host hook execution cannot stand in for them.

## Local artifacts

The reproducible probe scripts are `docs/discovery/artifacts/learning-repair/factory-learning-probe.py.txt`, `docs/discovery/artifacts/learning-repair/factory-learning-claude-probe.py.txt`, `docs/discovery/artifacts/learning-repair/factory-learning-appserver-probe.py.txt`, `docs/discovery/artifacts/learning-repair/factory-learning-interruption-probe.py.txt`, and `docs/discovery/artifacts/learning-repair/factory-learning-appserver-interruption-probe.py.txt`. These scripts contain no credentials.

Temporary artifact directories under `/var/folders/1f/039kvb3j5vb1n0nr66j5xw6c0000gn/T/`:

- `factory-learning-runtime-kkpz1g83`: Codex success, hook payloads, capture result.
- `factory-learning-runtime-s9frjjfd`: Claude authentication failure and hook payloads.
- `factory-learning-runtime-kjmqr6r6`: actual factory app-server success.
- `factory-learning-runtime-q7r6oeku`: interrupted exec.
- `factory-learning-runtime-z4db2ffi`: interrupted app-server.

The payload logger records transcript existence directly rather than inferring it from a supplied path. The current factory GC code invokes the host's vendored `distil_backlog.mjs --run`; these measurements do not demonstrate that this host sweep can see transcripts left only inside a terminated sandbox.

## Repaired recall: actual model context delivery

A second pair of real host measurements used the repaired `learning_recall.mjs` behind fixture logging wrappers registered for **SessionStart and UserPromptSubmit**. Both Codex exec and the actual factory app-server adapter exited 0. SessionStart returned `project_index`; UserPromptSubmit received the real prompt and returned `recalled` with the selected path. With tools prohibited by the prompt, both model responses reproduced the unique startup token `STARTUP_RECALL_OTTER_29`, the separate note-body token `RUNTIME_RECALL_ZEBRA_83`, and `Project Learnings/lesson.md` exactly. Neither token was present in the task prompt. This observes consumption of hook context, rather than merely hook registration or successful script execution.

Those note/index contents were explicit fixtures, not model-distilled learnings. Artifact directories are `factory-learning-runtime-7s8af3jd` (exec) and `factory-learning-runtime-2i46vwqv` (app-server); `docs/discovery/artifacts/learning-repair/factory-learning-recall-probe.py.txt` reproduces the setup. `recall-log.jsonl` records input events, selected sources and hook output.

## Explicit Codex distiller: real capture and another worktree

Using `LEARNINGS_DISTILLER=codex CODEX_LEARNINGS_MODEL=gpt-6-astra`, the repaired `session_learnings.mjs --json` processed an isolated synthetic transcript describing a queue-delivery ordering mistake and regression `zebra-reconcile-83`. The distiller made actual Codex model calls; no substituted model executable was used. The source scenario is a fixture, not a production incident.

First capture returned `wrote 2026-09-09 fixture-project fixturer.md`, `retryable:false`, exit 0. Reprocessing returned `rewrote` for the same exact path, `retryable:false`, exit 0. Exactly one lesson note remained, alongside `_hook.log`, `Project Learnings/_INDEX.md`, and `_VAULT_INDEX.md`. The model-written note preserved the ordering lesson and regression identifier. This verifies capture, indexing and identity deduplication using a real available backend using explicit backend selection at that measurement point. The final implementation defaults to the originating runtime, as rechecked below; no credential repair was attempted.

An actual second Git worktree was created from this isolated fixture repository with origin `https://example.invalid/acme/fixture-project.git`. Running recall there with query `queue delivery recovery` returned `status:recalled`, project `fixture-project`, and the written note including `zebra-reconcile-83`. Thus project identity survived a real worktree path change.

A narrower query `zebra-reconcile-83 rollback` returned `no_relevant_learnings`: those words occurred only in the note body or source transcript, not the model-produced summary. This is a measured index-first limitation. The hook's documented deeper-search fallback matters; a negative index result does not prove the vault has no applicable note.

Artifacts: `factory-learning-runtime-vub8l9an`; reproducer `docs/discovery/artifacts/learning-repair/factory-learning-distill-probe.py.txt`. The note date matches September 9 UTC. The initial rollout records `2026-09-09T03:13:43.068Z`, corresponding to September 8 at 23:13 EDT; the filename uses the local hour, whereas JSON timestamps carry UTC.

Finally a new real Codex session started in that second worktree with the repaired SessionStart/UserPromptSubmit hooks, asking about queue delivery recovery without supplying the regression identifier or lesson. It exited 0 and responded with `zebra-reconcile-83`, the correct persist-before-call/reconcile-after-restart fix, and the exact captured note path. `worktree-model.jsonl` stores that response; `artifacts/learning-repair/capture-results.json` preserves the model result (its original one-off local script depended on temporary paths). This joins the real capture and later real-session recall proofs rather than relying solely on a CLI search result.

## Claude registration shape — hypothesis refuted

Three actual Claude 2.1.259 runs compared `command:"node", args:["<absolute logger.mjs>"]`, `command:"node \"<absolute logger.mjs>\""`, and the consumer form `command:"node", args:["${CLAUDE_PROJECT_DIR}/logger.mjs"]`. **All three executed both SessionStart and SessionEnd callbacks**, independently of the downstream expired-auth exit 1. Thus neither a separate `args` list nor `${CLAUDE_PROJECT_DIR}` expansion explains missing capture on this installed version. Changing registration format on that assumption is unsupported by this measurement.

Reproducers `docs/discovery/artifacts/learning-repair/factory-learning-claude-args.py.txt` and `docs/discovery/artifacts/learning-repair/factory-learning-claude-args-env.py.txt`; artifact suffixes `034fyim3` (absolute args), `iwj7rpry` (command string), `higxvmfj` (environment-expanded args), with the same temporary directory prefix above. The fixture logger records actual callback payloads in `registered.jsonl`.


## Native Codex default recheck

With payload `runtime:"codex"` and `LEARNINGS_DISTILLER` absent, an additional real capture completed at exit 0, wrote a fresh fixture note, and returned `retryable:false`. This validates the repaired native runtime selection without an explicit backend override. Both indexes exist in the fresh temporary vault. Set `PROBE_NATIVE_DEFAULT=1` with the archived distillation probe to repeat this case.

Durable sanitized callback results and UTC timestamps are in `docs/discovery/artifacts/learning-repair/observed-results.json`. The artifact README explains reproducing these isolated measurements. Original temporary directories are diagnostic provenance only, not required dependencies of archived probes.

## Reproduced capture faults and repair

`node --test plugins/harness/hooks/learning_repair.test.mjs` in layer A failed before repair:
missing transcripts returned `skipped: transcript under 500 chars`, and first capture into
an empty temporary vault wrote no note because `Project Learnings` did not exist. The
same fixture exposed worktree-directory identity and pre-write whole-vault indexing
faults. The repaired path creates the directory, reports unavailable inputs explicitly,
resolves the project using Git origin/common-directory identity, and publishes both
indexes after the note. Notes and indexes are replaced atomically. A shared session lock
prevents concurrent native and host captures; partial factory evidence preserves an
existing session note. Dead local workers can be retried; foreign or missing lock owners
require inspection before manual recovery. These are observable recoverable gaps, not a
durable background queue.

The configured real vault was inspected read-only: it existed, held 16 notes and both
indexes, and had no `_hook.log`. This is an observation, not proof that hooks never fired:
configuration and log-write failures can produce the same absence. The unset shell
`OBSIDIAN_VAULT_DIRECTORY` is expected under `p0-13-vault.md`; shell and agent configuration
are separate scopes. No existing notes were rewritten and no historical backlog was run.

## Sandbox boundary measurement

Three disposable owned `factory-build-learning-probe-*` sandboxes were created and
removed. The first stopped before model execution when an environment-name-only check
found a GitHub credential variable. Two follow-up creations removed sensitive names from
the host create environment and used both `codex-pnpm:v1` and the default
`docker/sandbox-templates:codex-docker`. `sbx inspect` listed only the declared gateway
secret; `policy.capability_secrets()` returned an empty set. Nevertheless an in-VM boolean
check reported a nonempty, non-placeholder GitHub credential environment in both images.
No credential value was returned. Scoped `sbx secret rm github --sandbox <owned-name>
--force` reported no service binding and did not remove it.

The source remains undetermined. This does not reopen the settled historical binding on
`factory-build-python-harness`: the measurements use different new names and establish a
new environment discrepancy. No Codex/app-server process was launched in these VMs; no
transcript export was claimed. All owned probe sandboxes were removed. Resolving image or
daemon credential provisioning requires separate investigation while preserving the
credential boundary. Sanitized results are in
[the sandbox boundary artifact](artifacts/learning-repair/sandbox-boundary.json).

## Validation references

Layer A: 158 shared tests, `scripts/check.py --since=origin/v2`, and Prettier passed.
The Python, frontend and Go consumer branches each passed their declared applicable gates
and vendored integrity checks. Upstream freshness waits for the layer-A branch to land
on `harness@v2`; consumers are prepared at its feature SHA, not misreported as published.
Factory lifecycle transport and its separate gate evidence are in
[the lifecycle acceptance record](../acceptance/learning-lifecycle-2026-09-08.md).
[Reproducible host probes](artifacts/learning-repair/README.md) distinguish fixture inputs
from actual model calls. Human interactive UI shutdown and sandbox model execution remain
unverified; successful host paths must not be presented as those proofs.
