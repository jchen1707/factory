# Controller concurrency and draining acceptance

Measured 2026-09-07 UTC with a new scratch schema-5 database. The production
admission transaction and launch guard admitted two runs, retained both after the
limit was lowered to one, queued the third with `project-busy`, retained slots and
identities across a separate-process restart, and admitted the third only after
both earlier slots drained. No tracker, model, sandbox or production database was
accessed. No implementation defect was observed in this bounded measurement.

## What actually executed

The script used the real `Store`, `RuntimeState.admit`, `execution.guard`,
`operator_controls.configure`, `isolation.prepare`, `isolation.project_for_run`,
and `driver.step`. Contexts contain the real Linear, Codex and sbx adapters; no fake
adapter was used for claim. A Python audit hook refused network calls and any
subprocess except the explicitly scoped restart reader. No forbidden external call
was attempted, and the effects ledger remained empty throughout.

`driver.step` was exercised only on the third, saturated `approved` run: the real
claim entry action refused admission before reaching isolation preparation or
Linear. It returned `WAITING`, reason `project-busy`, with detail naming the occupied
project slot, and retained the driver's lease. Successful tracker claim was not
executed.

| Observation | Result |
| --- | --- |
| Two production launch-guard calls acquire two distinct slots at limit 2 | Pass |
| Lowering the configured limit to 1 preserves both slots and run states | Pass |
| Previously admitted runs still pass their next launch guard while draining | Pass |
| Third run is queued through the real driver with no launch ordinal or effect | Pass |
| Separate Python process reopens the store and reads identical slots/identities | Pass |
| Terminal transition for one run reclaims only that slot on the next admission | Pass |
| Remaining run keeps its identity; third is still queued at the lowered limit | Pass |
| Third acquires the single slot after the second terminal transition | Pass |

Two synthetic dirty-work marker files remained byte-for-byte unchanged across the
control operations. These markers were host fixtures, not Git worktrees; their
preservation does not establish full worktree/sandbox cleanup behavior.

## Constructed admission input and exact limits

`fixture-isolation.json` is a **constructed controller-test input**, not an
activation manifest or a fresh sandbox validation. It binds the authentic earlier
installed-dependency probe's raw observations, using its exact
`isolation-acceptance-bind` project name and original bind-fixture repository path.
That lets the production configuration/selection code execute without inventing
resource observations. The newly derived `factory-build-controller-fixture-*` and
reviewer names are unprovisioned metadata only. No evidence here asserts those new
identities, altered mounts, or a product project have passed real isolation checks.

Runs were inserted in `approved` as deterministic controller fixtures. A successful
`execution.guard` call records permission/admission; this probe did not subsequently
launch the agent. Targeted release used legal `approved -> cancelled` transitions
through `Store.record_transition`, not the cancel CLI or process signaling. Physical
targeted cancellation/recovery was measured separately in
[the real VM isolation report](runtime-isolation-installed-dependencies-2026-09-07.md).

The restart measurement reopened the real SQLite database in a separate Python
process and resolved saved identities. It did not restart a daemon with active
models, invoke `factory resume`, synchronize Git branches, test stale integration
bases, or complete a ticket lifecycle. Those remain separate acceptance obligations.
Neither this deterministic test nor the earlier VM fixture authorizes raising a
live project's concurrency.

## Evidence

`artifacts/runtime-controller-acceptance/` retains the executable `acceptance.py`,
scratch `controller.db`, local registry, constructed manifest and copied raw resource
evidence, `observations.json`, `restart.stdout.json`, dirty-work markers, and process
output. `source.json` binds the exact production modules used; `sha256.json` binds
all retained artifacts. The script completed with exit 0 and its final assertion
reported PASS. No factory source file or existing project was changed.
