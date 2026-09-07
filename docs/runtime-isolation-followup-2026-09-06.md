# Real bind and clone isolation acceptance

Measured 2026-09-06 local time (2026-09-07 UTC). Both layouts passed the scoped
measurement below. No production settings, tickets, or database were changed.

Evidence is retained in `artifacts/runtime-isolation-followup/` (ignored operational
artifacts). `acceptance.py` contains the complete probe; `observations.jsonl` records
creation argv, safe inspection metadata, environment **names only**, worker results,
poll observations, cancellation, recovery, and cleanup. Each attempt retains its
wrapper, worker, heartbeat, terminal record, stderr, and observations. `sha256.json`
binds the retained artifacts to their digests.

- Observations SHA-256: `e3dbed8727c9052410a393cb73edc8acb8c18d757a34eaa6854f3696329232f1`
- Artifact index SHA-256: `400ad8f98cebf978365e38d322c0ff362186f221d33d7dca1b604f835604df65`

## What ran

The probe used the real `SbxAdapter`, `create_argv`, `exec_detached`,
`detached_shell_script`, `poll`, and `kill_group`. Each layout used two fresh stock
VMs, each with 2 CPUs and 2 GB memory; skill sharing was disabled and GitHub/Linear
network destinations denied. Only the documented `mcpgateway` secret appeared in
inspection. `policy.capability_secrets()` returned an empty list for every sandbox.

| Measurement | Bind | Clone |
| --- | --- | --- |
| Two real detached workers simultaneously report running | Pass | Pass |
| Separate virtualenvs at the identical in-VM path | Pass | Pass |
| Different importable dependency values remain unchanged while both run | Pass | Pass |
| Same `/tmp` filename retains each worker's own value | Pass | Pass |
| Same SQLite database path contains only that VM's token | Pass | Pass |
| Both workers bind and hold `127.0.0.1:38471` simultaneously | Pass | Pass |
| Other worker's separately mounted protocol path is absent | Pass | Pass |
| Targeted process-group cancellation produces exit 143 | Pass | Pass |
| Survivor progresses through cancellation and recovery | Pass | Pass |
| Fresh attempt recovers in the original sandbox identity | Pass | Pass |

The dependency measurement created actual virtualenvs with `uv venv` and put a tiny
importable fixture module into each environment's site-packages. It proves mutable
environment isolation; it does not claim a product dependency installation succeeded.
The virtualenv path was `/home/agent/venvs/isolation-acceptance` in both VMs.

Bind workspaces were separate fixture subdirectories under a shared mounted project
root. Both workspace marker files appeared on the host in their respective paths.
This is resource separation, not a security assertion that one run cannot read the
other worktree through the shared project mount. The protocol mounts were siblings
outside that project root, and each VM could not see the other's protocol path.

Clone VMs used the same primary host repository path with `--clone`, and separately
mounted per-run protocol paths. Neither saw a host file created after cloning, and
neither workspace marker reached the host. Protocol progress and exit records did
reach the host. The primary repositories were tiny scratch Git repositories, not
product worktrees.

For both layouts, the survivor advanced from tick 4 to tick 7 during cancellation
and to tick 10 while the cancelled worker restarted and acquired the same port in
its original VM. The recovered worker's SQLite database retained its first token
and added another copy; the survivor's database still contained only its own token.
Original exit records were retained and recovery used a new attempt directory.

Separately, eight host processes with separate connections raced the real
`Store.runtime.admit()` against a new scratch schema-5 SQLite store and a limit of
two. Exactly two admissions succeeded. This did not access the live store.

## Scope and final state

No implementation defect was observed in these measurements. They exercise the
production sandbox adapter and admission transaction against real VMs and SQLite.
They do not exercise a complete ticket lifecycle, app-server, reviewer permissions,
host-crash recovery, daemon scheduling, Git synchronization, or actual product
dependency installation. Recovery here means a fresh detached attempt after a
targeted cancellation, not `factory resume` or `recovery.py` end to end.

No compatibility or production isolation pass manifest was generated. These results
must not be relabeled as measurements of a production project's specific sandbox,
template, kit, or mounts.

All four sandboxes were stopped, and final `sbx inspect` confirmed `state: stopped`:

- `factory-build-isolation-bind-75776-0`
- `factory-build-isolation-bind-75776-1`
- `factory-build-isolation-clone-75776-0`
- `factory-build-isolation-clone-75776-1`

`verified-summary.json` records that final inspection. Sandbox identities and disk
evidence are retained for examination; no other sandbox was touched.
