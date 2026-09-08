# Runtime certification and delegation rollout

This is a reviewable deployment procedure, not an applied rollout. James owns the schema
5→6 approval, merges, deployment and activation. Earlier schema 4→5 and service-restart
approvals do not authorize this migration. The objective is factory capability acceptance;
synthetic workloads suffice. Do not finish test tickets, change Backend tickets, resume
parked FRO work, or involve `nemoclaw-dev` to establish acceptance.

## Release inputs and dependencies

| Owner | Reviewed source to publish/merge | Deployment dependency |
| --- | --- | --- |
| Shared `harness@v2` | `cada9f200` (includes upstream `f1511d8`) | Shared certification/delegation contracts and consumer generation |
| `python-harness@v2` | `f66c81b`, based on `9b1cc5b` | Exact shared pin and regenerated content |
| `frontend-harness@v2` | `8429ecf`, based on `188233e` | Exact shared pin and regenerated content |
| Factory | `c37b6e9` (final source; subsequent handoff update is documentation only) | Schema 6, controller, workers, and synchronized shared content |
| Sandbox package | [Package manifest](runtime-certification/package-manifest.json) | Full native Codex 0.153.4 package, matching helpers and protected mount anchor |

These are local preparation refs, not a claim they were pushed or merged. Publish shared
source first, then the exact consumer pins and factory change. Managed sync PRs may appear
after the shared merge; reconcile those with the prepared consumer branches rather than
racing their automation. If merging changes a required source SHA, regenerate and recheck
all pins against the final source. Never edit generated `main` or vendored content by hand.
The final factory commit and all acceptance outcomes must be fixed before deployment.

Shared `scripts/check.py`, Python/frontend applicable declared gates, pin integrity and all
three generation checks passed in the preparation session. Final combined factory gates also
pass (`factory-gates-complete.json`). Real writable/reviewer/lifecycle acceptance also passes; see
[the acceptance report](runtime-certification-completion-acceptance.md). This procedure remains
an unapplied proposal and does not authorize deployment.

## Read-only preflight

Use the candidate checkout for migration preview. Do not point new schema-6 application
commands at a schema-5 store before migration.

```sh
uv run --project /Users/james/factory-runtime-certification factory migrate --database /Users/james/factory/state/factory.db
launchctl print gui/501/com.jchen.factory
launchctl print gui/501/com.jchen.factory.console
plutil -lint /Users/james/factory/ops/com.jchen.factory.plist /Users/james/factory/ops/com.jchen.factory.console.plist
```

The measured host is James's `gui/501` domain; discover the correct domain if the operator
changes. Preview was executed read-only on 2026-09-08: schema 5, `quick_check=ok`, 72 runs
(9 blocked, 51 cancelled, 12 completed). Both launchd jobs were loaded; the writer timer
was idle and the console running. These are observations, not permission to assume the
same state later. Refresh the inventory immediately before approving deployment.

The preview must show only the three new tables: `runtime_certifications`, `agent_leases`
and `delegation_requests`, with existing rows preserved. Review a different source schema
separately. Record current Git revisions, registry and operator settings, service plist
paths, active runs/holders, and owned sandbox identities. Do not attach to `codex-*`.

## Approved maintenance window: stop writers and back up

Run this section only after James approves the concrete release and schema 5→6 migration.
Use the old live binary to drain active work, or explicitly Suspend the named runs before
changing code. Suspended children/threads/worktrees must be reconciled and preserved.
Stopping the timer alone does not stop detached agents or standalone CLI controllers.

```sh
launchctl bootout gui/501/com.jchen.factory
launchctl bootout gui/501/com.jchen.factory.console
lsof /Users/james/factory/state/factory.db /Users/james/factory/state/factory.db-wal /Users/james/factory/state/factory.db-shm
```

Confirm both services are unloaded and no other process is writing the store. Do not use a
broad kill command. Preserve active sandbox holders and source artifacts until their owned
lifecycle is settled. The console is stopped because its controls can write settings.

Take a consistent online backup with exclusive creation and restrictive permissions:

```sh
python3 - <<'PY'
import datetime, hashlib, json, os, sqlite3
from pathlib import Path
source = Path('/Users/james/factory/state/factory.db')
root = source.parent / 'backups' / ('runtime-schema6-' + datetime.datetime.now(datetime.UTC).strftime('%Y%m%dT%H%M%SZ'))
root.mkdir(parents=True, mode=0o700, exist_ok=False)
target = root / 'factory-schema5.db'
os.close(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
old = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
assert old.execute('PRAGMA user_version').fetchone()[0] == 5
copy = sqlite3.connect(target)
old.backup(copy)
assert copy.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
assert copy.execute('PRAGMA foreign_key_check').fetchall() == []
rows = {}
for (table,) in copy.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
    quoted = '"' + table.replace('"', '""') + '"'
    values = sorted(json.dumps(tuple(row), ensure_ascii=True, default=lambda x: {'bytes': x.hex()}) for row in copy.execute('SELECT * FROM ' + quoted))
    rows[table] = {'count': len(values), 'sha256': hashlib.sha256('\n'.join(values).encode()).hexdigest()}
(root / 'rows-before.json').write_text(json.dumps(rows, indent=2))
copy.close()
old.close()
(root / 'backup.sha256').write_text(hashlib.sha256(target.read_bytes()).hexdigest() + '\n')
print(root)
PY
```

