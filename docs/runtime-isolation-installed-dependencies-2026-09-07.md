# Real installed-dependency isolation follow-up

Measured 2026-09-07 UTC in disposable scratch repositories and four fresh stock
sandboxes. Both bind and clone layouts passed. This closes the earlier fixture's
specific dependency-installation gap: the original probe injected a tiny module
into site-packages; this probe used the actual `uv pip install` installer to install
`idna==3.10` and `idna==3.11` independently, then exercised their IDNA encoder and
checked installed distribution versions repeatedly while both workers ran.

The preceding [isolation measurement](runtime-isolation-followup-2026-09-06.md)
already established simultaneous resources and admission transactions. Its admission
race was not repeated here. This follow-up repeats resources and cancellation only
to establish that real dependency installations remain independent through those
operations. It does not replace a product-specific acceptance measurement.

## Measured effects

| Observation | Bind | Clone |
| --- | --- | --- |
| Two detached workers simultaneously report running | Pass | Pass |
| Actual installed dependency versions remain 3.10 and 3.11 at identical VM venv path | Pass | Pass |
| Each version imports and encodes a non-ASCII domain correctly | Pass | Pass |
| Same temporary-file and SQLite paths contain only each worker's token | Pass | Pass |
| Both hold 127.0.0.1:38471 simultaneously | Pass | Pass |
| Other worker's protocol mount is absent | Pass | Pass |
| Targeted cancellation returns 143 while survivor advances | Pass | Pass |
| New attempt in the original VM retains database state and reclaims port | Pass | Pass |

The virtualenv path was `/home/agent/venvs/isolation-installed-dependencies` in
every VM. The survivor advanced from tick 4 to 7 to 11 across the bind run's
simultaneous, cancellation and recovery observations; the clone survivor advanced
from 3 to 7 to 10. Recovered workers retained their original SQLite row and added a
second row with the same token. Survivor databases stayed unchanged. Installed
versions stayed distinct throughout.

Bind workers wrote separate fixture subdirectories under a shared project mount;
both marker files reached the host. Clone workers used the same primary repository
path independently: neither saw a host file created after cloning, and neither
workspace marker reached the host. Protocol evidence remained host-visible.

The probe used production `SbxAdapter`, `create_argv`, `exec_detached`, `poll`,
`kill_group`, and `detached_shell_script`. Each VM had 2 CPUs/2 GB, no shared skills,
and denied GitHub/Linear destinations including `mcp.linear.app`. Inspection found
no forbidden capability secrets. No model call was made.

## Evidence and limits

Retained under `artifacts/runtime-isolation-installed-dependencies/`:

- `acceptance.py`: executable probe, adapted from the earlier retained probe.
- `observations.jsonl`: creation argv, safe inspection metadata, worker states,
  targeted cancellation, recovery, and final stopped-state observations.
- `bind/protocol/` and `clone/protocol/`: exact worker/wrapper, package installer
  output, readiness/progress, heartbeats and original/recovery terminal records.
- `verify_evidence.py`: assertions across retained resource and terminal observations.
- `verified-summary.json`, `sha256.json`, and `run.log`.

Observations SHA-256: `7e4179a7644fe684443a7a7c0dabea5640254ee64ab3bc456c1450c5857e657c`.
Artifact index SHA-256: `d657024ccf83a9e3fd8b6f3141127d60e8c71de718d66964b48e3c97fb9be56e`.

This measures a real small dependency installation, not the CRUD application's
complete Python/frontend dependency graph, native dependencies, or template/kits.
The fixture bind layout provides resource separation, not confidentiality between
worktrees under its shared project mount. The workers were adapter-level fixtures,
not complete factory state-machine runs: controller scheduling, per-run identity
selection, Git synchronization/stale-base handling, review, and `factory resume`
remain separate acceptance obligations. Recovery here is a fresh detached attempt
following targeted cancellation, not a host crash or controller recovery.

No production manifest was generated or concurrency setting changed. Existing
products/tickets, CRUD run sandboxes, the production database and registry, and
`codex-*` sessions were untouched. No source file was changed.

All fresh sandboxes are stopped, confirmed by inspection:

- `factory-build-isolation-deps-bind-83339-0`
- `factory-build-isolation-deps-bind-83339-1`
- `factory-build-isolation-deps-clone-83339-0`
- `factory-build-isolation-deps-clone-83339-1`
