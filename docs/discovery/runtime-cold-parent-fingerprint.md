# Cold-parent fingerprint change: runtime persists project trust

Measured 2026-09-08 with native Codex 0.153.4 in disposable `factory-build-*` sandboxes.
This diagnosis made no model turns, changed no live settings, and did not modify production
worker, observer or fingerprint code. All three owned diagnostic sandboxes were removed.

## Cause

The initial native app-server `thread/start` adds
`projects.<synthetic working directory>.trust_level = "trusted"` to the VM-local
`/home/agent/.codex/config.toml`. Initializing the app-server and requesting `model/list`
alone does not change the file. No `turn/start` is necessary to reproduce the write.

The fingerprint correctly includes that complete configuration file. The first canary
therefore changes its own environment, retiring its original certificate; the next job
observes the settled configuration. This is not an unstable mount or environment reading.

For capacity acceptance run 0, observed current spec hash was
`b36d6c19a053f531aaba89f75b76c80be6a0def6b771d01ca68a406fe73b9bac`, matching passed job
`e90695c8d2f3412b960cba93232628c2`. Replacing **only** its configuration inventory with
pristine VM configuration hashes reconstructs failed initial job
`1becc2fcc3494500b09bca044402bae0`'s exact spec hash:
`c51428202660bdc0398a118d2dc8f76de7d028cd6e97cc7ac4101e19edcb2391`.

The sole changed configuration file hash was:

- Before: `e2bf8447f09db7cf2814f18432f83030be58c497d299db877f9ff8b12cd00839`.
- After: `61c850f039ed2183d30a3e51ad872f76c2a978fe331d825eb2bddb523ddea67f`.

Sanitized TOML comparison retained names, types and value hashes rather than configuration
contents. Its only added scalar is the synthetic project's `trust_level`; the hash matches
JSON string `"trusted"`.

## Experiments and rejected fixes

Three separate fresh diagnostic VMs used private writable scratch and read-only mounts of
the synthetic source and authority. The capacity experiment's VMs were observed read-only.

1. Native app-server initialize/model list: no configuration change.
2. Native initialize/thread start, approval `never`, sandbox `danger-full-access`: project
   trust persisted, thread creation succeeded, every other observed component unchanged.
3. The same thread start with an invocation-local project trust configuration override:
   the runtime still persisted project trust.
4. The same thread start with a process-level `-c projects."<cwd>".trust_level="trusted"`
   override: the runtime still persisted project trust.

Neither override is a fix. No fingerprint field was removed or relaxed.

## Remaining implementation decision

A bounded zero-model runtime preparation before constructing the first fingerprint could
settle this runtime-owned change before paid certification. It would need durable ownership,
explicit VM-only scope, preserved configuration observations, no turn admission, and restart
coverage. It must never write the host's `~/.codex/config.toml`, mutate an unrelated human
sandbox, or treat a changed configuration as equivalent evidence. This is a proposed next
step, not implemented or validated production behavior.

## Evidence

Local artifact root: `artifacts/runtime-cold-fingerprint/`.

- `parent-components.json`, `reconstruction.json`: current and retained identities and exact
  reconstruction of the original failed fingerprint.
- `before.json`, `after-metadata.json`, `delta.json`: metadata-only negative control.
- `before-thread.json`, `after-thread.json`, `thread-delta.json`, `thread.stdout`: native
  thread-start reproduction without inference.
- `config-key-diff.json`: sanitized TOML key/value-hash comparison.
- `before-override.json`, `after-override.json`, `override-delta.json`: failed thread override.
- `before-cli.json`, `after-cli.json`, `cli-delta.json`: failed process override.
- `cleanup.json`: all three owned VMs absent; zero model turns.

The reusable scripts retain the exact experiments but reference the capacity fixture's
paths and configuration. Do not rerun them after that fixture changes or disappears without
preparing a new isolated fixture. An initial script invocation rejected an invalid read-only
primary mount before VM creation; a subsequent metadata invocation used the wrong script
entry point and exited before runtime initialization. Both were corrected before the retained
successful comparisons, and neither started a model turn.

## Implementation checkpoint

A separate `RuntimePreparation` controller and sealed zero-turn adapter worker now implement
this preparation. They were kept separate while capacity acceptance was active. After its VMs were removed,
workflow certification was connected and both modules were added to the implementation
fingerprint. Frozen builder, reviewer and child launches only revalidate their original
identity; they do not start another preparation.

