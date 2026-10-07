# Codex-era build runtime incidents, September 2026

> **Codex-era history.** In September 2026 Factory launched Codex workers inside shared sbx build
> VMs. The operator commands this page names were `runtime-filesystem`, `runtime-capacity`,
> `build-runtime-replacement`, `fresh-build-runtime`, `runtime-retire`, terminal-history
> reconciliation, `project-register` and `project-unregister`. None of them reached `main`, and
> the parked code was discarded when Factory became Claude-only. Nothing on this page is a
> current procedure. Where a rule still applies to today's code, the section says so and cites
> the code.

The measurements come from the `effects` and `transitions` rows of the incident database,
read on 2026-10-07 with SQLite's `immutable=1` flag. The rules come from the parked operator
documents of that time. All times are UTC.

## BAC-60: the nemoclaw-dev build VM ran out of inodes

BAC-60 (run `33209a4871ac4216`) was blocked with `evidence-mismatch` on the shared build VM
`factory-build-nemoclaw-dev` (generation `988a54bd-51a4-4027-8307-c61285f2016d`). Inodes ran
out, not bytes.

| Time | Step | Measurement |
| --- | --- | --- |
| 2026-09-13 19:52 | Metadata diagnosis of the old VM | 1,310,720 inodes, 3 free. 20,957,446,144 bytes, 8,292,478,976 available. `/tmp`, `/tmp/pytest-of-agent`, the BAC-60 venv and `/home/agent/.cache` were all on that one root filesystem. |
| 2026-09-13 22:16 | Side-by-side 40g capacity candidate created with `DOCKER_SANDBOXES_ROOT_SIZE=40g` | Floors: 10 GiB available bytes and 1,000,000 available inodes. |
| 2026-09-13 22:24 | Candidate validated, never bound to a ticket | 41,956,900,864 bytes, 2,621,440 inodes (2,621,347 free). |
| 2026-09-14 00:33 | Replacement `factory-build-nemoclaw-dev-bac60-recovery` provisioned | Generation `dc0000c0-40fd-4cc7-9cfa-4b567d417c50`, same project mounts as the old VM. |
| 2026-09-14 00:36 | Replacement prepared with the frozen dependency install | 2,610,895 of 2,621,440 inodes free after the install. |
| 2026-09-14 00:39 | Replacement activated as BAC-60's run-only `build_sandbox` | The ticket was not resumed by activation. |
| 2026-09-14 01:05 | BAC-60 completed (`merge-is-james`) | |

The vault notes from the incident attribute most of the inode use to pytest temporary
directories and failed environments, not mainly to venvs. That attribution is an inference,
not a measurement. The old VM was started only for read-only diagnosis probes. It was never
repaired or deleted during recovery.

## BAC-68, BAC-69 and BAC-72: fresh runs on the replacement

At 02:36 and 02:37 on 2026-09-14, three existing, unclaimed runs were each authorized
separately to use the replacement VM. Each authorization bound only that run. No later ticket
inherited it.

| Ticket | Run | Outcome |
| --- | --- | --- |
| BAC-68 | `f397896e6cd44ff0` | Blocked with `review-finding` at 14:35, cancelled by James at 15:28. A second run, `5e64e3388944448b`, is still `approved` at attempt 0. |
| BAC-69 | `d96492fc1c4a4a35` | Blocked with `vault-write-outside-allowlist` at 03:50, cancelled by James at 15:46. |
| BAC-72 | `efbe15f1e35e4418` | Completed at 14:49 (`merge-is-james`). |

Today's code still sets no VM root size (no `ROOT_SIZE` appears in `src/factory/`).
nemoclaw-dev still names one shared `build_sandbox` (`config/projects.toml`).

## Preservation boundary for removing a build VM

Before a build VM is removed, establish its exact generation and every run that owns it. A
`using -` row from `runtimes` is not enough. For each owner, record what survives:

- **Git.** Record where the object database and branch refs live, and the exact commits. An
  exported branch is not a backup of a dirty index or working tree.
- **Dirty or untracked source.** A host bind mount survives VM removal. A clone or other
  VM-private filesystem does not.
- **Artifacts.** The host `.factory/` directory, clone-artifact mounts, Factory archives and
  the host state and authority tree survive. Removal does not bring back artifacts that
  cancellation or gc already removed.
- **Dependencies.** VM-local environments and caches are lost. Rebuild them only through the
  target repository's declared bootstrap.