Retain the printed backup directory privately. Also retain the current registry, settings
and service definitions in that directory. Do not copy an open SQLite file with `cp` and
call it a verified backup. Rehearse applying the same reviewed migration on a copy first;
compare every pre-existing table's count and digest with `rows-before.json`.

After the backup/rehearsal passes, deploy the approved factory commit to the live checkout
without overwriting dirty work, then apply the reviewed migration:

```sh
uv run --project /Users/james/factory factory migrate --database /Users/james/factory/state/factory.db --apply
```

Before any settings write or restart, verify schema 6, `quick_check=ok`, no foreign-key
violations, and identical row counts/digests for every pre-existing table. New tables may be
empty; this is expected. Save the migration output and verification result beside the backup.
Any mismatch stops rollout. Do not let the old schema-5 controller open the upgraded store.

## Runtime package and trusted source

The currently measured `codex-pnpm:v1` already contains the full matching 0.153.4 package.
Verify it rather than blindly upgrading it. A new generation still needs its own certificate.
The [manifest](runtime-certification/package-manifest.json) records the measured image and
native file hashes. A version string, npm launcher or standalone copied `codex` binary is
insufficient. The native `bin/codex`, sibling `bin/codex-code-mode-host`, and packaged
`codex-resources/bwrap` must all match. `/mnt` must be empty, root-owned and not writable by
the agent, with safe ancestors. Never repair that anchor inside an already certified VM.

If packaging differs, prepare a clean disposable `factory-build-*` packaging sandbox from
the approved base; install the entire pinned `@openai/codex@0.153.4` package and verify all
three files. Do not snapshot an acceptance VM containing project data, thread history or
account material. Review its exact image digest before selecting it. The supported template
publication commands, to be filled with the reviewed clean sandbox/tag, are:

```sh
sbx template save factory-build-runtime-package factory-codex-0.153.4:certification --output /Users/james/factory/state/runtime-package/codex-0.153.4.tar
sbx template load /Users/james/factory/state/runtime-package/codex-0.153.4.tar
```

These template writes have **not** been executed. Do not overwrite the current live tag.
No package rebuild is necessary when the existing full package and image match the manifest.
Fresh credential preflight and complete certification are still mandatory for every new
sandbox identity. Existing credential acknowledgements are project-specific; never copy an
acknowledgement or account token just to make a new sandbox pass.

After shared source is merged, export its exact reviewed commit to the controller-owned
contract directory outside every candidate-writable mount:

```sh
set -euo pipefail
mkdir -p /Users/james/factory/state/runtime-contracts
mkdir /Users/james/factory/state/runtime-contracts/cada9f200
git -C /Users/james/harness-runtime-certification archive cada9f200 plugins/harness | tar -x -C /Users/james/factory/state/runtime-contracts/cada9f200
mkdir -p /Users/james/factory/state/runtime-config
cp /Users/james/factory/docs/runtime-certification/factory-crud-certification.json /Users/james/factory/state/runtime-config/factory-crud-certification.json
```

Use a new, empty source directory and verify exported bytes against that commit before
selection. Update this path and the JSON proposal if the merged source SHA changes.
The proposal uses `packages/contracts/openapi.json`, which exists and is explicitly protected
in the current disposable verification target. Recheck that declaration against the target's
new immutable snapshot; a protected path from a different project does not transfer.

## Staged selection

The [settings proposal](runtime-certification/settings-proposal.json) targets only
`factory-crud-verification`. It preserves current run concurrency (the live explicit value
is 4), model routing/presets, delivery policy, existing run snapshots and all other projects.
The verified capacity ceiling proposal is 8 agents, 2 children per parent, depth 1. These
are distinct from run concurrency. Lowering a cap drains admitted work rather than killing it.

1. **Manual/default-disabled compatibility:** deploy/migrate with existing selections retained
   and delegation disabled. Missing fields remain manual/default-disabled. Do not reset or
   reinterpret existing run snapshots, reuse old identities, or launch an app-server run with
   a stale manual manifest. Keep unattended intake stopped until the selected next stage is ready.
2. **Automatic certification, delegation disabled:** apply the exact initial proposal in
   Approval mode. Each model probe and application invocation needs its own approval; zero-turn
   runtime preparation, observation and deterministic verification may continue.

```sh
FACTORY_HOME=/Users/james/factory uv run --project /Users/james/factory factory configure --project factory-crud-verification --agent-adapter app-server --certification-mode automatic --certification-config /Users/james/factory/state/runtime-config/factory-crud-certification.json --delegation-mode disabled --max-active-agents 8 --max-children-per-parent 2 --max-delegation-depth 1 --mode approval
```