The controller commits ownership before launching the native server, retains before/after
identity and configuration hashes, and creates no paid invocation. Confirmed receipts match
the complete fresh after-identity. Changed configuration requires another checked preparation;
explicit project distrust is refused before starting the server. Pending ownership refuses
blind retries even if the first thread already changed configuration before acknowledgement
was lost. That ambiguous state currently needs operator reconciliation; no automatic restart
of the uncertain preparation is implemented.

The worker permits only the runtime's measured addition of the current project's trusted
entry. Other configuration changes refuse; configuration contents and server diagnostics are
not transported. Only native `initialize` and `thread/start` are sent, never `turn/start`.
The launcher uses the existing sealed Codex/helper boundary and a private VM Codex home.

`warmup-result-final.json` measures the successful helper in another disposable generation:
initial spec `510d819574c1d4664dea6f8f0f5e55d11e287091c1882f3588545ad7e04346a3`, settled spec
`811ed239cccae4c5f53f5d070cf5e283163110cbd463ee03aaeb551eb5341e75`, one durable preparation
effect, no model invocations, and identical repeated fresh observations. The owned VM was
removed (`warmup-cleanup.json`). An earlier successful helper experiment's reporting script
used an incorrect Store method after its runtime assertions; its evidence is retained and a
fresh generation was used for the clean PASS, rather than describing that script as passing.

Five focused tests cover replay, changed generation/configuration, uncertain acknowledgement
with a changed spec, and independently connected controllers joining one preparation. Ruff and mypy pass for this checkpoint. The 31 focused preparation/certification/child cases and mypy pass after workflow wiring.
Real six-check workflow acceptance and review remain required before claiming this fixes
automatic certification end to end.

## Separate clone restart drift: internal Git forwarding

The first fresh writable clone passed all six checks on its initial fingerprint after
zero-turn preparation. Its later native-thread resume nevertheless produced a second,
independent identity change. Two complete read-only observations of the same VM and
generation differed only in `actual.configuration.ports`:
`127.0.0.1:49248->9418/tcp` became `127.0.0.1:49249->9418/tcp`.
Configuration files, environment, mounts, source authority, native helpers and requested
specification were identical. The sandbox had stopped and restarted; Docker assigns a
new host port to sbx's internal clone Git daemon on every start. The adapter's existing
`git_daemon_url` already documents and resolves this lifecycle behavior.

Offline reconstruction from the complete fresh observation reproduced every retained
parent fingerprint by replacing only that host port:

| Certificate | Host port | Retained spec digest |
| --- | --- | --- |
| `d4c79eb79b934a55af48b343386ed91e` | 49244 | `da281c10439b3024751c25edcbc168df66fd88ec186244433ca11960b5ff5ba6` |
| `111bc6b677ef458d8e0513dc960d95f2` | 49245 | `d4a3b56d62fa905f87140f2db05584a6c34e9c0bdb241a38f5e8597efc9029c2` |
| `ff9145ced2464d70a21fb3c5836b7e58` | 49246 | `c9745c6b295109676902e1b5de0b1308e6bc4de6c2bc2d3b1e616bc1f7c4c3b2` |
| `6952971869d34b1fb20d1d10b238cded` | 49247 | `e67e02b6b0e46e93cb1a47a8fc686cdf5df211ce705478bd3ebf150e61090506` |

The correction normalizes only the valid numeric host port of an exact
`127.0.0.1:<port>->9418/tcp` mapping, only when the requested specification enables
clone layout. Both observations use the same normalization. It preserves the number
and order of mappings, loopback interface, destination and protocol; other application
ports and unknown or malformed records remain fully fingerprinted. A non-clone layout
receives no port normalization. No configuration hash or capability field is ignored.

The observed pair failed the new regression before the correction; all 63 sandbox
adapter tests pass afterward, including exposed interfaces, changed destinations,
changed protocol, invalid numeric ports, non-clone layout and additional application
ports. Evidence is retained under `artifacts/runtime-cold-fingerprint/` as
`writable-parent-components-1.json`, `writable-parent-components.json`,
`writable-delta.json` and `writable-port-reconstruction.json`. These diagnostic reads
and offline reconstruction made no model calls. Final resumed workflow acceptance
remains the acceptance owner's responsibility; this report does not infer its result.
