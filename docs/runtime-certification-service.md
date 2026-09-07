# Automatic sandbox certification

Implementation is opt-in. Production rollout and complete workflow/child acceptance are
pending; see runtime-certification-implementation-handoff.md for measured scope and remaining work.
The native helper fix passed all six checks in both disposable build and reviewer sandboxes.
Certificates from previous workers and other sandbox identities remain unusable for this build.
Full workflow/child acceptance and production rollout checks remain unfinished; see the handoff.

`factory configure --project PROJECT --agent-adapter app-server --certification-mode automatic
--certification-config /absolute/host/config.json` selects automatic certification for new runs.
Absent settings retain manual compatibility. Existing runs retain their selected mode and config
path. Configuration must be outside candidate-writable mounts. This command is not a deployment
or migration action.

The host JSON has exactly these string fields:

```json
{
  "binary": "/absolute/path/to/native/codex",
  "source": "/trusted/layer-A/workflows/runtime-certification/probes.json",
  "model": "gpt-5.6-sol",
  "alternate_model": "gpt-5.6-terra",
  "effort": "low",
  "usage_scope": "thread",
  "canary_path": "existing-protected-file"
}
```

Use the measured binary, model support and counter scope for the intended runtime. This example
does not certify them. The source contract belongs to harness@v2 and must arrive through its normal
merged-source distribution. The disposable acceptance uses an explicit unmerged source checkout;
no consumer freshness or production rollout is implied.

Automatic application selection requires an immutable authority snapshot containing runtime hook
wiring. Older snapshots without `.codex/hooks.json` refuse automatic certification; replace policy
through the existing explicit operator action and reverify. The requested sandbox specification is
built by the existing builder/reviewer spec functions. Fresh observations bind generation, image,
native bytes, actual mounts/environment/configuration, requested layout, trusted authority/hooks,
probe contract/parameters and execution-helper revisions. Liveness fields such as uptime do not
change identity. Missing or mismatched observations refuse launch.

Each controller tick requests/reuses one fingerprinted job and advances at most one new paid phase.
The nine source-declared phases cover canary/schema, interruption/durability, recovery and usage
with compaction/model change. Every phase has its own invocation, approval, agent slot and accounting.
Approval prompts use `certification:JOB:PHASE`. Deterministic observation and terminal accounting
continue while an approval is pending. Certification and application execution exclude one another
atomically in the same VM, while independent VMs can progress concurrently.

Worker/request bytes and sealed prompt/schema inputs are retained in argv; the process-group wrapper
does not execute a candidate-writable body file. Raw runtime stdout is captured directly into the
host certification directory. The evaluator checks runtime notifications and target state before
fenced publication. A model result or report file alone cannot authorize an application launch.
Queued application preparation reselects and validates immediately before paid admission.

Controller restart observes existing paid launch intent; expiry of a coordination lease never grants
permission to repeat a probe. Unknown holders retain capacity. Ended stale jobs are accounted before
a changed fingerprint can proceed. Failed jobs remain failed; do not remove evidence or change a
fingerprint merely to retry. A timeout or unexpected target mutation preserves its evidence.
Compaction and unproven usage attribution remain visibly incomplete in API-equivalent estimates.

The service records its status in `runtime_certifications`, its selected build/review job IDs in run
settings, and per-phase invocation/approval/accounting evidence in the existing runtime store. No
separate unattended application or migration is needed: workflow selection/driver ticks resume it.
The schema 5→6 live migration, shared merge/sync and activation remain separately owned rollout work.

Certified native launch additionally fingerprints the packaged `codex-resources/bwrap` resource
beside the runtime's `bin` directory. Both executables are hash-checked into sealed snapshots.
The launcher copies them from offset zero into regular files on an invocation-private tmpfs and
remounts that filesystem read-only. Regular files provide reopenable native helper paths; executing
a memfd or bind-mounted anonymous file directly does not. The existing VM `/dev` is preserved for
PTY allocation, and Codex still enforces the requested build/reviewer sandbox policy. No new session
or parent-death kill flag is introduced; the existing detached process-group owner remains in charge.
The mountpoint is the image's empty, root-owned `/mnt` directory. Both preflight and launch refuse
root execution, symlink or writable ancestors, nonempty anchors and overlap with the candidate cwd.
The immutable resource directory is first in runtime PATH, preventing helper shadowing. The existing
image directory is never created, renamed or changed; its executable mount exists only inside the
launch namespace. An image/layout without this protected empty anchor cannot use automatic
certification until its template/layout is explicitly prepared. Missing packaged resources, changed
hashes and old certificates without launcher provenance refuse launch. Standalone binary-only
upgrades are insufficient for this launcher; retain and certify the matching package resources.
