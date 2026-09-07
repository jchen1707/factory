# Live sandbox compatibility — 2026-09-07

Both live disposable-project environments pass all six compatibility checks on
Codex CLI 0.146.0, worker SHA256
`f2b3751989c9810250554ec4b35dfcb9eec07b00d11fc60b0a6f81e9168b93be`.

| Check | Build | Reviewer |
| --- | --- | --- |
| Hook enforcement | Actual model tool receives protected-path refusal | Actual model tool receives protected-path refusal |
| Schema output | Canary and recovered worker return `{"ok":true}`, exit 0 | Same |
| Sandbox isolation | VM marker absent on host, removed afterward | Deterministic write fails with read-only filesystem error |
| Detached durability | Holder under PID 1 after 61.38 seconds, work still running | Holder under PID 1 after 60.96 seconds, work still running |
| Recovery | Intentional group interruption exits 143; same thread resumes; 10,591 new input tokens after baseline subtraction | Exit 143; same thread resumes; 10,179 new input tokens |
| Usage semantics | Fresh events: usage notifications, explicit compaction reset, Sol-to-Terra model change, thread resume | Same |

Both targets retain identical Git HEAD, status and diff before/after. All telemetry
experiments completed successfully. Latest-window input resets during explicit
compaction while cumulative input remains nonzero and monotonic. Worker recovery
usage matches resumed totals minus the retained interruption baseline.

Artifacts are under `artifacts/runtime-live-sandbox-validation/`. Each role directory
contains identity, isolation, interruption, usage results, `verified-summary.json`,
`protocol-evidence/` and `evidence-sha256.json`. The `compatibility/` directory contains
`factory-build-crud-live-20260907.json` and
`factory-review-crud-live-20260907.json`. Both pass production `validate_compatibility`.
Production configuration and build/review selection were also rehearsed in the
separate validation store; both selected `AppServerAdapter`. The rehearsal wrote no setting to the live store. James subsequently approved those
exact settings and they were applied; see `artifacts/runtime-live-app-server-selection/`.
`proposed-live-settings.json` retains the exact selected values.

James explicitly approved the project-only inherited credential acknowledgement.
Both authenticated GitHub API probes returned HTTP 401 at measurement. The registry
acknowledges a name, not an immutable credential or future proxy configuration; the
exception remains visible in preflight. No secret value was retained. This is not a
claim that the variable was removed. Read-only image/container inspection located
injection at sandbox creation rather than in the template's base environment.

Limits: reviewer mount refusal is proven by the deterministic sandbox command;
model self-report alone is not credited. Natural 80% or worker-triggered compaction
was not forced. Interruption proves worker-group exit and same-thread recovery, not
all descendant cleanup. Full workflow orchestration is separately validated; no
CRUD completion, FRO-12 advancement, activation or other-product work is claimed.

Repository gates: canonical `gate_report.mjs --force --json` reports PASS for Ruff
check, Ruff format, mypy and pytest, each exit 0 with empty output tails. Evidence:
`factory-gates-approved.json` under the artifact root. Mypy covers configured paths;
no source directory was added. Fixture tests prove control-plane logic and are not
the source of the live compatibility results above.