- **Native sessions.** These are VM-local and are lost. In the Codex era this meant Codex's
  state database and rollout files. Today it means the Claude transcripts under
  `$HOME/.claude/projects/` in the VM (`src/factory/live_probe.py`), which `--resume` needs.
  Git, an artifact archive and a transcript export do not make a session restorable.

Approving the loss of VM-private data was a separate, explicit act. It was never a claim that a
backup existed, and it never overrode an ownership hold. The database records one retirement.
At 2026-09-11 03:14, generation `a9131687-7a02-410a-8593-105de0a9620d` of
`factory-build-nemoclaw-dev` was removed. It had 25 recorded owners, and James had abandoned the
five that were still blocked.

## Captured-authority acceptance

The parked preflight proved inside the VM that a run's captured authority was intact and
read-only. Main mounts captured authority read-only but does not run this proof. For each new
run, the parked check did seven things:

1. Validated every frozen host file and the manifest against the retained policy.
2. Bound the probe to the observed sandbox generation before and after it ran.
3. Read the whole captured tree in the VM, rejected extra files, missing files and symlinks,
   and compared each SHA-256 with the host's bytes, including `snapshot.json`.
4. Read `/proc/self/mountinfo`, chose the deepest effective mount for each file, and required
   its `ro` flag. Writable nested mounts and ambiguous evidence failed.
5. Opened each file only with `O_WRONLY | O_NOFOLLOW`, with no create, truncate or append. Only
   Linux `EROFS` passed. `EACCES` is not mount protection.
6. Rehashed each file in the VM and revalidated the host capture afterwards, including after a
   timeout.
7. Retained the receipt on the host and recorded `preflight:captured-authority`. A failed check
   blocked before any agent launch.

## Terminal-history reconciliation keeps unknown exits unknown

Some completed or cancelled runs had attempt rows that never recorded a closure. James could
approve abandoning that history. The rule was that an unknown historical exit stays unknown:

- Never read a reused worktree's exit marker as proof that an older attempt ended.
- The closure records `outcome=abandoned-unknown` and keeps `exit_code=NULL`.
- `ended_at` is the operator's closure time, not an inferred process exit.
- Session IDs and the original attempt rows are kept in an audited before-snapshot.
- Only rows with both `exit_code` and `outcome` absent are closed. Run state, transitions,
  effects, accounting, captured authority and artifacts are not rewritten.

## Unregister a project before deleting its checkout

Remove a project's registry entry before you delete its checkout, never after. This rule still
applies on main. `factory doctor` checks the vendored layer A of every registered project
(`_vendored_layer_a` in `src/factory/doctor.py`), so an entry that points at a deleted checkout
fails doctor and can block qualifying a new project. Do not weaken the vendor check or change
every project's defaults to hide a stale entry.

## BAC-56: launch evidence collision after cancel and fresh run

A fresh run of a cancelled ticket could refuse to launch with `launch evidence directory
already belongs to an invocation`. Runs of one ticket shared `<worktree>/.factory/run/<attempt>`,
so the new run's attempt directory still belonged to the cancelled run's launch. The parked fix
moved new evidence to `<worktree>/.factory/<run-id>/run/<attempt>`.

The recovery written for BAC-56 was:

1. Confirm that the failed run has only a prepared launch and no spawn effect.
2. Do not retry it or advance its attempt counter. Its frozen preparation still names the
   colliding path, so neither repairs ownership.
3. With the fix loaded in the controller, and with James's decision, cancel the failed run and
   start a fresh one:

   ```sh
   uv run factory cancel BAC-56 --reason "retire never-launched legacy evidence collision"
   uv run factory run BAC-56 --no-follow
   ```

4. Never edit a captured preparation or historical launch ownership to get past the guard.

The database does not record the error text, but it records the recovery. BAC-56 run
`7f369db1702e482e` was cancelled at 2026-09-10 23:37 after exhausting its attempts. The fresh
run `d9bfc661bc84451f` prepared a launch at `.factory/run/1`, the cancelled run's attempt-1
path, and never spawned. At 2026-09-11 00:36 James cancelled it with the reason above, and run
`711b64e9d799480b` spawned into `.factory/711b64e9d799480b/run/1`.

Main still raises the error (`src/factory/agent_launches.py`). For bind-mounted projects such
as nemoclaw-dev, main's evidence directory is still per worktree, not per run (`factory_dir_for`
in `src/factory/steps/__init__.py`). Clone projects already key it by run ID.
