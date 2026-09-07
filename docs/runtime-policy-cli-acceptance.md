# Offline operator policy replacement acceptance

Measured 2026-09-07 UTC. The actual production CLI successfully replaced a paused
scratch run's Prototype policy with Hardening, without any tracker, model or sandbox
operation. All **14 deterministic acceptance checks passed**. This closes the successful
CLI gap left explicit in [the authority experiment](runtime-authority-acceptance.md).

## Dependency defect and correction

The existing CLI and console replacement paths called the general execution-context
builder. That builder fetches the current Linear issue, so a local policy replacement
unnecessarily depended on tracker availability and credentials. `authority.snapshot`
uses only the host home, registered project, stored run and store; it never uses Issue,
routing or an executing adapter.

Six regressions (CLI and console, each for Suspended, Blocked and Awaiting Human)
failed before the correction at the exact attempted tracker read. A narrow frozen
`authority.SnapshotContext` now carries only those four local inputs. Both replacement
callers resolve the project through the existing registry and `project_for_run`, then
use that context. The CLI no longer loads routing for replacement. General execution
context loading remains unchanged; no missing ticket metadata is fabricated.

Paused-state checks, leases, explicit replacement, policy interpretation, integrity,
revisioning and stale-evidence refusal remain in their existing paths. Six new
regressions passed afterward; the combined offline-policy, console and runtime-workflow
suite passed all 55 tests. Independent review then caught that the narrow path had
also omitted existing local `load_harness_config` validation. Six additional cases
failed when malformed source declared neither gates nor apps. Both callers now retain
that validation; all 12 focused cases pass, and the actual CLI experiment was repeated
successfully against the corrected source. The 55-test broader report predates this
final validation-preservation correction. The final four Definition-of-Done gates pass for the combined branch at `ec36c41`;
`artifacts/runtime-accounting-policy-gates/factory-gates.json` retains the output.
The policy correction is committed as `8f4ccbb`.

## Actual CLI experiment

`artifacts/runtime-policy-cli-acceptance/acceptance.py` created a fresh local Git
repository and schema-5 SQLite store. Its fixture declares root/child requirements,
a visible Prototype engineering deferral with rationale and revisit condition, and
Core/Hardening profiles. It constructs the initial paused run and revision-1
verify/review authority request and pass records through production store/authority
functions. These initial records are fixtures, not claims that gates or reviews ran.

The experiment then removed its scratch `config/models.toml` and invoked the installed
production executable as an ordinary subprocess:

```text
factory configure --ticket AUT-1 --replace-policy hardening
```

`FACTORY_HOME` pointed exclusively at this experiment's scratch home. The command
returned 0, emitted the complete revision-2 Hardening snapshot, retained no deferrals,
and recorded exactly one `policy-replaced` operator event. There were no monkeypatches,
fake adapters or injected CLI handlers in this acceptance subprocess.

The surrounding production-function checks established:

- Candidate source weakening does not replace an existing immutable snapshot.
- Ordinary profile settings cannot implicitly replace governing authority.
- Conflicting child requirements and child deferral of a parent-required requirement
  fail through the real Node interpreter without advancing the policy revision.
- After the actual CLI replacement, both old verify/review pass records are refused;
  old request markers also cannot be relabeled as current evidence.
- Fresh revision-2 requests and evidence satisfy authority checks. This proves
  provenance validation, not gate or model execution.
- Published snapshot tampering is refused. Exact restoration restores integrity.
- A separate active scratch run's replacement is refused by the actual CLI before
  any tracker context can be constructed.

No live factory home, CRUD database, existing product, ticket, VM or credential was
accessed. The separate console regressions cover its HTTP route with a rejecting
tracker seam; the actual unmocked acceptance measurement above is the CLI, not a
browser-driven console experiment.

## Evidence and source identity

Factory HEAD during measurement was `9a08440eb907b760db3039b6fd0cb865213e7849` plus
the local correction. `source.patch`, exact source/test copies and `sha256.json` bind
the measured working code; the report does not attribute the correction to that HEAD.

Retained under `artifacts/runtime-policy-cli-acceptance/`:

- `preflight.json`: original tracker dependency diagnosis before edits.
- `regression-red.txt`, `regression-green.txt`, `focused-tests.txt`, and
  `validation-regression-red.txt` / `validation-regression-green.txt`.
- `acceptance.py`, `inputs.json`, the real fixture repository and scratch home/store.
- Both published snapshots and revision-1/revision-2 request evidence.
- `paused-cli-replacement.json`, `active-cli-refusal.json`: actual exit/stdout/stderr.
- `results.json`: all 14 checks, operator audit, authority check rows and snapshots.
- `source/`, `source.patch`, `sha256.json`.

Results SHA-256: `39d1fc686a24f64514e2a4af49029cc77048760f4777e549278700779b220ef8`.

This establishes local operator replacement and authority invalidation. It does not
establish reviewer adherence to every profile, real gate execution under replacement,
or complete delivery behavior. No production activation or merge occurred.

The earlier successful CLI experiment before validation preservation remains under
`before-validation-preservation/`; the top-level results bind the corrected final code.
