# Monorepo red-phase replay acceptance

Status: **PASS**, measured in `factory-build-crud-20260907` on 2026-09-07 UTC.
The sandbox was stopped after the check; no model invocation or tracker write occurred.

The delivered `gate_report.mjs --force --json` ran both declared candidate gates with
frozen authority. `api acceptance` and `web acceptance` passed, exit 0, with one test each;
no gate was skipped and neither declared a caveat. Production replay then ran both test
patches against base `ece7eb835d0e3d6166f607999a30f70a265f6009`:

- API: exit 1, `AssertionError: 'old' != 'new'`, one test executed.
- Web: exit 1, `ERR_ASSERTION`, actual `old`, expected `new`, one test executed.
- Aggregate: `pass`, reason `app-replays`; replay returned `proceed`.
- Frozen authority integrity and scratch cleanup both passed.

Candidate revision: `aae9cf729e9b1dcbee812f3798febfc35cc98f50`.
Dedicated fixture run: `cb8693cf44d1473c`, authority revision 1.

This deterministic check exercises production `redphase.replay`, frozen authority resolution,
and `clone.scratch_add` with a disposable two-app repository. It creates no tickets, makes
no model calls, and uses a separate factory database. The existing FRO-12 clone, policy,
and run are outside the fixture.

The API app declares a Python unittest gate; the web app declares a Node test gate and a
Git-magic test selector. Each candidate test asserts new behavior that the base implementation
does not provide. The web test also imports a synthetic helper module written only into the private VM
clone's child `node_modules`, so a successful assertion replay requires the production
nested dependency link. This helper is a fixture, not evidence that real product dependencies
were installed. No gate command or test path is guessed by the factory.

The script first runs both declared candidate gates, then calls production replay against
the recorded base. It requires two actual assertion failures, an aggregate pass, intact
snapshotted authority, and removal of the scratch worktrees. This measures replay and
clone dependency availability; it is not an end-to-end CRUD workflow, browser acceptance,
or proof that historical app configs already declare test selectors.

Evidence is retained under `artifacts/runtime-monorepo-replay-acceptance/`:

- `run.py`: preparation and real acceptance driver.
- `fixture.json`: exact base and candidate revisions.
- `sandbox-commands.jsonl`: real sandbox argv, working directories, exits, and output.
- `candidate-gates.json`: the complete delivered runner report for both passing candidate gates.
- `report.json`: candidate outcomes, replay checks, snapshot identity, and cleanup.
- `isolated-home/`: dedicated SQLite state and frozen authority.

The host fixture and private VM clone use `/tmp/factory-monorepo-replay-acceptance-20260908`.
The Git bundle is transferred through the existing writable protocol mount, in its separate
`monorepo-replay-acceptance` subdirectory. No sandbox is created or reconfigured for this check.
