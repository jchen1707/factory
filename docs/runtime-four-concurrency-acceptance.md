# Four-run capacity and model observations

Measured 2026-09-07 after factory #87 merged (`bc6773a`). James authorized model
and concurrency tests using existing FRO issues, then explicitly requested four.
The disposable project's live settings are now revision 2: concurrency 4, per-run
isolation, existing model routing and app-server retained. No other project changed.

## Measurements

Four fresh `factory-build-crud-four-*` sandboxes used production clone specifications
with explicit 2-CPU/2-GiB fixture resource limits. Each passed production preflight,
including the previously approved project credential acknowledgement. All reported
Codex CLI 0.146.0. The host has 16 GiB and 10 logical CPUs; observed memory-pressure
free percentages were 46% before model work and 32% during it. These samples do not
establish sustained build throughput or the host's absolute maximum concurrency.

Four simultaneous deterministic processes each installed and executed a distinct
`idna` version (3.7, 3.8, 3.9, 3.10), bound port 38479, and wrote the same private
temporary, SQLite and clone paths with different run tokens. Every process read only
its token. The clone marker was absent on the host. Killing one process group produced
exit 143 while all three siblings' counters advanced with their data intact.
This evidence supports the project-scoped isolation manifest consumed by the supported
`factory configure --concurrency 4 --isolation per-run` command.

The production launch guard admitted four model attempts in an isolated validation
store. A fifth admission was refused at limit 4. Passing limit 2 refused new admission
without removing existing slots; the live limit was not lowered by that check.

The shipped app-server worker performed bounded acceptance-design tasks against frozen
FRO-13/14/15 requirements. All four turns overlapped for **12.685 seconds**, measured
from raw `turn/started` and `turn/completed` notifications. An explicitly requested
45-second delay was denied by the environment hook; no delay or successful execution
of that command is credited. Terra stopped repository inspection after that refusal
and produced requirement-only scenarios. Other outputs identified absent Notes code
and unresolved contract/test dependencies. These outputs are observations, not proof
of product correctness or a model-quality ranking.

| Model / effort | Exit | Input / cached / output tokens | API-equivalent estimated USD |
| --- | --- | --- | --- |
| Sol / high | 0 | 49,179 / 32,128 / 2,352 | 0.12809520 |
| Terra / medium | 0 | 21,108 / 0 / 670 | 0.05025600 |
| Luna / medium | 0 | 47,828 / 30,976 / 1,344 | 0.00560272 |
| Sol / xhigh | 0 | 60,125 / 43,904 / 3,631 | 0.15506560 |

Every output met the requested scenario schema. Production accounting collection
retained partial estimates during execution, then complete estimates for all four
finished invocations: **$0.33901952 total**, not Codex account charges. Replaying each
final event stream three additional times preserved exactly one cost row and unchanged
amounts. No normalized runtime-observation errors were reported. Cached and uncached
usage both occurred; this short sample did not exercise long-context pricing or
interrupted-model accounting. Metadata probes refused Astra/high and Astra/xhigh in
all four actual sandbox catalogues; no fallback or model execution was claimed.

## Limits and next operational dependency

These were worker probes with production admission/accounting functions and an
isolated fixture store, not four factory ticket lifecycles. Explicit experiment
metadata records that no production adapter compatibility report was attached.
No Linear writes, readiness changes, ticket implementation or FRO-12 resume occurred.
FRO-13/14 still depend on FRO-12; FRO-15 depends on both. The probe approvals and
parking transitions are fixture interventions, not a delivery-outcome cohort.

**Per-run app-server launch still requires exact-identity compatibility evidence.**
The live compatibility directory contains the two earlier shared-sandbox manifests.
New per-run sandbox names cannot use those manifests. Provision each approved actual
run's build/review identities and validate all six checks before launching through
production selection. Do not rename the old manifests or treat these narrower
capacity probes as compatibility passes. Four is configured and capacity-tested;
four complete live workflows are not yet demonstrated. The 2-CPU/2-GiB fixture limits
are not newly introduced defaults for future production sandbox specifications.

Initial fixture setup attempted unsupported Store state updates, which were refused
before any model launch. The corrected script used the legal suspended-to-planning
transition. Final sandbox inspection initially looked for a nonexistent top-level
status field; `sbx ls --json` independently confirmed all four sandboxes stopped.
These were experiment setup/reporting errors, not factory source changes. Each private
clone was clean after removing the operator's own isolation marker. Full logs and
model outputs remain retained.

Evidence: `artifacts/runtime-four-concurrency/` contains frozen issue JSON, exact
specifications, preflight, installed dependency logs, before/after isolation records,
digested isolation manifest, slot-admission record, live setting read-back, raw
observation snapshots, `verified-summary.json`, and `stopped.json`. Per-run mounted
protocol directories recorded in `identity.json` retain requests and raw streams.

Repository verification: canonical `gate_report.mjs --force --json` reported PASS.
`ruff check`, `ruff format --check`, `mypy`, and `pytest` each exited 0 with empty output
tails (66 ms, 41 ms, 261 ms, 121,931 ms). Mypy only covers its configured source paths;
the artifact probe scripts are not newly type-checked production code. Pytest uses
fake sandboxes; the separate measurements above establish the stated runtime effects.
No production source was changed.