3. **Read-only children:** after the new deployment identities pass all six checks and a
   synthetic launch, select read-only delegation. Existing runs do not gain missing mailbox
   mounts or tool registration simply because project settings changed; use a new run or the
   explicit preserved recovery path.

```sh
FACTORY_HOME=/Users/james/factory uv run --project /Users/james/factory factory configure --project factory-crud-verification --delegation-mode read-only
```

4. **Isolated writable children:** enable only after the final writable source/integration/
   independent-review acceptance is recorded for this release. Their results never substitute
   for gates or independent review.

```sh
FACTORY_HOME=/Users/james/factory uv run --project /Users/james/factory factory configure --project factory-crud-verification --delegation-mode isolated-write
```

Inspect the printed effective settings and retained runtime status after each operation. A
run may restrict project delegation/caps, never raise them. `--max-active-agents inherit`
and `--delegation-mode inherit` remove a run override. Approval is separate from Suspend;
parent approval does not approve a child. The exact invocation to approve appears in controls:

```sh
FACTORY_HOME=/Users/james/factory uv run --project /Users/james/factory factory configure --ticket SYNTHETIC-TICKET --approve-attempt 'certification:JOB-ID:PHASE'
```

Replace that example with the **observed** invocation ID. Do not invent an ID or approve an
entire subtree. Automatic mode is a separate later operator setting, not implicit in choosing
automatic certification.

## Observation and restarting services

Before enabling the timer's intake, one approved no-claim pass can reconcile existing work:

```sh
FACTORY_HOME=/Users/james/factory uv run --project /Users/james/factory factory tick --once --no-claim
```

This can advance existing runs; it is not a read-only preflight. Restart the console, then
the normal writer only after the maintenance/selection checks pass and James approves intake:

```sh
launchctl bootstrap gui/501 /Users/james/factory/ops/com.jchen.factory.console.plist
launchctl bootstrap gui/501 /Users/james/factory/ops/com.jchen.factory.plist
curl --fail http://127.0.0.1:7717/
```

Observe `/projects` and run controls for preparation effects, certification status/failures,
child parentage/status, active agent count, waiting invocation IDs and reasons. Queued child
and checking-certificate counts overlap; do not add them as distinct agents. Capture operator
interventions, repeated failures, completion outcomes and API-equivalent cost through the
existing metrics command and retained invocation evidence. Interrupted/compacted usage can
remain incomplete; preserve the known lower bound and missingness. Do not report those estimates
as Codex account charges or grant more budget by deleting failed usage.

Hold further model approvals when identity/evidence changes unexpectedly, ownership is
uncertain, source preservation refuses, pricing becomes unexpectedly incomplete, or a budget/
capacity boundary cannot be explained. An uncertain zero-turn preparation retains its effect;
never clear it or launch another native thread merely because its acknowledgement is missing.
Reconcile its exact VM/generation and evidence, or take an explicitly reviewed preserved
replacement path. Complete paid certification remains required afterward.

## Preservation limits and old clones

Source snapshots include actual VM commits, tracked files, staged changes and non-ignored
untracked source; ignored dependency environments are intentionally excluded. They require
matching inventories around export and refuse changing sources. The current transport is
bounded to 64 MiB of serialized output (including base64 bundle/files), at most 100,000 files,
and regular files plus canonical relative aliases whose targets remain in the exported tree.
The stacks' tracked `.claude` aliases are preserved without expansion. This is not a 64 MiB
raw-source allowance. Oversized history, external/missing/cyclic/noncanonical aliases,
submodules and unresolved merges refuse with preserved evidence. Writable artifacts may keep
unchanged baseline aliases but cannot add, edit or delete them. Do not delete or flatten an
unsupported layout to satisfy certification.

Writable children use private source/dependency/scratch areas and declared disjoint scopes.
Integration preserves dirty parent work, refuses stale/conflicting source, and retains durable
application evidence. Existing clones without mailbox mounts cannot be silently recreated.
Drain or Suspend their owned work, retain VM-local commits, dirty/untracked source, dependency
state and native thread history, then review an explicit preservation/migration plan. If the
bounded snapshot cannot represent that source, stop and preserve the VM; a stale host checkout
is never a substitute. Native thread continuation requires the original owned VM/generation;
a Git snapshot alone is not a transferable Codex thread.

## Rollback

Stop new admissions first: disable delegation and switch to Approval mode at the project,
then inspect run overrides and explicitly restrict those that remain Automatic. This preserves
active work; use targeted Suspend only when current execution must stop. Stop the writer if
controller behavior is suspect, and retain all owned source, mailbox, thread and cost artifacts.

Do not turn automatic certification into manual mode as a bypass for a failed certificate.
A new manual app-server launch still needs valid identity-specific evidence; otherwise keep
admissions stopped or explicitly select the preserved legacy adapter for **new** runs only.
Do not change the adapter/thread underneath an existing run.

Keep schema 6 and its new history. Do not restore the old whole database over newer runs or
run the old schema-5-only binary against it. Prefer a corrected schema-6 controller or leave
writers stopped. An old template may be selected for future identities only after its own
compatibility checks; never replace or remove a VM with unpreserved work to accomplish rollback.
